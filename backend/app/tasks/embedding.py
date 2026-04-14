"""Celery tasks wrapping the CamemBERT-bio embedding service.

These tasks run in the 'ai' queue (same GPU worker as transcription).

Tasks:
    embed_texts          — embed a list of texts, return vectors as list[list[float]]
    embed_and_index_chunk — embed a chunk and upsert into pgvector (used by indexers)
    reindex_all_chunks   — P1: full re-indexing job when model is swapped (DrBERT migration)

Note on isolation: NS4 (patient_history) embeddings are computed here but the upsert
MUST go through PatientVectorStore (ia/rag/retriever/patient_store.py) which enforces
the cabinet_id + patient_id filter. Never upsert patient chunks directly from this task.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from app.celery_app import celery_app

log = logging.getLogger(__name__)


@celery_app.task(
    name="app.tasks.embedding.embed_texts",
    bind=True,
    max_retries=2,
    default_retry_delay=15,
    queue="ai",
    acks_late=True,
)
def embed_texts(
    self,
    texts: list[str],
) -> list[list[float]]:
    """Embed a batch of texts using CamemBERT-bio.

    Args:
        texts: List of strings to embed. Max ~512 tokens each.

    Returns:
        List of 768-dimensional float vectors (one per input text).
    """
    try:
        from ia.embedding.service import get_embedding_service

        if not texts:
            return []

        service = get_embedding_service()
        vectors = service.embed(texts)
        log.info("[embed] Embedded %d texts → shape (%d, %d)", len(texts), len(vectors), len(vectors[0]) if vectors else 0)
        return vectors

    except Exception as exc:
        log.warning("[embed] Error: %s — retrying (%d/%d)", exc, self.request.retries, self.max_retries)
        raise self.retry(exc=exc)


@celery_app.task(
    name="app.tasks.embedding.embed_and_index_chunk",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    queue="ai",
    acks_late=True,
)
def embed_and_index_chunk(
    self,
    chunk_id: str,
    text: str,
    source: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Embed a single chunk and upsert its vector into the chunks table.

    This task is called by indexer jobs (CCAM, HAS, VIDAL, doctor_corpus).
    For NS4 (patient_history) use the dedicated PatientVectorStore instead.

    Args:
        chunk_id: UUID string of the Chunk record (must already exist in DB).
        text:     The chunk text to embed.
        source:   One of: ccam, has, vidal, doctor_corpus.
        metadata: JSONB metadata dict (already stored on the chunk).

    Returns:
        dict with chunk_id and embedding_dim.
    """
    _NS4_SOURCE = "patient_history"
    if source == _NS4_SOURCE:
        raise ValueError(
            "NS4 patient_history chunks must be indexed via PatientVectorStore, "
            "not embed_and_index_chunk. This prevents accidental bypass of RLS filters."
        )

    try:
        import asyncio
        from ia.embedding.service import get_embedding_service

        service = get_embedding_service()
        vectors = service.embed([text])
        vector = vectors[0]

        # Upsert vector via synchronous DB call (Celery worker uses sync SQLAlchemy)
        _upsert_chunk_vector_sync(chunk_id, vector)

        log.info("[embed_index] Indexed chunk %s (source=%s, dim=%d)", chunk_id, source, len(vector))
        return {"chunk_id": chunk_id, "embedding_dim": len(vector)}

    except ValueError:
        raise  # Don't retry on NS4 guard violation
    except Exception as exc:
        log.warning("[embed_index] Error chunk %s: %s — retrying", chunk_id, exc)
        raise self.retry(exc=exc)


