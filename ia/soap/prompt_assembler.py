"""6-layer SOAP prompt assembler.

Layers (in order, from REQUIREMENTS.md §6):
    1. System prompt (SOAP rules + specialty + date)
    2. Safety context (allergies + active interactions)
    3. RAG chunks (top-5 MMR-deduplicated, formatted by namespace)
    4. Doctor style (NS5 few-shot examples for this doctor × motif)
    5. Transcript (pseudonymised)
    6. Final instruction (JSON-only output reminder)

The assembled prompt is passed to Claude claude-sonnet-4-6 (streaming).

Usage:
    prompt = assemble_soap_prompt(
        specialty="cardiologie",
        date="2026-04-14",
        allergies=["pénicilline"],
        interactions=[interaction_obj, ...],
        rag_chunks=final_chunks,       # list[SearchHit] after MMR
        style_examples=style_chunks,   # list[DoctorStyleChunk] or []
        transcript="Le patient se plaint de...",
    )
    # → list[dict]  (Anthropic messages format)
"""
from __future__ import annotations

import datetime
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger(__name__)

# Namespace → friendly label for chunk citations
_NAMESPACE_LABELS = {
    "ccam": "CCAM (ATIH)",
    "has": "Recommandation HAS",
    "vidal": "VIDAL / BDPM",
    "patient_history": "Historique patient",
    "doctor_corpus": "Corpus médecin",
}

# Max tokens per layer (rough estimates to stay within Claude's 200k context)
_MAX_CHUNK_CHARS = 600       # per chunk
_MAX_STYLE_CHARS = 800       # per style example
_MAX_STYLE_EXAMPLES = 3
_MAX_RAG_CHUNKS = 5


@dataclass
class AssembledPrompt:
    """Result of prompt assembly."""
    messages: list[dict[str, Any]]       # Anthropic messages format
    system: str                           # System prompt string
    chunk_ids_used: list[str]             # IDs of RAG chunks included
    style_examples_used: int


