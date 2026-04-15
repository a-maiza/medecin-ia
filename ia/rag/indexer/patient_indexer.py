"""Patient history indexer — NS4.

Indexes the transcript and validated SOAP of a consultation into the `chunk`
table (namespace='patient_history'). All text is AES-256-GCM encrypted before
being stored. Embeddings are computed on-premise (never via an external API).

Security invariants:
    - Text encrypted with encrypt(plaintext, patient_id) before upsert.
    - Embedding computed from plaintext, THEN plaintext discarded.
    - PatientVectorStore is used for the upsert to ensure patient/cabinet filters
      are always present in the chunk metadata.
    - No patient text is ever sent to an external embedding service.

Usage:
    from ia.rag.indexer.patient_indexer import PatientIndexer

    indexer = PatientIndexer(db)
    chunk_ids = await indexer.index_consultation(
        consultation_id=uuid4,
        transcript_plaintext="Le patient présente...",
        soap_dict={"soap": {...}, ...},
        patient_id=uuid4,
        cabinet_id=uuid4,
    )
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

_CHUNK_CHARS = 2048    # ~512 tokens
_OVERLAP_CHARS = 256   # ~64 tokens


class PatientIndexer:
    """Async indexer for NS4 (patient_history) chunks.

    Args:
        db: AsyncSession with RLS context already set for this cabinet.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def index_consultation(
        self,
        *,
        consultation_id: uuid.UUID,
        transcript_plaintext: str,
        soap_dict: dict,
        patient_id: uuid.UUID,
        cabinet_id: uuid.UUID,
    ) -> list[uuid.UUID]:
        """Index transcript + SOAP sections for a validated consultation.

        Steps:
            1. Build text units: transcript chunks + individual SOAP sections.
            2. Embed all texts on-premise (CamemBERT-bio).
            3. Encrypt each text with the patient-specific AES key.
            4. Upsert via PatientVectorStore (enforces patient/cabinet filters).

        Returns:
            List of chunk UUIDs upserted.
        """
        # Build text units from transcript and SOAP
        texts = _build_text_units(transcript_plaintext, soap_dict)
        if not texts:
            log.info("[PatientIndexer] No text to index for consultation %s", consultation_id)
            return []

        log.info(
            "[PatientIndexer] Indexing %d units for consultation %s patient %s",
            len(texts), consultation_id, patient_id,
        )

        # Embed on-premise (plaintext)
        embeddings = await _embed_async(texts)

        # Encrypt + upsert
        from ia.rag.retriever.patient_store import PatientVectorStore
        from app.security.encryption import encrypt

        store = PatientVectorStore(self._db, cabinet_id=cabinet_id, patient_id=patient_id)
        chunk_ids: list[uuid.UUID] = []

        for text, embedding in zip(texts, embeddings):
            chunk_id = uuid.uuid4()
            encrypted = encrypt(text, patient_id).to_db()

            await store.upsert_chunk(
                chunk_id=chunk_id,
                content=encrypted,
                embedding=embedding,
                metadata={
                    "consultation_id": str(consultation_id),
                    "plaintext_len": len(text),  # for debugging; not the text itself
                },
            )
            chunk_ids.append(chunk_id)

        log.info(
            "[PatientIndexer] Upserted %d chunks for consultation %s",
            len(chunk_ids), consultation_id,
        )
        return chunk_ids


# ── Text unit builder ─────────────────────────────────────────────────────────

def _build_text_units(transcript: str, soap_dict: dict) -> list[str]:
    """Build a flat list of text strings to embed and index.

    Sources:
        - Transcript: split into overlapping windows of ~512 tokens.
        - SOAP sections: each S/O/A/P section as a separate unit.
    """
    units: list[str] = []

    # Transcript chunks
    if transcript and transcript.strip():
        units.extend(_chunk_text(transcript))

    # SOAP sections (individual semantic units)
    soap = soap_dict.get("soap", {})
    for section_key in ("S", "O", "A", "P"):
        section = soap.get(section_key)
        if not section:
            continue
        section_text = _flatten_soap_section(section_key, section)
        if section_text.strip():
            units.extend(_chunk_text(section_text))

    return [u for u in units if u.strip()]


def _chunk_text(text: str) -> list[str]:
    """Split text into overlapping windows of ~512 tokens."""
    if len(text) <= _CHUNK_CHARS:
        return [text.strip()]

    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []
    current_parts: list[str] = []
    current_len = 0

    for sentence in sentences:
        s_len = len(sentence)
        if current_len + s_len > _CHUNK_CHARS and current_parts:
            chunk_text = " ".join(current_parts).strip()
            if chunk_text:
                chunks.append(chunk_text)
            overlap = chunk_text[-_OVERLAP_CHARS:]
            current_parts = [overlap]
            current_len = len(overlap)

        current_parts.append(sentence)
        current_len += s_len + 1

    if current_parts:
        last = " ".join(current_parts).strip()
        if last:
            chunks.append(last)

    return chunks


def _flatten_soap_section(key: str, section: dict) -> str:
    """Convert a SOAP section dict to a flat text string."""
    if not isinstance(section, dict):
        return str(section)

    parts: list[str] = [f"Section {key}:"]

    for field_name, value in section.items():
        if isinstance(value, str) and value.strip():
            parts.append(f"{field_name}: {value}")
        elif isinstance(value, list) and value:
            flat = ", ".join(str(v) for v in value if v)
            if flat:
                parts.append(f"{field_name}: {flat}")
        elif isinstance(value, dict) and value:
            flat = json.dumps(value, ensure_ascii=False)
            parts.append(f"{field_name}: {flat}")

    return " | ".join(parts)


# ── Async embedding helper ────────────────────────────────────────────────────

async def _embed_async(texts: list[str]) -> list[list[float]]:
    """Embed texts on-premise using CamemBERT-bio (non-blocking)."""
    import asyncio
    from ia.embedding.service import get_embedding_service

    loop = asyncio.get_event_loop()
    service = get_embedding_service()

    def _embed_sync():
        return service.embed(texts)

    vectors = await loop.run_in_executor(None, _embed_sync)
    return [v.tolist() if hasattr(v, "tolist") else list(v) for v in vectors]
