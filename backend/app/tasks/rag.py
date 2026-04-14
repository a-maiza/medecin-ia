"""Celery tasks for RAG pipeline maintenance.

Tasks:
    rebuild_bm25_index  — Nightly rebuild of BM25Okapi index (Celery beat schedule).
"""
from __future__ import annotations

import logging
import os

from app.celery_app import celery_app

log = logging.getLogger(__name__)


@celery_app.task(
    name="app.tasks.rag.rebuild_bm25_index",
    bind=True,
    max_retries=1,
    queue="ai",
    acks_late=True,
)
def rebuild_bm25_index(self) -> dict:
    """Rebuild the BM25Okapi index from all non-patient chunks in PostgreSQL.

    Scheduled nightly via Celery beat (defined in celery_app.py beat schedule).
    Takes ~10-30 min for 420 000 chunks on a standard CPU worker.

    Returns:
        dict with chunk_count and built_at timestamp.
    """
    try:
        from ia.rag.retriever.bm25_index import build_and_save_index

        db_url = os.environ["DATABASE_URL"]
        index = build_and_save_index(db_url)

        result = {"chunk_count": len(index.chunk_ids), "built_at": index.built_at}
        log.info("[rebuild_bm25] Done: %s", result)
        return result

    except Exception as exc:
        log.error("[rebuild_bm25] Failed: %s", exc)
        raise self.retry(exc=exc)