def assemble_soap_prompt(
    *,
    specialty: str,
    date: Optional[str] = None,
    allergies: list[str],
    interactions: list[Any],             # DrugInteraction-like objects with .drug_a, .drug_b, .severity
    rag_chunks: list[Any],               # SearchHit or BoostedPassage objects with .chunk_id, .content, .source
    style_examples: list[Any],           # DoctorStyleChunk-like objects with .text
    transcript: str,
) -> AssembledPrompt:
    """Assemble the 6-layer prompt for SOAP generation.

    Args:
        specialty:       Doctor's specialty (injected into system prompt).
        date:            Consultation date (ISO format). Defaults to today.
        allergies:       Patient allergy list (DCI names).
        interactions:    Detected drug interactions for this patient.
        rag_chunks:      Top-5 RAG results after MMR deduplication.
        style_examples:  NS5 few-shot examples for this doctor × motif_key.
        transcript:      Pseudonymised consultation transcript.

    Returns:
        AssembledPrompt with Anthropic-compatible messages list.
    """
    from ia.prompts.soap_system import SOAP_SYSTEM_PROMPT

    consultation_date = date or datetime.date.today().isoformat()

    # ── Layer 1 : System prompt ───────────────────────────────────────────────
    system = SOAP_SYSTEM_PROMPT.format(
        specialty=specialty or "médecine générale",
        date=consultation_date,
    )

    # ── Build user message with remaining layers ──────────────────────────────
    parts: list[str] = []

    # ── Layer 2 : Safety context ──────────────────────────────────────────────
    safety_lines: list[str] = []
    if allergies:
        safety_lines.append(f"ALLERGIES CONNUES : {', '.join(allergies)}")
    if interactions:
        for ia in interactions:
            sev = getattr(ia, "severity", "INCONNUE")
            drug_a = getattr(ia, "drug_a", "?")
            drug_b = getattr(ia, "drug_b", "?")
            mechanism = getattr(ia, "mechanism", "")
            safety_lines.append(
                f"INTERACTION {sev} : {drug_a} × {drug_b}"
                + (f" — {mechanism}" if mechanism else "")
            )

    if safety_lines:
        parts.append("=== CONTEXTE SÉCURITÉ ===")
        parts.extend(safety_lines)
        parts.append("")

    # ── Layer 3 : RAG chunks ──────────────────────────────────────────────────
    chunks_to_use = rag_chunks[:_MAX_RAG_CHUNKS]
    chunk_ids_used: list[str] = []

    if chunks_to_use:
        parts.append("=== RÉFÉRENCES MÉDICALES (RAG) ===")
        for i, chunk in enumerate(chunks_to_use, start=1):
            chunk_id = str(getattr(chunk, "chunk_id", f"unknown_{i}"))
            content = getattr(chunk, "content", "") or getattr(chunk, "text", "")
            source = getattr(chunk, "source", "")
            label = _NAMESPACE_LABELS.get(source, source)
            meta = getattr(chunk, "metadata", {}) or {}
            doc_title = meta.get("document_title", meta.get("title", ""))
            section = meta.get("section", "")

            # Truncate very long chunks
            if len(content) > _MAX_CHUNK_CHARS:
                content = content[:_MAX_CHUNK_CHARS] + "…"

            citation = f"[{i}] {label}"
            if doc_title:
                citation += f" — {doc_title}"
            if section:
                citation += f" § {section}"

            parts.append(citation)
            parts.append(content)
            parts.append("")
            chunk_ids_used.append(chunk_id)
        parts.append("")

    # ── Layer 4 : Doctor style examples (NS5 few-shot) ────────────────────────
    style_used = 0
    if style_examples:
        examples = style_examples[:_MAX_STYLE_EXAMPLES]
        parts.append("=== EXEMPLES DE STYLE DU MÉDECIN ===")
        for ex in examples:
            text = getattr(ex, "text", "")
            if len(text) > _MAX_STYLE_CHARS:
                text = text[:_MAX_STYLE_CHARS] + "…"
            parts.append(text)
            parts.append("")
            style_used += 1

    # ── Layer 5 : Transcript ──────────────────────────────────────────────────
    parts.append("=== TRANSCRIPT DE CONSULTATION ===")
    parts.append(transcript.strip())
    parts.append("")

    # ── Layer 6 : Final instruction ───────────────────────────────────────────
    parts.append("=== INSTRUCTION ===")
    parts.append(
        "Génère le compte-rendu SOAP complet au format JSON valide. "
        "Respecte strictement l'OUTPUT_SCHEMA. "
        "N'inclus aucun texte hors du JSON. "
        "Les codes CCAM et CIM-10 doivent provenir exclusivement des références fournies ci-dessus. "
        "Si le transcript ne mentionne pas une information → utilise \"[non mentionné]\"."
    )

    user_content = "\n".join(parts)

    messages = [{"role": "user", "content": user_content}]

    log.debug(
        "[prompt_assembler] system=%d chars, user=%d chars, chunks=%d, style=%d",
        len(system),
        len(user_content),
        len(chunk_ids_used),
        style_used,
    )

    return AssembledPrompt(
        messages=messages,
        system=system,
        chunk_ids_used=chunk_ids_used,
        style_examples_used=style_used,
    )


def assemble_rag_prompt(
    *,
    question: str,
    chunks: list[Any],
) -> tuple[str, str]:
    """Assemble a RAG Q&A prompt (not SOAP).

    Args:
        question: The clinical question.
        chunks:   Top retrieved chunks.

    Returns:
        (system_prompt, user_message) tuple for Anthropic API.
    """
    from ia.prompts.rag_system import RAG_SYSTEM_PROMPT

    chunk_texts: list[str] = []
    for i, chunk in enumerate(chunks[:_MAX_RAG_CHUNKS], start=1):
        content = getattr(chunk, "content", "") or getattr(chunk, "text", "")
        source = getattr(chunk, "source", "")
        meta = getattr(chunk, "metadata", {}) or {}
        label = _NAMESPACE_LABELS.get(source, source)
        title = meta.get("document_title", meta.get("title", ""))
        section = meta.get("section", "")

        header = f"[{i}] {label}"
        if title:
            header += f" — {title}"
        if section:
            header += f" § {section}"

        if len(content) > _MAX_CHUNK_CHARS:
            content = content[:_MAX_CHUNK_CHARS] + "…"

        chunk_texts.append(f"{header}\n{content}")

    chunks_formatted = "\n\n".join(chunk_texts) if chunk_texts else "Aucun document disponible."

    # The RAG system prompt already contains {chunks} and {question} placeholders
    full_system = RAG_SYSTEM_PROMPT.format(
        chunks=chunks_formatted,
        question=question,
    )

    # For RAG Q&A, we use the system prompt directly as the full message
    # (Claude doesn't need a separate user turn when the question is embedded)
    return full_system, question
