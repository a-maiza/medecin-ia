"""HAS guidelines indexer — NS2.

Downloads or processes HAS (Haute Autorité de Santé) recommendation PDFs,
extracts text with pdfplumber, splits hierarchically on heading boundaries
(H1 → chapter, H2 → section), embeds with CamemBERT-bio, and upserts into
the `chunk` table (namespace='has').

Metadata stored per chunk:
    pathologie  — disease/topic (from PDF filename or provided)
    has_grade   — evidence grade (A/B/C/AE) extracted from text, or null
    annee       — publication year (from PDF metadata or provided)
    section     — section heading
    page        — page number

Usage:
    from ia.rag.indexer.has_indexer import HasIndexer, HasDocumentMeta

    indexer = HasIndexer()
    stats = indexer.index_pdf(
        path="/tmp/has_guide_hta.pdf",
        meta=HasDocumentMeta(pathologie="HTA", has_grade="A", annee=2023),
    )

    # Or bulk-index a directory:
    stats = indexer.index_directory("/data/has/", default_annee=2024)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_EMBED_BATCH = 32
_CHUNK_CHARS = 2048    # ~512 tokens
_OVERLAP_CHARS = 256   # ~64 tokens

# Regex to extract HAS evidence grade from text (A, B, C, AE, accord d'experts)
_GRADE_RE = re.compile(
    r"\b(grade\s*[:\-]?\s*([A-C]|AE)|accord\s+d['\u2019]experts)\b",
    re.IGNORECASE,
)


@dataclass
class HasDocumentMeta:
    """Metadata for a HAS recommendation document."""
    pathologie: str = ""
    has_grade: Optional[str] = None   # A / B / C / AE (highest grade in the doc)
    annee: Optional[int] = None


@dataclass
class IndexStats:
    upserted: int = 0
    skipped: int = 0
    errors: int = 0
    delta: bool = True
    content_hash: str = ""
    extra: dict = field(default_factory=dict)


class HasIndexer:
    """Hierarchical chunker + embedder for HAS recommendation PDFs."""

    def index_pdf(
        self,
        path: str,
        meta: HasDocumentMeta,
        doc_id: Optional[str] = None,
    ) -> IndexStats:
        """Index a single HAS PDF.

        Args:
            path:   Absolute path to the PDF file.
            meta:   Document-level metadata (pathologie, has_grade, annee).
            doc_id: Existing document UUID. If None, a new row is created.
        """
        raw = Path(path).read_bytes()
        content_hash = hashlib.sha256(raw).hexdigest()

        if _hash_unchanged(content_hash, "has"):
            log.info("[HasIndexer] Hash unchanged for %s — skipping", path)
            return IndexStats(content_hash=content_hash, delta=False)

        if doc_id is None:
            doc_id = _ensure_global_document(
                source="has",
                filename=os.path.basename(path),
                content_hash=content_hash,
            )

        sections = _extract_sections(path)
        log.info("[HasIndexer] Extracted %d sections from %s", len(sections), path)

        chunks = _hierarchical_chunk(sections)
        log.info("[HasIndexer] %d chunks after hierarchical chunking", len(chunks))

        # Infer grade from text if not provided
        if meta.has_grade is None:
            meta = HasDocumentMeta(
                pathologie=meta.pathologie,
                has_grade=_infer_grade(sections),
                annee=meta.annee,
            )

        stats = self._upsert(doc_id, chunks, meta)
        stats.content_hash = content_hash
        return stats

    def index_directory(
        self,
        directory: str,
        default_annee: Optional[int] = None,
    ) -> IndexStats:
        """Index all PDF files in a directory.

        Pathologie is inferred from the filename (stem before first underscore).
        """
        total = IndexStats()
        for pdf_path in sorted(Path(directory).glob("*.pdf")):
            stem = pdf_path.stem
            pathologie = stem.split("_")[0].replace("-", " ")
            meta = HasDocumentMeta(pathologie=pathologie, annee=default_annee)
            try:
                stats = self.index_pdf(str(pdf_path), meta)
                total.upserted += stats.upserted
                total.errors += stats.errors
                total.skipped += stats.skipped
            except Exception as exc:
                log.error("[HasIndexer] Failed to index %s: %s", pdf_path, exc)
                total.errors += 1
        return total

    def _upsert(
        self,
        doc_id: str,
        chunks: list[dict],
        meta: HasDocumentMeta,
    ) -> IndexStats:
        import numpy as np
        import psycopg2  # type: ignore[import]
        from psycopg2.extras import register_vector  # type: ignore[import]
        from ia.embedding.service import get_embedding_service

        service = get_embedding_service()
        conn = psycopg2.connect(_db_dsn())
        stats = IndexStats()

        try:
            register_vector(conn)

            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM chunk WHERE document_id = %s AND namespace = 'has'",
                    (uuid.UUID(doc_id),),
                )
            conn.commit()

            for batch_start in range(0, len(chunks), _EMBED_BATCH):
                batch = chunks[batch_start: batch_start + _EMBED_BATCH]
                texts = [c["text"] for c in batch]

                try:
                    vectors = service.embed(texts)
                except Exception as exc:
                    log.error("[HasIndexer] Embed failed at offset %d: %s", batch_start, exc)
                    stats.errors += len(batch)
                    continue

                with conn.cursor() as cur:
                    for idx, (chunk, vec) in enumerate(zip(batch, vectors)):
                        try:
                            cur.execute(
                                """
                                INSERT INTO chunk
                                    (id, document_id, namespace, text, chunk_index,
                                     metadata, embedding, created_at)
                                VALUES
                                    (%s, %s, 'has', %s, %s, %s, %s, NOW())
                                """,
                                (
                                    uuid.uuid4(),
                                    uuid.UUID(doc_id),
                                    chunk["text"],
                                    batch_start + idx,
                                    json.dumps({
                                        "pathologie": meta.pathologie,
                                        "has_grade": meta.has_grade,
                                        "annee": meta.annee,
                                        "section": chunk.get("section", ""),
                                        "page": chunk.get("page"),
                                    }),
                                    np.array(vec, dtype=np.float32),
                                ),
                            )
                            stats.upserted += 1
                        except Exception as exc:
                            log.warning("[HasIndexer] Insert failed: %s", exc)
                            stats.errors += 1

                conn.commit()
                log.debug("[HasIndexer] Progress: %d/%d", batch_start + len(batch), len(chunks))

        finally:
            conn.close()

        return stats


# ── PDF extraction ─────────────────────────────────────────────────────────────

def _extract_sections(pdf_path: str) -> list[dict]:
    """Extract text sections from a HAS PDF using pdfplumber.

    Returns a list of dicts: {section, text, page}.
    Sections are split on heading patterns (numbered headings, bold short lines).
    """
    import pdfplumber  # type: ignore[import]

    sections: list[dict] = []
    current_section = "Introduction"
    current_parts: list[str] = []
    current_page = 1

    _HEADING_RE = re.compile(
        r"^(\d+[\.\d]*\s+[A-ZÀÂÉÈÊËÎÏÔÙÛÜ].{3,80}|[A-ZÀÂÉÈÊËÎÏÔÙÛÜ][A-ZÀÂÉÈÊËÎÏÔÙÛÜ ]{4,60})$"
    )

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue

                if _HEADING_RE.match(line) and len(line) < 120:
                    # Flush current section
                    if current_parts:
                        sections.append({
                            "section": current_section,
                            "text": " ".join(current_parts),
                            "page": current_page,
                        })
                        current_parts = []
                    current_section = line
                    current_page = page_num
                else:
                    current_parts.append(line)

    # Flush last section
    if current_parts:
        sections.append({
            "section": current_section,
            "text": " ".join(current_parts),
            "page": current_page,
        })

    return sections


# ── Hierarchical chunking ─────────────────────────────────────────────────────

def _hierarchical_chunk(sections: list[dict]) -> list[dict]:
    """Split sections into chunks of ~512 tokens (2048 chars) with 64-token overlap."""
    chunks: list[dict] = []

    for section in sections:
        text = section["text"]
        if not text.strip():
            continue

        if len(text) <= _CHUNK_CHARS:
            chunks.append({
                "text": text.strip(),
                "section": section["section"],
                "page": section.get("page"),
            })
            continue

        # Split into sentences, then build windows
        sentences = re.split(r"(?<=[.!?])\s+", text)
        current_parts: list[str] = []
        current_len = 0

        for sentence in sentences:
            s_len = len(sentence)
            if current_len + s_len > _CHUNK_CHARS and current_parts:
                chunk_text = " ".join(current_parts).strip()
                if chunk_text:
                    chunks.append({
                        "text": chunk_text,
                        "section": section["section"],
                        "page": section.get("page"),
                    })
                overlap = chunk_text[-_OVERLAP_CHARS:]
                current_parts = [overlap]
                current_len = len(overlap)

            current_parts.append(sentence)
            current_len += s_len + 1

        if current_parts:
            last = " ".join(current_parts).strip()
            if last:
                chunks.append({
                    "text": last,
                    "section": section["section"],
                    "page": section.get("page"),
                })

    return chunks


def _infer_grade(sections: list[dict]) -> Optional[str]:
    """Extract the highest evidence grade mentioned in the document."""
    grade_priority = {"A": 4, "B": 3, "C": 2, "AE": 1}
    best: Optional[str] = None

    for section in sections:
        for m in _GRADE_RE.finditer(section.get("text", "")):
            raw = m.group(2) or "AE"
            grade = raw.upper().strip()
            if best is None or grade_priority.get(grade, 0) > grade_priority.get(best, 0):
                best = grade

    return best


# ── DB helpers ────────────────────────────────────────────────────────────────

def _db_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("+asyncpg", "").replace("postgresql+asyncpg", "postgresql")


def _hash_unchanged(new_hash: str, source: str) -> bool:
    try:
        import psycopg2  # type: ignore[import]
        conn = psycopg2.connect(_db_dsn())
        with conn.cursor() as cur:
            cur.execute(
                "SELECT content_hash FROM document "
                "WHERE source = %s AND deprecated = FALSE "
                "ORDER BY uploaded_at DESC LIMIT 1",
                (source,),
            )
            row = cur.fetchone()
        conn.close()
        return row is not None and row[0] == new_hash
    except Exception:
        return False


def _ensure_global_document(source: str, filename: str, content_hash: str) -> str:
    import psycopg2  # type: ignore[import]
    conn = psycopg2.connect(_db_dsn())
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM document WHERE source = %s AND deprecated = FALSE LIMIT 1",
                (source,),
            )
            row = cur.fetchone()
            if row:
                doc_id = str(row[0])
                cur.execute(
                    "UPDATE document SET content_hash = %s, filename = %s WHERE id = %s",
                    (content_hash, filename, uuid.UUID(doc_id)),
                )
                conn.commit()
                return doc_id

            doc_id = str(uuid.uuid4())
            cur.execute(
                """
                INSERT INTO document
                    (id, type, source, filename, content_hash, deprecated, uploaded_at)
                VALUES
                    (%s, 'global', %s, %s, %s, FALSE, NOW())
                """,
                (uuid.UUID(doc_id), source, filename, content_hash),
            )
            conn.commit()
            return doc_id
    finally:
        conn.close()
