"""BM25Okapi sparse index over all non-patient chunks.

Architecture:
  - Index is built from the `chunks` table (all sources except patient_history).
  - Serialised to disk as a pickle file and loaded on startup for fast in-memory search.
  - Rebuilt nightly via Celery beat task `rebuild_bm25_index`.

Usage:
    index = get_bm25_index()
    results = index.search("douleur thoracique", top_k=20)
    # → [(chunk_id, score), ...]
"""
from __future__ import annotations

import logging
import os
import pickle
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_INDEX_PATH = Path(os.environ.get("BM25_INDEX_PATH", "/tmp/bm25_index.pkl"))


# ── Tokenizer ─────────────────────────────────────────────────────────────────

_STOP_WORDS_FR = frozenset({
    "le", "la", "les", "un", "une", "des", "de", "du", "et", "en", "à",
    "au", "aux", "ce", "se", "si", "ne", "pas", "par", "sur", "sous",
    "avec", "dans", "pour", "que", "qui", "est", "son", "sa", "ses",
    "il", "elle", "ils", "elles", "nous", "vous", "on", "je", "tu",
    "ou", "mais", "car", "ni", "or", "donc", "or",
})


def _tokenize(text: str) -> list[str]:
    """Lowercase, remove accents, split on non-alphanum, drop stop words."""
    # Normalise accents: é→e, ç→c, etc.
    nfd = unicodedata.normalize("NFD", text.lower())
    ascii_text = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    tokens = re.findall(r"[a-z0-9]+", ascii_text)
    return [t for t in tokens if len(t) > 1 and t not in _STOP_WORDS_FR]


# ── Index container ───────────────────────────────────────────────────────────

@dataclass
class BM25Index:
    """Wraps a rank_bm25 BM25Okapi model with chunk_id ↔ row mapping."""

    model: object          # BM25Okapi instance
    chunk_ids: list[str]   # parallel list: chunk_ids[i] ↔ model corpus[i]
    built_at: str          # ISO timestamp

    def search(self, query: str, top_k: int = 20) -> list[tuple[str, float]]:
        """Search the index. Returns [(chunk_id, score)] sorted descending.

        Scores are BM25 tf-idf-like values (not bounded). Zero-score docs are excluded.
        """
        from rank_bm25 import BM25Okapi  # type: ignore[import]

        tokens = _tokenize(query)
        if not tokens:
            return []

        scores: list[float] = self.model.get_scores(tokens).tolist()

        # Pair with ids, filter zero scores, sort descending, take top_k
        ranked = sorted(
            ((cid, s) for cid, s in zip(self.chunk_ids, scores) if s > 0.0),
            key=lambda x: x[1],
            reverse=True,
        )
        return ranked[:top_k]


# ── Singleton management ──────────────────────────────────────────────────────

_cached_index: Optional[BM25Index] = None


def get_bm25_index() -> Optional[BM25Index]:
    """Return the in-memory BM25 index, loading from disk if needed.

    Returns None if no index file exists yet (first startup before nightly build).
    """
    global _cached_index
    if _cached_index is not None:
        return _cached_index

    if not _INDEX_PATH.exists():
        log.warning("[BM25] Index file not found at %s — sparse search disabled", _INDEX_PATH)
        return None

    log.info("[BM25] Loading index from %s…", _INDEX_PATH)
    with open(_INDEX_PATH, "rb") as f:
        _cached_index = pickle.load(f)
    log.info("[BM25] Index loaded: %d chunks, built at %s", len(_cached_index.chunk_ids), _cached_index.built_at)
    return _cached_index


def invalidate_bm25_cache() -> None:
    """Force reload on next call to get_bm25_index()."""
    global _cached_index
    _cached_index = None


# ── Build function (called by Celery nightly job) ─────────────────────────────

def build_and_save_index(db_url: str) -> BM25Index:
    """Fetch all non-patient chunks from PostgreSQL, build BM25Okapi, save to disk.

    This is the expensive operation (~minutes for 420 000 chunks). Run overnight.

    Args:
        db_url: Sync SQLAlchemy URL (postgresql://...).

    Returns:
        The freshly built BM25Index.
    """
    import datetime
    import psycopg2  # type: ignore[import]
    from rank_bm25 import BM25Okapi  # type: ignore[import]

    log.info("[BM25] Starting index build…")

    dsn = db_url.replace("+asyncpg", "").replace("postgresql+asyncpg", "postgresql")
    conn = psycopg2.connect(dsn)

    chunk_ids: list[str] = []
    corpus: list[list[str]] = []

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id::text, content
                FROM   chunks
                WHERE  source != 'patient_history'
                  AND  content IS NOT NULL
                ORDER  BY id
                """,
            )
            for row in cur:
                chunk_ids.append(row[0])
                corpus.append(_tokenize(row[1]))
    finally:
        conn.close()

    log.info("[BM25] Tokenised %d chunks, fitting BM25Okapi…", len(corpus))
    model = BM25Okapi(corpus)

    index = BM25Index(
        model=model,
        chunk_ids=chunk_ids,
        built_at=datetime.datetime.utcnow().isoformat(),
    )

    _INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_INDEX_PATH, "wb") as f:
        pickle.dump(index, f, protocol=pickle.HIGHEST_PROTOCOL)

    log.info("[BM25] Index saved to %s (%d chunks)", _INDEX_PATH, len(chunk_ids))

    # Invalidate in-memory cache so next call loads the new file
    invalidate_bm25_cache()
    return index
