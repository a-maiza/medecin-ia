"""Cross-encoder reranker: top-12 candidate passages → relevance scores.

Model: cross-encoder/mmarco-mMiniLMv2-L12-H384-v1
  - Multilingual (includes French)
  - 12-layer MiniLM, fast enough for real-time RAG (< 100 ms for 12 passages on CPU)

The reranker scores (query, passage) pairs jointly — much more accurate than
bi-encoder cosine similarity for ranking, but too slow for first-stage retrieval.
Used in the pipeline AFTER the hybrid retriever (HNSW + BM25 → top-20), to
promote the most relevant chunks to top-12 before medical boosting and MMR.

Usage:
    scores = rerank("douleur thoracique irradiant au bras gauche", passages)
    sorted_pairs = sorted(zip(scores, passages), reverse=True)
"""
from __future__ import annotations

import asyncio
import logging
from functools import lru_cache
from typing import Optional

log = logging.getLogger(__name__)

_MODEL_NAME = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
_MAX_PASSAGES = 20   # hard cap to prevent OOM on large batches


@lru_cache(maxsize=1)
def _get_cross_encoder():
    """Load and cache the cross-encoder model (once per process)."""
    from sentence_transformers import CrossEncoder  # type: ignore[import]

    log.info("Loading cross-encoder %s…", _MODEL_NAME)
    model = CrossEncoder(_MODEL_NAME, max_length=512)
    log.info("Cross-encoder ready.")
    return model


# ── Public API ─────────────────────────────────────────────────────────────────

def rerank(query: str, passages: list[str]) -> list[float]:
    """Score each (query, passage) pair. Returns scores in the same order as passages.

    Higher score = more relevant.
    Input is automatically capped at _MAX_PASSAGES to prevent OOM.

    Args:
        query:    The clinical question or SOAP-generation context.
        passages: Candidate passage texts (already retrieved by hybrid_search).

    Returns:
        list[float] of length len(passages), same order, not sorted.
    """
    if not passages:
        return []

    passages = passages[:_MAX_PASSAGES]
    model = _get_cross_encoder()

    pairs = [(query, p) for p in passages]
    scores: list[float] = model.predict(pairs, show_progress_bar=False).tolist()
    return scores


async def rerank_async(query: str, passages: list[str]) -> list[float]:
    """Async wrapper — runs rerank() in the default thread executor."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, rerank, query, passages)


def rerank_and_sort(
    query: str,
    passages: list[str],
    *,
    top_k: int = 12,
) -> list[tuple[float, str]]:
    """Rerank and return the top-k (score, passage) pairs sorted by descending score.

    Convenience wrapper used by the RAG pipeline.
    """
    scores = rerank(query, passages)
    ranked = sorted(zip(scores, passages), key=lambda x: x[0], reverse=True)
    return ranked[:top_k]


async def rerank_and_sort_async(
    query: str,
    passages: list[str],
    *,
    top_k: int = 12,
) -> list[tuple[float, str]]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, rerank_and_sort, query, passages, top_k)
