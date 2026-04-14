"""Celery job: extract, chunk, embed, and index a Document into pgvector.

Triggered by POST /documents/upload after the file is persisted.
Progress is published to Redis pub/sub channel `doc:progress:{document_id}`.

Pipeline:
  1. Load Document row from DB
  2. Extract raw text (pdfplumber for PDF, python-docx for DOCX)
  3. Store raw text + SHA-256 content_hash on Document
  4. Semantic chunking: 512-token windows, 64-token overlap
  5. Upsert Chunk rows (document_id, namespace, text, chunk_index, metadata)
  6. Embed each chunk via CamemBERT-bio
  7. Update chunk.embedding in DB
  8. Publish DONE event to Redis

Progress events (JSON published to channel):
  {"status": "extracting", "document_id": "...", "filename": "..."}
  {"status": "chunking",   "document_id": "...", "chunk_count": N}
  {"status": "embedding",  "document_id": "...", "done": N, "total": M}
  {"status": "done",       "document_id": "...", "chunk_count": N}
  {"status": "error",      "document_id": "...", "message": "..."}
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from typing import Any

from app.celery_app import celery_app

log = logging.getLogger(__name__)

# Approximate characters per token for French text (used for chunking)
_CHARS_PER_TOKEN = 4
_CHUNK_TOKENS = 512
_OVERLAP_TOKENS = 64
_CHUNK_CHARS = _CHUNK_TOKENS * _CHARS_PER_TOKEN   # 2048
_OVERLAP_CHARS = _OVERLAP_TOKENS * _CHARS_PER_TOKEN  # 256

# Maximum file size accepted by the upload endpoint (50 MB)
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


# ── Celery task ───────────────────────────────────────────────────────────────

@celery_app.task(
    name="app.jobs.index_document.index_document",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    queue="ai",
    acks_late=True,
)
def index_document(
    self,
    document_id: str,
    file_path: str,         # absolute path on disk (temp file kept until task finishes)
    cabinet_id: str,
    medecin_id: str,
) -> dict[str, Any]:
    """Index a uploaded document: extract → chunk → embed → upsert pgvector.

    Args:
        document_id: UUID of the existing Document row.
        file_path:   Path to the uploaded file (PDF or DOCX).
        cabinet_id:  UUID string of the owning cabinet (for namespace isolation).
        medecin_id:  UUID string of the uploading doctor.

    Returns:
        dict with document_id, chunk_count, status.
    """
    redis_client = _get_redis()
    channel = f"doc:progress:{document_id}"

    def _pub(payload: dict) -> None:
        try:
            if redis_client:
                redis_client.publish(channel, json.dumps({**payload, "document_id": document_id}))
        except Exception:
            pass  # Non-fatal

    try:
        # ── 1. Extract text ───────────────────────────────────────────────────
        _pub({"status": "extracting", "filename": os.path.basename(file_path)})
        raw_text = _extract_text(file_path)
        if not raw_text.strip():
            raise ValueError("Extracted text is empty — unsupported or corrupted file")

        content_hash = hashlib.sha256(raw_text.encode()).hexdigest()

        # ── 2. Chunk ──────────────────────────────────────────────────────────
        chunks = _semantic_chunk(raw_text)
        _pub({"status": "chunking", "chunk_count": len(chunks)})
        log.info(
            "[index_document] doc=%s → %d chunks from %d chars",
            document_id, len(chunks), len(raw_text),
        )

        # ── 3. DB: update document + upsert chunks ────────────────────────────
        chunk_ids = _persist_to_db(
            document_id=document_id,
            raw_text=raw_text,
            content_hash=content_hash,
            chunks=chunks,
            cabinet_id=cabinet_id,
            medecin_id=medecin_id,
        )

        # ── 4. Embed + update vectors ─────────────────────────────────────────
        _embed_and_update(chunk_ids, chunks, redis_channel=channel, pub_fn=_pub)

        # ── 5. Rebuild BM25 index (fire-and-forget) ───────────────────────────
        try:
            from app.tasks.rag import rebuild_bm25_index
            rebuild_bm25_index.apply_async(countdown=60, queue="ai")
        except Exception:
            pass  # Non-blocking

        _pub({"status": "done", "chunk_count": len(chunks)})
        log.info("[index_document] Done: doc=%s chunks=%d", document_id, len(chunks))
        return {"document_id": document_id, "chunk_count": len(chunks), "status": "done"}

    except Exception as exc:
        log.error("[index_document] Failed doc=%s: %s", document_id, exc, exc_info=True)
        _pub({"status": "error", "message": str(exc)})
        _mark_document_error(document_id)
        raise self.retry(exc=exc)

    finally:
        # Clean up temp file
        try:
            if os.path.exists(file_path):
                os.unlink(file_path)
        except Exception:
            pass


# ── Text extraction ───────────────────────────────────────────────────────────

def _extract_text(file_path: str) -> str:
    """Extract plain text from PDF or DOCX file."""
    ext = os.path.splitext(file_path)[1].lower()

    if ext == ".pdf":
        return _extract_pdf(file_path)
    elif ext in (".docx", ".doc"):
        return _extract_docx(file_path)
    else:
        raise ValueError(f"Unsupported file type: {ext}")


def _extract_pdf(path: str) -> str:
    """Extract text from PDF using pdfplumber, page by page."""
    import pdfplumber  # type: ignore[import]

    pages: list[str] = []
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            if text.strip():
                pages.append(f"[Page {i + 1}]\n{text}")

    return "\n\n".join(pages)


def _extract_docx(path: str) -> str:
    """Extract text from DOCX using python-docx, preserving heading structure."""
    import docx  # type: ignore[import]

    doc = docx.Document(path)
    parts: list[str] = []

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        # Add heading markers so the chunker can split on section boundaries
        if para.style.name.startswith("Heading"):
            parts.append(f"\n## {text}\n")
        else:
            parts.append(text)

    return "\n".join(parts)


# ── Semantic chunking ─────────────────────────────────────────────────────────

def _semantic_chunk(text: str) -> list[str]:
    """Split text into overlapping windows of ~512 tokens (2048 chars), overlap 64 tokens.

    Splits preferentially on sentence boundaries ('. ', '.\n') rather than
    mid-sentence to preserve semantic units.
    """
    # Normalise line endings
    text = re.sub(r"\r\n|\r", "\n", text)

    if len(text) <= _CHUNK_CHARS:
        return [text.strip()]

    # Split into sentences (rough heuristic, good enough for chunking)
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

            # Overlap: keep the last OVERLAP_CHARS worth of content
            overlap_text = chunk_text[-_OVERLAP_CHARS:]
            current_parts = [overlap_text]
            current_len = len(overlap_text)

        current_parts.append(sentence)
        current_len += s_len + 1  # +1 for space

    # Last chunk
    if current_parts:
        last = " ".join(current_parts).strip()
        if last:
            chunks.append(last)

    return chunks


# ── DB persistence ────────────────────────────────────────────────────────────

def _persist_to_db(
    document_id: str,
    raw_text: str,
    content_hash: str,
    chunks: list[str],
    cabinet_id: str,
    medecin_id: str,
) -> list[str]:
    """Update Document + insert Chunk rows. Returns list of chunk UUIDs."""
    import psycopg2  # type: ignore[import]

    dsn = _db_dsn()
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            # Update document: store raw text, hash, mark as being indexed
            cur.execute(
                """
                UPDATE document
                SET content_raw = %s,
                    content_hash = %s
                WHERE id = %s
                """,
                (raw_text[:200_000], content_hash, uuid.UUID(document_id)),
            )

            # Delete any previous chunks (re-indexing scenario)
            cur.execute(
                "DELETE FROM chunk WHERE document_id = %s",
                (uuid.UUID(document_id),),
            )

            chunk_ids: list[str] = []
            for idx, chunk_text in enumerate(chunks):
                chunk_id = str(uuid.uuid4())
                chunk_ids.append(chunk_id)
                # namespace: private uploads go to doctor_corpus (NS5)
                cur.execute(
                    """
                    INSERT INTO chunk
                        (id, document_id, namespace, text, chunk_index, metadata, created_at)
                    VALUES
                        (%s, %s, 'doctor_corpus', %s, %s, %s, NOW())
                    """,
                    (
                        uuid.UUID(chunk_id),
                        uuid.UUID(document_id),
                        chunk_text,
                        idx,
                        json.dumps({
                            "cabinet_id": cabinet_id,
                            "doctor_id": medecin_id,
                            "document_id": document_id,
                            "chunk_index": idx,
                        }),
                    ),
                )

        conn.commit()
        return chunk_ids

    finally:
        conn.close()


def _embed_and_update(
    chunk_ids: list[str],
    chunks: list[str],
    redis_channel: str,
    pub_fn,
    batch_size: int = 32,
) -> None:
    """Embed chunks in batches and update the embedding column in DB."""
    import numpy as np
    import psycopg2  # type: ignore[import]
    from psycopg2.extras import register_vector  # type: ignore[import]
    from ia.embedding.service import get_embedding_service

    service = get_embedding_service()
    dsn = _db_dsn()
    conn = psycopg2.connect(dsn)

    try:
        register_vector(conn)
        total = len(chunks)
        done = 0

        for batch_start in range(0, total, batch_size):
            batch_texts = chunks[batch_start: batch_start + batch_size]
            batch_ids = chunk_ids[batch_start: batch_start + batch_size]

            vectors = service.embed(batch_texts)

            with conn.cursor() as cur:
                for chunk_id, vec in zip(batch_ids, vectors):
                    cur.execute(
                        "UPDATE chunk SET embedding = %s WHERE id = %s",
                        (np.array(vec, dtype=np.float32), uuid.UUID(chunk_id)),
                    )
            conn.commit()
            done += len(batch_texts)
            pub_fn({"status": "embedding", "done": done, "total": total})

    finally:
        conn.close()


def _mark_document_error(document_id: str) -> None:
    """Soft-mark a document as deprecated on indexing failure."""
    try:
        import psycopg2  # type: ignore[import]
        conn = psycopg2.connect(_db_dsn())
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE document SET deprecated = TRUE WHERE id = %s",
                (uuid.UUID(document_id),),
            )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ── Helpers ───────────────────────────────────────────────────────────────────

def _db_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("+asyncpg", "").replace("postgresql+asyncpg", "postgresql")


def _get_redis():
    """Return a synchronous Redis client for pub/sub from the task worker."""
    try:
        import redis  # type: ignore[import]
        return redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    except Exception:
        return None
