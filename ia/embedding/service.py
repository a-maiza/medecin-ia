"""CamemBERT-bio embedding service — 768-dimensional vectors.

Model: almanach/camembert-bio (MVP)
       DrBERT/DrBERT-7GB-cased (V1 — see migration notes below)

The model is loaded lazily on first call and cached for the process lifetime.
Batch size is chosen adaptively based on available VRAM to maximise GPU throughput.

CRITICAL: All patient text (NS4) MUST be embedded on-premise via this service.
          Never send patient text to an external embedding API.

Migration to DrBERT-7GB-cased (P1):
  1. Replace MODEL_NAME env var with "DrBERT/DrBERT-7GB-cased"
  2. Embedding dimension is still 768 — no schema change needed
  3. Schedule re-indexing job via: celery -A app.worker call kb.tasks.reindex_all_chunks
  4. Run both models in parallel during transition (A/B test quality score)
"""
from __future__ import annotations

import asyncio
import logging
import os
from functools import lru_cache
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

_DEFAULT_MODEL = "almanach/camembert-bio"
_EMBEDDING_DIM = 768


def _detect_batch_size() -> int:
    """Choose batch size based on available GPU VRAM (falls back to CPU batch size)."""
    try:
        import torch  # type: ignore[import]
        if not torch.cuda.is_available():
            return 8
        vram_gb = torch.cuda.get_device_properties(0).total_memory / 1e9
        if vram_gb >= 24:
            return 128
        elif vram_gb >= 16:
            return 64
        elif vram_gb >= 8:
            return 32
        elif vram_gb >= 4:
            return 16
        return 8
    except Exception:
        return 8


@lru_cache(maxsize=1)
def _get_model_and_batch():
    """Load sentence-transformer model and detect optimal batch size (runs once)."""
    from sentence_transformers import SentenceTransformer  # type: ignore[import]

    model_name = os.environ.get("EMBEDDING_MODEL_NAME", _DEFAULT_MODEL)
    log.info("Loading embedding model %s…", model_name)
    model = SentenceTransformer(model_name)
    batch_size = _detect_batch_size()
    log.info("Embedding model ready — batch_size=%d", batch_size)
    return model, batch_size


# ── Public API ─────────────────────────────────────────────────────────────────

def embed(texts: list[str]) -> np.ndarray:
    """Embed a list of texts synchronously.

    Returns shape (N, 768) float32 array, L2-normalised (unit vectors).
    Chunking into batches is handled internally.

    For async callers use embed_async().
    """
    if not texts:
        return np.empty((0, _EMBEDDING_DIM), dtype=np.float32)

    model, batch_size = _get_model_and_batch()

    # sentence-transformers handles batching internally when batch_size is passed
    embeddings: np.ndarray = model.encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=True,   # L2-normalise → cosine similarity = dot product
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    return embeddings.astype(np.float32)


async def embed_async(texts: list[str]) -> np.ndarray:
    """Async wrapper — runs embed() in the default thread executor."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, embed, texts)


def embed_one(text: str) -> np.ndarray:
    """Convenience helper for a single text. Returns shape (768,)."""
    return embed([text])[0]


def embedding_dim() -> int:
    """Return the embedding dimension (768 for both CamemBERT-bio and DrBERT)."""
    return _EMBEDDING_DIM
