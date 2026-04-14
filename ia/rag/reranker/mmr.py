"""Maximal Marginal Relevance (MMR) for result deduplication.

MMR balances relevance vs diversity:
    MMR(d) = λ × sim(d, query) − (1−λ) × max_{d' ∈ selected} sim(d, d')

Parameters (from REQUIREMENTS.md §6):
    lambda = 0.65   → more weight on relevance than diversity
    top_k  = 5      → final set size

Usage:
    final = mmr(candidates, query_vector, top_k=5, lmbda=0.65)
"""
from __future__ import annotations

import math
import logging
from typing import Union

from ia.rag.reranker.medical_booster import BoostedPassage
from ia.rag.reranker.cross_encoder import RankedPassage

log = logging.getLogger(__name__)

_DEFAULT_LAMBDA = 0.65
_DEFAULT_TOP_K = 5

# Accept either BoostedPassage or RankedPassage
Passage = Union[BoostedPassage, RankedPassage]


def _cosine(a: list[float], b: list[float]) -> float:
    """Fast cosine similarity between two float vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def mmr(
    candidates: list[Passage],
    query_vector: list[float],
    candidate_vectors: list[list[float]],
    *,
    top_k: int = _DEFAULT_TOP_K,
    lmbda: float = _DEFAULT_LAMBDA,
) -> list[Passage]:
    """Select top_k passages using Maximal Marginal Relevance.

    Args:
        candidates:         Sorted list of passage objects (BoostedPassage or RankedPassage).
        query_vector:       768-dim embedding of the enriched query.
        candidate_vectors:  Parallel list of embeddings for each candidate.
        top_k:              Maximum results to return.
        lmbda:              Trade-off parameter [0=max diversity, 1=max relevance].

    Returns:
        Subset of candidates selected for maximum marginal relevance (top_k at most).
    """
    if not candidates:
        return []

    if len(candidates) <= top_k:
        return list(candidates)

    # Pre-compute query similarities
    query_sims: list[float] = [_cosine(query_vector, vec) for vec in candidate_vectors]

    selected_indices: list[int] = []
    remaining: list[int] = list(range(len(candidates)))

    while len(selected_indices) < top_k and remaining:
        best_idx = -1
        best_score = float("-inf")

        for i in remaining:
            # Relevance term
            relevance = lmbda * query_sims[i]

            # Diversity term: max similarity to already selected
            if selected_indices:
                max_sim_to_selected = max(
                    _cosine(candidate_vectors[i], candidate_vectors[j])
                    for j in selected_indices
                )
            else:
                max_sim_to_selected = 0.0

            diversity = (1 - lmbda) * max_sim_to_selected
            mmr_score = relevance - diversity

            if mmr_score > best_score:
                best_score = mmr_score
                best_idx = i

        selected_indices.append(best_idx)
        remaining.remove(best_idx)

    result = [candidates[i] for i in selected_indices]
    log.debug("[MMR] Selected %d/%d candidates (λ=%.2f)", len(result), len(candidates), lmbda)
    return result


def mmr_texts_only(
    texts: list[str],
    scores: list[float],
    query_vector: list[float],
    *,
    top_k: int = _DEFAULT_TOP_K,
    lmbda: float = _DEFAULT_LAMBDA,
) -> list[tuple[float, str]]:
    """Convenience wrapper when you only have text + scores (no passage objects).

    Computes embeddings on-the-fly for MMR diversity calculation.
    Returns [(score, text)] sorted by selection order.
    """
    from ia.embedding.service import get_embedding_service

    if not texts:
        return []

    svc = get_embedding_service()
    vectors = svc.embed(texts)

    # Build fake RankedPassage-like objects for mmr()
    from dataclasses import dataclass

    @dataclass
    class _Hit:
        text: str
        score: float

    hits = [_Hit(text=t, score=s) for t, s in zip(texts, scores)]
    selected = mmr(hits, query_vector, vectors, top_k=top_k, lmbda=lmbda)

    return [(h.score, h.text) for h in selected]
