"""CCAM indexer — NS1.

Downloads the ATIH CCAM file (CSV/TSV), parses it, embeds each code entry with
CamemBERT-bio, and upserts into the `chunk` table (namespace='ccam').

Delta detection: SHA-256 of the downloaded file — skips full re-index if unchanged.

Usage (standalone, called from sync_ccam Celery job or directly):
    from ia.rag.indexer.ccam_indexer import CcamIndexer
    stats = CcamIndexer().index_from_url("https://...")

    # Or from pre-downloaded bytes:
    stats = CcamIndexer().index_from_bytes(raw_bytes, doc_id="uuid-string")
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

# Batch size for embedding calls (GPU VRAM-limited)
_EMBED_BATCH = 64


@dataclass
class IndexStats:
    upserted: int = 0
    skipped: int = 0
    errors: int = 0
    content_hash: str = ""
    delta: bool = True
    extra: dict = field(default_factory=dict)


class CcamIndexer:
    """Indexes ATIH CCAM nomenclature into the `chunk` table (namespace='ccam').

    Each CCAM code becomes one chunk:
        text     = "{code} — {libelle_long}. {notes}"
        metadata = {code, chapter, specialty: null, has_grade: null}
    """

    def index_from_url(self, url: str, doc_id: str | None = None) -> IndexStats:
        """Download CCAM file, detect delta, and index."""
        log.info("[CcamIndexer] Downloading from %s", url)
        raw = _download(url)
        return self.index_from_bytes(raw, doc_id=doc_id)

    def index_from_bytes(self, raw: bytes, doc_id: str | None = None) -> IndexStats:
        """Parse and index CCAM from raw bytes.

        Args:
            raw:    Raw file bytes (CSV/TSV, UTF-8 or latin-1).
            doc_id: Existing document UUID to associate chunks with.
                    If None, a new global document row is created.
        """
        content_hash = hashlib.sha256(raw).hexdigest()

        if _hash_unchanged(content_hash, "ccam"):
            log.info("[CcamIndexer] Hash unchanged — skipping")
            return IndexStats(content_hash=content_hash, delta=False)

        rows = _parse_ccam(raw)
        log.info("[CcamIndexer] Parsed %d CCAM codes", len(rows))

        if doc_id is None:
            doc_id = _ensure_global_document(
                source="ccam",
                filename=f"ccam_{content_hash[:8]}.csv",
                content_hash=content_hash,
            )

        stats = self._upsert(doc_id, rows)
        stats.content_hash = content_hash
        stats.delta = True
        log.info("[CcamIndexer] Done: %s", stats)
        return stats

    def _upsert(self, doc_id: str, rows: list[dict]) -> IndexStats:
        import numpy as np
        import psycopg2  # type: ignore[import]
        from psycopg2.extras import register_vector  # type: ignore[import]
        from ia.embedding.service import get_embedding_service

        service = get_embedding_service()
        conn = psycopg2.connect(_db_dsn())
        stats = IndexStats()

        try:
            register_vector(conn)

            # Clear previous CCAM chunks for this document
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM chunk WHERE document_id = %s AND namespace = 'ccam'",
                    (uuid.UUID(doc_id),),
                )
            conn.commit()

            for batch_start in range(0, len(rows), _EMBED_BATCH):
                batch = rows[batch_start: batch_start + _EMBED_BATCH]
                texts = [r["text"] for r in batch]

                try:
                    vectors = service.embed(texts)
                except Exception as exc:
                    log.error("[CcamIndexer] Embed failed at offset %d: %s", batch_start, exc)
                    stats.errors += len(batch)
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
                                        "has_grade": None,
                                    }),
                                    np.array(vec, dtype=np.float32),
                                ),
                            )
                            stats.upserted += 1
                        except Exception as exc:
                            log.warning("[CcamIndexer] Insert failed for %s: %s", row["code"], exc)
                            stats.errors += 1

                conn.commit()
                log.debug("[CcamIndexer] Progress: %d/%d", batch_start + len(batch), len(rows))

        finally:
            conn.close()

        return stats


# ── Parsing ───────────────────────────────────────────────────────────────────

def _parse_ccam(raw: bytes) -> list[dict[str, str]]:
    """Parse ATIH CCAM CSV/TSV export.

    Expected columns (tab-separated):
        CODE | LIBELLE_COURT | LIBELLE_LONG | CHAPITRE | NOTES
    Fallback to semicolon-separated if tab yields only 1 column.
    """
    try:
        text = raw.decode("utf-8-sig", errors="replace")
    except Exception:
        text = raw.decode("latin-1", errors="replace")

    # Auto-detect delimiter
    delimiter = "\t"
    first_line = text.split("\n", 1)[0]
    if first_line.count(";") > first_line.count("\t"):
        delimiter = ";"

    rows: list[dict[str, str]] = []
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)

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
    """Return the ID of an existing or newly created global Document row."""
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


def _download(url: str) -> bytes:
    import urllib.request
    with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310
        return resp.read()
