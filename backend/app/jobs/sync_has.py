"""Celery job: monthly sync of HAS clinical guidelines into chunks table (NS2).

Schedule: 1st of each month at 01:30 UTC (configured in celery_app.py).

Source: HAS (Haute Autorité de Santé) — has-sante.fr
  The HAS API provides PDF links and metadata for recommendations (fiches mémo,
  recommandations de bonne pratique, guides ALD).
  URL: configured via HAS_API_URL env var

Delta detection: SHA-256 per document — only re-indexes changed documents.

Requires env vars:
  DATABASE_URL  — PostgreSQL DSN
  HAS_API_URL   — Override base URL for HAS metadata API (optional)

Each guideline section becomes one Chunk with:
  namespace = 'has'
  text      = "{titre} — {section_title}: {content}"
  metadata  = {pathologie, has_grade, annee, url_source}
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from app.celery_app import celery_app

log = logging.getLogger(__name__)

_HAS_API_BASE = "https://api.has-sante.fr/api/v1"


@celery_app.task(
    name="app.jobs.sync_has.sync_has",
    bind=True,
    max_retries=2,
    default_retry_delay=600,
    queue="ai",
    acks_late=True,
    soft_time_limit=5400,   # 90-min soft limit (many PDFs)
    time_limit=5700,
)
def sync_has(
    self,
    mode: str = "full",     # "full" | "memo-only" | "reco-only"
) -> dict[str, Any]:
    """Sync HAS documents into NS2 chunks.

    Args:
        mode: "memo-only" for faster syncs (fiches mémo only).
              "full" for all document types.

    Returns:
        dict with new_docs, updated_docs, skipped, errors, synced_at.
    """
    has_url = os.environ.get("HAS_API_URL", _HAS_API_BASE)

    try:
        log.info("[sync_has] Starting HAS sync (mode=%s)", mode)
        documents = _fetch_has_metadata(has_url, mode)
        log.info("[sync_has] Found %d HAS documents", len(documents))

        new_docs = updated_docs = skipped = errors = 0

        for doc_meta in documents:
            try:
                result = _process_has_document(doc_meta)
                if result == "new":
                    new_docs += 1
                elif result == "updated":
                    updated_docs += 1
                elif result == "skipped":
                    skipped += 1
            except Exception as exc:
                log.warning("[sync_has] Failed to process %s: %s",
                            doc_meta.get("titre", "?"), exc)
                errors += 1

        # Trigger BM25 rebuild
        try:
            from app.tasks.rag import rebuild_bm25_index
            rebuild_bm25_index.apply_async(countdown=120, queue="ai")
        except Exception:
            pass

        result = {
            "new_docs": new_docs,
            "updated_docs": updated_docs,
            "skipped": skipped,
            "errors": errors,
            "synced_at": _now_iso(),
        }
        log.info("[sync_has] Done: %s", result)
        return result

    except Exception as exc:
        log.error("[sync_has] Fatal: %s", exc, exc_info=True)
        raise self.retry(exc=exc)


# ── HAS API interaction ────────────────────────────────────────────────────────

def _fetch_has_metadata(base_url: str, mode: str) -> list[dict]:
    """Fetch HAS document list from the HAS API.

    Returns list of metadata dicts: {titre, pathologie, has_grade, annee, url_pdf, hash}.
    Falls back to empty list if API is unavailable (non-fatal for monthly job).
    """
    try:
        import urllib.request
        import json

        # HAS public search API — returns paginated results
        doc_types = "fiches-memo" if mode == "memo-only" else "recommandations,fiches-memo"
        api_url = f"{base_url}/search?type={doc_types}&limit=500&format=json"

        with urllib.request.urlopen(api_url, timeout=30) as resp:  # noqa: S310
            data = json.loads(resp.read())

        docs = []
        for item in data.get("results", []):
            docs.append({
                "titre": item.get("titre", ""),
                "pathologie": item.get("pathologie", ""),
                "has_grade": item.get("grade", ""),
                "annee": str(item.get("annee", ""))[:4],
                "url_pdf": item.get("url_pdf", ""),
                "specialite": item.get("specialite", ""),
            })
        return docs

    except Exception as exc:
        log.warning("[sync_has] API fetch failed: %s — returning empty list", exc)
        return []


def _process_has_document(meta: dict) -> str:
    """Download PDF, extract text, chunk, embed, upsert. Returns 'new'/'updated'/'skipped'."""
    url_pdf = meta.get("url_pdf", "")
    if not url_pdf:
        return "skipped"

    # Download PDF
    try:
        import urllib.request
        with urllib.request.urlopen(url_pdf, timeout=60) as resp:  # noqa: S310
            pdf_bytes = resp.read()
    except Exception as exc:
        log.warning("[sync_has] Download failed for %s: %s", url_pdf, exc)
        return "skipped"

    content_hash = hashlib.sha256(pdf_bytes).hexdigest()

    # Check existing document
    existing_id, existing_hash = _find_has_doc(url_pdf)
    if existing_id and existing_hash == content_hash:
        return "skipped"

    # Extract text
    text = _extract_pdf_text(pdf_bytes)
    if not text.strip():
        return "skipped"

    # Chunk
    from app.jobs.index_document import _semantic_chunk
    chunks = _semantic_chunk(text)

    # Upsert document + chunks
    doc_id = _upsert_has_document(
        existing_id=existing_id,
        url_source=url_pdf,
        filename=url_pdf.split("/")[-1] or "has_doc.pdf",
        content_hash=content_hash,
        content_raw=text[:200_000],
        meta=meta,
    )

    _upsert_has_chunks(doc_id=doc_id, chunks=chunks, meta=meta)

    return "new" if not existing_id else "updated"


# ── PDF extraction ────────────────────────────────────────────────────────────

def _extract_pdf_text(pdf_bytes: bytes) -> str:
    """Extract text from PDF bytes using pdfplumber."""
    import pdfplumber  # type: ignore[import]

    pages: list[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            if text.strip():
                pages.append(f"[Page {i + 1}]\n{text}")

    return "\n\n".join(pages)


# ── DB operations ─────────────────────────────────────────────────────────────

def _find_has_doc(url_source: str) -> tuple[str | None, str | None]:
    """Return (doc_id, content_hash) for existing doc with this URL, or (None, None)."""
    try:
        import psycopg2  # type: ignore[import]
        conn = psycopg2.connect(_db_dsn())
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, content_hash FROM document "
                "WHERE url_source = %s AND deprecated = FALSE LIMIT 1",
                (url_source,),
            )
            row = cur.fetchone()
        conn.close()
        if row:
            return str(row[0]), row[1]
        return None, None
    except Exception:
        return None, None


def _upsert_has_document(
    existing_id: str | None,
    url_source: str,
    filename: str,
    content_hash: str,
    content_raw: str,
    meta: dict,
) -> str:
    import psycopg2  # type: ignore[import]

    conn = psycopg2.connect(_db_dsn())
    try:
        with conn.cursor() as cur:
            if existing_id:
                cur.execute(
                    """
                    UPDATE document
                    SET content_hash = %s, content_raw = %s, deprecated = FALSE
                    WHERE id = %s
                    """,
                    (content_hash, content_raw, uuid.UUID(existing_id)),
                )
                conn.commit()
                return existing_id

            doc_id = str(uuid.uuid4())
            cur.execute(
                """
                INSERT INTO document
                    (id, type, source, filename, content_hash, content_raw,
                     pathologie, specialite, annee, url_source, deprecated, uploaded_at)
                VALUES
                    (%s, 'global', 'has', %s, %s, %s, %s, %s, %s, %s, FALSE, NOW())
                """,
                (
                    uuid.UUID(doc_id),
                    filename,
                    content_hash,
                    content_raw,
                    meta.get("pathologie", ""),
                    meta.get("specialite", ""),
                    meta.get("annee", ""),
                    url_source,
                ),
            )
            conn.commit()
            return doc_id
    finally:
        conn.close()


def _upsert_has_chunks(doc_id: str, chunks: list[str], meta: dict) -> None:
    """Delete old chunks for this doc, insert new ones with embeddings."""
    import json
    import numpy as np
    import psycopg2  # type: ignore[import]
    from psycopg2.extras import register_vector  # type: ignore[import]
    from ia.embedding.service import get_embedding_service

    service = get_embedding_service()
    conn = psycopg2.connect(_db_dsn())

    try:
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM chunk WHERE document_id = %s", (uuid.UUID(doc_id),))
        conn.commit()

        batch_size = 32
        for batch_start in range(0, len(chunks), batch_size):
            batch = chunks[batch_start: batch_start + batch_size]
            vectors = service.embed(batch)

            with conn.cursor() as cur:
                for idx, (text, vec) in enumerate(zip(batch, vectors)):
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
                            text,
                            batch_start + idx,
                            json.dumps({
                                "pathologie": meta.get("pathologie", ""),
                                "has_grade": meta.get("has_grade", ""),
                                "annee": meta.get("annee", ""),
                                "specialite": meta.get("specialite", ""),
                                "titre": meta.get("titre", ""),
                            }),
                            np.array(vec, dtype=np.float32),
                        ),
                    )
            conn.commit()
    finally:
        conn.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _db_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("+asyncpg", "").replace("postgresql+asyncpg", "postgresql")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
