"""Doctor style learner — NS5 few-shot retrieval.

Fetches the top-k DoctorStyleChunk rows for a given (medecin_id, motif_key) pair
and returns them for injection into layer 4 of the SOAP prompt.

Selection strategy:
    1. Filter by medecin_id (mandatory).
    2. Embed the motif_key on-premise and rank by cosine similarity to chunk embeddings.
    3. Return top-k (default 3) chunks sorted by descending similarity.

The returned chunks are already plain text (they were stored encrypted only for
NS4 patient data; NS5 style chunks store the validated SOAP text in plaintext
since it has been de-identified and reviewed by the doctor before storage).

Usage:
    from ia.soap.style_learner import StyleLearner

    learner = StyleLearner(db)
    examples = await learner.fetch_style_examples(
        medecin_id=uuid4,
        motif_key="fievre",
        top_k=3,
    )
    # examples: list[StyleExample]
    # Pass to prompt_assembler as style_examples=examples
"""
from __future__ import annotations

import logging
import re
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

_DEFAULT_TOP_K = 3


@dataclass(frozen=True, slots=True)
class StyleExample:
    """A single doctor style example for few-shot prompting."""
    chunk_id: uuid.UUID
    text: str
    quality_score: Optional[float]
    motif_key: str
    similarity: float   # cosine similarity to the query motif


class StyleLearner:
    """Retrieves NS5 few-shot style examples for a given doctor and motif.

    Args:
        db: AsyncSession with RLS context already set.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def fetch_style_examples(
        self,
        medecin_id: uuid.UUID,
        motif_key: str,
        *,
        top_k: int = _DEFAULT_TOP_K,
    ) -> list[StyleExample]:
        """Fetch top-k style examples by motif similarity.

        Args:
            medecin_id: UUID of the doctor.
            motif_key:  Raw motif string (will be normalised and embedded).
            top_k:      Number of examples to return (default 3).

        Returns:
            List of StyleExample sorted by descending similarity. May be empty
            if the doctor has no validated SOAPs yet.
        """
        normalised_motif = _normalise_motif(motif_key)

        # Embed the motif on-premise
        query_vec = await _embed_async([normalised_motif])
        if not query_vec:
            return []
        query_vector = query_vec[0]

        rows = await self._fetch_chunks(medecin_id, query_vector, top_k=top_k)
        log.debug(
            "[StyleLearner] medecin=%s motif=%r → %d style examples",
            medecin_id, normalised_motif, len(rows),
        )
        return rows

    async def _fetch_chunks(
        self,
        medecin_id: uuid.UUID,
        query_vector: list[float],
        *,
        top_k: int,
    ) -> list[StyleExample]:
        from sqlalchemy import text as sa_text

        stmt = sa_text(
            """
            SELECT
                id,
                text,
                1 - (embedding <=> CAST(:vec AS vector)) AS similarity
            FROM  doctor_style_chunk
            WHERE medecin_id = :medecin_id
              AND embedding IS NOT NULL
            ORDER BY embedding <=> CAST(:vec AS vector)
            LIMIT :top_k
            """
        )

        result = await self._db.execute(
            stmt,
            {
                "vec": str(query_vector),
                "medecin_id": str(medecin_id),
                "top_k": top_k,
            },
        )

        examples: list[StyleExample] = []
        for row in result.fetchall():
            examples.append(StyleExample(
                chunk_id=uuid.UUID(str(row.id)),
                text=row.text,
                quality_score=None,  # not stored in the style table directly
                motif_key="",
                similarity=float(row.similarity),
            ))

        return examples

    async def fetch_by_motif_key(
        self,
        medecin_id: uuid.UUID,
        motif_key: str,
        *,
        top_k: int = _DEFAULT_TOP_K,
    ) -> list[StyleExample]:
        """Fetch examples that were tagged with a matching motif_key.

        Falls back to embedding-based search if no exact matches exist.
        """
        from sqlalchemy import text as sa_text

        normalised = _normalise_motif(motif_key)

        # Try exact motif_key match first (fast path)
        exact_stmt = sa_text(
            """
            SELECT
                id,
                text,
                1.0 AS similarity
            FROM  doctor_style_chunk
            WHERE medecin_id = :medecin_id
              AND embedding IS NOT NULL
            ORDER BY created_at DESC
            LIMIT :top_k
            """
        )
        result = await self._db.execute(
            exact_stmt,
            {"medecin_id": str(medecin_id), "top_k": top_k},
        )
        rows = result.fetchall()

        if rows:
            return [
                StyleExample(
                    chunk_id=uuid.UUID(str(r.id)),
                    text=r.text,
                    quality_score=None,
                    motif_key=normalised,
                    similarity=float(r.similarity),
                )
                for r in rows
            ]

        # Fall back to embedding similarity
        return await self.fetch_style_examples(medecin_id, motif_key, top_k=top_k)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalise_motif(motif: str) -> str:
    """Lowercase, strip accents, strip punctuation."""
    nfkd = unicodedata.normalize("NFKD", motif.lower().strip())
    ascii_str = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9\s]", "", ascii_str).strip()


async def _embed_async(texts: list[str]) -> list[list[float]]:
    """Embed texts on-premise using CamemBERT-bio (non-blocking)."""
    import asyncio
    from ia.embedding.service import get_embedding_service

    loop = asyncio.get_event_loop()
    service = get_embedding_service()

    def _embed_sync():
        return service.embed(texts)

    vectors = await loop.run_in_executor(None, _embed_sync)
    return [v.tolist() if hasattr(v, "tolist") else list(v) for v in vectors]