@celery_app.task(
    name="app.tasks.embedding.reindex_all_chunks",
    bind=True,
    max_retries=1,
    queue="ai",
    acks_late=True,
    # Long-running task — no time limit enforced here; operator should set --time-limit
)
def reindex_all_chunks(
    self,
    source_filter: str | None = None,
    batch_size: int = 64,
) -> dict[str, Any]:
    """Re-embed all chunks (or a subset by source) and update vectors in DB.

    Used for the P1 DrBERT-7GB-cased migration. Processes in batches and
    reports progress via task state updates (PROGRESS).

    Args:
        source_filter: If set, only re-embed chunks with this source value.
                       Pass None to re-embed everything (excluding NS4).
        batch_size:    Number of chunks to embed per batch.

    Returns:
        dict with total_chunks, reindexed, skipped, errors.
    """
    try:
        stats = _reindex_sync(source_filter=source_filter, batch_size=batch_size, task=self)
        log.info("[reindex] Complete: %s", stats)
        return stats

    except Exception as exc:
        log.error("[reindex] Fatal error: %s", exc)
        raise self.retry(exc=exc)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _upsert_chunk_vector_sync(chunk_id: str, vector: list[float]) -> None:
    """Update the embedding column of a Chunk row using a sync DB session.

    Celery tasks run in a synchronous context; we use a plain psycopg2 connection
    rather than asyncpg to avoid event-loop complexity in workers.
    """
    import os
    import psycopg2  # type: ignore[import]
    from psycopg2.extras import register_vector  # type: ignore[import]
    import numpy as np

    dsn = os.environ["DATABASE_URL"].replace("+asyncpg", "").replace("postgresql+asyncpg", "postgresql")
    conn = psycopg2.connect(dsn)
    try:
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE chunks SET embedding = %s WHERE id = %s",
                (np.array(vector, dtype=np.float32), uuid.UUID(chunk_id)),
            )
        conn.commit()
    finally:
        conn.close()


def _reindex_sync(
    source_filter: str | None,
    batch_size: int,
    task,
) -> dict[str, Any]:
    """Batch re-index implementation. Reports PROGRESS state to Celery."""
    import os
    import psycopg2  # type: ignore[import]
    from psycopg2.extras import register_vector, DictCursor  # type: ignore[import]
    import numpy as np
    from ia.embedding.service import get_embedding_service

    dsn = os.environ["DATABASE_URL"].replace("+asyncpg", "").replace("postgresql+asyncpg", "postgresql")
    service = get_embedding_service()

    conn = psycopg2.connect(dsn)
    try:
        register_vector(conn)

        # Count total (excluding NS4)
        with conn.cursor() as cur:
            if source_filter:
                cur.execute("SELECT COUNT(*) FROM chunks WHERE source = %s", (source_filter,))
            else:
                cur.execute("SELECT COUNT(*) FROM chunks WHERE source != 'patient_history'")
            total = cur.fetchone()[0]

        reindexed = 0
        skipped = 0
        errors = 0
        offset = 0

        while offset < total:
            with conn.cursor(cursor_factory=DictCursor) as cur:
                if source_filter:
                    cur.execute(
                        "SELECT id, content FROM chunks WHERE source = %s ORDER BY id LIMIT %s OFFSET %s",
                        (source_filter, batch_size, offset),
                    )
                else:
                    cur.execute(
                        "SELECT id, content FROM chunks WHERE source != 'patient_history' ORDER BY id LIMIT %s OFFSET %s",
                        (batch_size, offset),
                    )
                rows = cur.fetchall()

            if not rows:
                break

            texts = [r["content"] for r in rows]
            ids = [r["id"] for r in rows]

            try:
                vectors = service.embed(texts)
            except Exception as exc:
                log.error("[reindex] Batch embed failed at offset %d: %s", offset, exc)
                errors += len(rows)
                offset += batch_size
                continue

            with conn.cursor() as cur:
                for chunk_uuid, vec in zip(ids, vectors):
                    try:
                        cur.execute(
                            "UPDATE chunks SET embedding = %s WHERE id = %s",
                            (np.array(vec, dtype=np.float32), chunk_uuid),
                        )
                        reindexed += 1
                    except Exception as exc:
                        log.warning("[reindex] Failed to update chunk %s: %s", chunk_uuid, exc)
                        errors += 1
            conn.commit()

            offset += batch_size
            # Report progress to Celery result backend
            task.update_state(
                state="PROGRESS",
                meta={"total": total, "reindexed": reindexed, "errors": errors},
            )
            log.info("[reindex] Progress: %d/%d", reindexed, total)

    finally:
        conn.close()

    return {"total_chunks": total, "reindexed": reindexed, "skipped": skipped, "errors": errors}
