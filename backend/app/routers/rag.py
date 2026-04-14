"""RAG clinical Q&A endpoint.

POST /rag/query
    Body: { "question": "...", "patient_id": "uuid" (optional) }
    Returns: { "answer": "...", "sources": [{namespace, document_title, section}] }

Pipeline:
  1. Enrich query (specialty + patient context if patient_id provided)
  2. Hybrid search across all 5 namespaces
     - NS4 (patient_history) only queried when patient_id is supplied
  3. Cross-encoder reranking → top-5
  4. Build RAG prompt from system prompt + chunks
  5. Stream Claude response, accumulate full answer
  6. Return answer + deduplicated source citations
"""
from __future__ import annotations

import logging
import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.models.medecin import Medecin
from app.models.patient import Patient
from app.schemas.auth import CurrentUser
from app.security.jwt import get_current_user

log = logging.getLogger(__name__)

router = APIRouter(prefix="/rag", tags=["rag"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class RAGQueryRequest(BaseModel):
    """Clinical question to answer using the knowledge base."""

    question: str = Field(..., min_length=3, max_length=2000)
    patient_id: Optional[uuid.UUID] = Field(
        None,
        description="If provided, NS4 patient history is included in search",
    )
    top_k: int = Field(5, ge=1, le=20, description="Max chunks to include in context")


class RAGSource(BaseModel):
    """Citation returned alongside the answer."""

    namespace: str   # ccam | has | vidal | patient_history | doctor_corpus
    document_title: str
    section: Optional[str] = None
    chunk_index: int


class RAGQueryResponse(BaseModel):
    """Answer from Claude with source citations."""

    answer: str
    sources: list[RAGSource]
    chunks_used: int


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.post(
    "/query",
    summary="Clinical Q&A via RAG",
    description=(
        "Enrich query → hybrid search (dense+BM25) → rerank → Claude response. "
        "NS4 patient history is included only when patient_id is provided. "
        "Returns answer + source citations."
    ),
)
async def rag_query(
    body: RAGQueryRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
) -> RAGQueryResponse:
    """Answer a clinical question using the RAG pipeline."""
    settings = get_settings()

    # ── 1. Load medecin + optional patient context ─────────────────────────────
    medecin_result = await db.execute(
        select(Medecin).where(Medecin.id == current_user.medecin_id)
    )
    medecin = medecin_result.scalar_one_or_none()
    specialty = medecin.specialite if medecin else ""

    patient = None
    active_drugs: list[str] = []
    dfg: Optional[float] = None

    if body.patient_id:
        patient = await db.get(Patient, body.patient_id)
        if patient is None:
            raise HTTPException(status_code=404, detail="Patient not found")
        if patient.cabinet_id != current_user.cabinet_id:
            raise HTTPException(status_code=403, detail="Forbidden")

        # Decrypt active drugs for query enrichment
        if patient.traitements_actifs_encrypted:
            try:
                import json
                from app.security.encryption import decrypt
                active_drugs = json.loads(
                    decrypt(patient.traitements_actifs_encrypted, body.patient_id)
                )
            except Exception:
                pass

        dfg = patient.dfg

    # ── 2. Enrich query ────────────────────────────────────────────────────────
    from ia.rag.retriever.query_enricher import enrich_query
    from ia.rag.retriever.hybrid_search import (
        hybrid_search,
        GLOBAL_NAMESPACES,
        PATIENT_NAMESPACE,
    )

    enriched = enrich_query(
        body.question,
        specialty=specialty,
        active_drugs=active_drugs,
        dfg=dfg,
    )

    # ── 3. Hybrid search ───────────────────────────────────────────────────────
    namespaces = list(GLOBAL_NAMESPACES)
    if body.patient_id:
        namespaces.append(PATIENT_NAMESPACE)

    chunks = await hybrid_search(
        db=db,
        query=enriched,
        namespaces=namespaces,
        top_k=body.top_k,
        cabinet_id=current_user.cabinet_id,
        patient_id=body.patient_id,
    )

    if not chunks:
        return RAGQueryResponse(
            answer=(
                "Aucun document pertinent trouvé dans votre base de connaissances."
            ),
            sources=[],
            chunks_used=0,
        )

    # ── 4. Build RAG prompt ────────────────────────────────────────────────────
    from ia.prompts.rag_system import RAG_SYSTEM_PROMPT

    chunks_text = _format_chunks_for_prompt(chunks)
    prompt = RAG_SYSTEM_PROMPT.format(
        chunks=chunks_text,
        question=body.question,
    )

    # ── 5. Call Claude (non-streaming for RAG — accumulate full answer) ────────
    answer = await _call_claude(prompt, settings)

    # ── 6. Build source citations ──────────────────────────────────────────────
    sources = _extract_sources(chunks)

    return RAGQueryResponse(
        answer=answer,
        sources=sources,
        chunks_used=len(chunks),
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _format_chunks_for_prompt(chunks: list) -> str:
    """Format retrieved chunks as numbered context blocks for the Claude prompt."""
    parts: list[str] = []
    for i, chunk in enumerate(chunks, start=1):
        ns = getattr(chunk, "namespace", getattr(chunk, "source", "unknown"))
        title = _chunk_title(chunk)
        parts.append(f"[{i}] [{ns.upper()}] {title}\n{chunk.text}")
    return "\n\n---\n\n".join(parts)


def _chunk_title(chunk) -> str:
    """Extract a display title from chunk metadata."""
    meta = getattr(chunk, "chunk_metadata", None) or getattr(chunk, "metadata", {}) or {}
    return (
        meta.get("titre")
        or meta.get("code")
        or meta.get("document_id", "")[:8]
        or "Document"
    )


def _extract_sources(chunks: list) -> list[RAGSource]:
    """Deduplicate and format chunk citations."""
    seen: set[tuple[str, str]] = set()
    sources: list[RAGSource] = []

    for chunk in chunks:
        ns = getattr(chunk, "namespace", getattr(chunk, "source", "unknown"))
        title = _chunk_title(chunk)
        key = (ns, title)

        if key in seen:
            continue
        seen.add(key)

        meta = getattr(chunk, "chunk_metadata", None) or {}
        sources.append(RAGSource(
            namespace=ns,
            document_title=title,
            section=meta.get("section") or meta.get("pathologie"),
            chunk_index=getattr(chunk, "chunk_index", 0),
        ))

    return sources


async def _call_claude(prompt: str, settings) -> str:
    """Call Claude claude-sonnet-4-6 and return the accumulated text response."""
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    parts: list[str] = []
    async with client.messages.stream(
        model=settings.ANTHROPIC_MODEL,
        max_tokens=1000,
        temperature=0.15,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        async for text in stream.text_stream:
            parts.append(text)

    return "".join(parts)
