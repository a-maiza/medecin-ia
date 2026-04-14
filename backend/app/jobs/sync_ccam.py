"""Celery job: weekly sync of CCAM nomenclature into chunks table (NS1).

Schedule: weekly on Sunday at 01:00 UTC (configured in celery_app.py).

Source: ATIH (Agence Technique de l'Information sur l'Hospitalisation)
  URL: configured via CCAM_DATA_URL env var (ATIH requires prior account)
  Format: CSV/XML export of CCAM version ~99.0+

Delta detection: SHA-256 of downloaded file — skips full re-index if unchanged.

Requires env vars:
  DATABASE_URL  — PostgreSQL DSN
  CCAM_DATA_URL — Download URL for the CCAM file (ATIH account required)

Each CCAM row becomes one Chunk with:
  namespace = 'ccam'
  text      = "{code} — {libelle_long}. {notes}"
  metadata  = {code, chapter, specialty, has_grade: null}
"""
from __future__ import annotations

import csv
import hashlib
import io
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from app.celery_app import celery_app

log = logging.getLogger(__name__)


@celery_app.task(
    name="app.jobs.sync_ccam.sync_ccam",
    bind=True,
    max_retries=2,
    default_retry_delay=600,
    queue="ai",
    acks_late=True,
    soft_time_limit=3600,   # 1h soft limit (CCAM is large: ~18 min on GPU)
    time_limit=3900,
)
def sync_ccam(self) -> dict[str, Any]:
    """Download CCAM file, detect delta, chunk + embed + upsert into pgvector.

    Returns:
        dict with upserted_chunks, skipped, errors, content_hash, synced_at.
    """
    url = os.environ.get("CCAM_DATA_URL", "")
    if not url:
        log.warning("[sync_ccam] CCAM_DATA_URL not set — skipping")
        return {"upserted_chunks": 0, "skipped": 0, "errors": 0,
                "synced_at": _now_iso(), "reason": "CCAM_DATA_URL not configured"}

    try:
        log.info("[sync_ccam] Downloading CCAM from %s", url)
        raw_bytes = _download(url)
        content_hash = hashlib.sha256(raw_bytes).hexdigest()

        if _hash_unchanged(content_hash, "ccam"):
            log.info("[sync_ccam] Hash unchanged — skipping")
            return {"upserted_chunks": 0, "skipped": 0, "errors": 0,
                    "content_hash": content_hash, "synced_at": _now_iso(), "delta": False}

        rows = _parse_ccam(raw_bytes)
        log.info("[sync_ccam] Parsed %d CCAM codes", len(rows))

        # Create/find a global Document row for this sync
        doc_id = _ensure_global_document(
            source="ccam",
            filename=f"ccam_{_now_iso()[:10]}.csv",
            content_hash=content_hash,
        )

        stats = _upsert_ccam_chunks(doc_id, rows)
        _store_hash(content_hash, "ccam")

        # Trigger BM25 rebuild (non-blocking)
        try:
            from app.tasks.rag import rebuild_bm25_index
            rebuild_bm25_index.apply_async(countdown=120, queue="ai")
        except Exception:
            pass

        log.info("[sync_ccam] Done: %s", stats)
        return {**stats, "content_hash": content_hash, "synced_at": _now_iso(), "delta": True}

    except Exception as exc:
        log.error("[sync_ccam] Failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)


# ── Parsing ───────────────────────────────────────────────────────────────────

def _parse_ccam(raw: bytes) -> list[dict[str, str]]:
    """Parse CCAM CSV/TSV export into normalised dicts.

    Expected columns (ATIH format, tab-separated):
        CODE | LIBELLE_COURT | LIBELLE_LONG | CHAPITRE | NOTES
    """
    try:
        text = raw.decode("utf-8-sig", errors="replace")
    except Exception:
        text = raw.decode("latin-1", errors="replace")

    rows: list[dict[str, str]] = []
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")

    for row in reader:
        code = (row.get("CODE") or row.get("code") or "").strip()
        libelle = (
            row.get("LIBELLE_LONG")
            or row.get("LIBELLE_COURT")
            or row.get("libelle_long")
            or row.get("libelle")
            or ""
        ).strip()
        chapter = (row.get("CHAPITRE") or row.get("chapitre") or "").strip()
        notes = (row.get("NOTES") or row.get("notes") or "").strip()

        if not code or not libelle:
            continue

        text_content = f"{code} — {libelle}"
        if notes:
            text_content += f". {notes}"

        rows.append({
            "code": code,
            "libelle": libelle,
            "chapter": chapter,
            "text": text_content,
        })

    return rows


# ── DB operations ─────────────────────────────────────────────────────────────

def _upsert_ccam_chunks(doc_id: str, rows: list[dict[str, str]]) -> dict[str, int]:
    """Delete old CCAM chunks for this document and insert fresh ones with embeddings."""
    import numpy as np
    import psycopg2  # type: ignore[import]
    from psycopg2.extras import register_vector  # type: ignore[import]
    from ia.embedding.service import get_embedding_service
    import json

    dsn = _db_dsn()
    conn = psycopg2.connect(dsn)
    service = get_embedding_service()

    upserted = errors = 0
    batch_size = 64

    try:
        register_vector(conn)

        # Clear previous chunks for this document
        with conn.cursor() as cur:
            cur.execute("DELETE FROM chunk WHERE document_id = %s", (uuid.UUID(doc_id),))
        conn.commit()

        for batch_start in range(0, len(rows), batch_size):
            batch = rows[batch_start: batch_start + batch_size]
            texts = [r["text"] for r in batch]

            try:
                vectors = service.embed(texts)
            except Exception as exc:
                log.error("[sync_ccam] Embed failed at offset %d: %s", batch_start, exc)
                errors += len(batch)
                continue

            with conn.cursor() as cur:
                for idx, (row, vec) in enumerate(zip(batch, vectors)):
                    try:
                        cur.execute(
                            """
                            INSERT INTO chunk
                                (id, document_id, namespace, text, chunk_index,
                                 metadata, embedding, created_at)
                            VALUES
                                (%s, %s, 'ccam', %s, %s, %s, %s, NOW())
                            """,
                            (
                                uuid.uuid4(),
                                uuid.UUID(doc_id),
                                row["text"],
                                batch_start + idx,
                                json.dumps({
                                    "code": row["code"],
                                    "chapter": row["chapter"],
                                    "specialty": None,
                                }),
                                np.array(vec, dtype=np.float32),
                            ),
                        )
                        upserted += 1
                    except Exception as exc:
                        log.warning("[sync_ccam] Insert failed for %s: %s", row["code"], exc)
                        errors += 1
            conn.commit()
            log.info("[sync_ccam] Progress: %d/%d", batch_start + len(batch), len(rows))

    finally:
        conn.close()

    return {"upserted_chunks": upserted, "errors": errors, "skipped": 0}


def _ensure_global_document(source: str, filename: str, content_hash: str) -> str:
    """Return existing or create a new global Document row for this sync."""
    import psycopg2  # type: ignore[import]

    conn = psycopg2.connect(_db_dsn())
    try:
        with conn.cursor() as cur:
            # Try to find an existing non-deprecated doc for this source
            cur.execute(
                "SELECT id FROM document WHERE source = %s AND deprecated = FALSE LIMIT 1",
                (source,),
            )
            row = cur.fetchone()
            if row:
                doc_id = str(row[0])
                # Update hash
                cur.execute(
                    "UPDATE document SET content_hash = %s, filename = %s WHERE id = %s",
                    (content_hash, filename, uuid.UUID(doc_id)),
                )
                conn.commit()
                return doc_id

            # Create new document
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


# ── Shared helpers (also imported by sync_has.py) ────────────────────────────

def _download(url: str) -> bytes:
    import urllib.request
    with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310
        return resp.read()


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


def _store_hash(content_hash: str, source: str) -> None:
    # hash is already stored by _ensure_global_document; this is a no-op stub
    # kept for API symmetry with sync_vidal
    pass


def _db_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("+asyncpg", "").replace("postgresql+asyncpg", "postgresql")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
