"""PatientVectorStore — NS4 vector search with mandatory isolation filters.

SECURITY INVARIANT:
    Every search through this class injects patient_id + cabinet_id as hard filters.
    It is architecturally impossible to call this store without those filters —
    there is no overload or bypass method.

    Direct queries against the `chunks` table with source='patient_history' are
    forbidden in application code. Use only this class.

Usage:
    store = PatientVectorStore(db, cabinet_id=uuid, patient_id=uuid)
    results = await store.search(query_vector, top_k=20)
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

_COSINE_THRESHOLD = 0.65   # Minimum similarity to include a chunk


@dataclass(frozen=True, slots=True)
class VectorHit:
    chunk_id: uuid.UUID
    content: str
    score: float          # cosine similarity [0, 1]
    source: str
    metadata: dict


class PatientVectorStore:
    """Dense vector search restricted to a single patient within a cabinet.

    Args:
        db:         AsyncSession — must be inside an RLS context (rls.rls_context).
        cabinet_id: UUID of the cabinet (RLS outer fence).
        patient_id: UUID of the specific patient (application-level inner fence).
    """

    def __init__(
        self,
        db: AsyncSession,
        *,
        cabinet_id: uuid.UUID,
        patient_id: uuid.UUID,
    ) -> None:
        self._db = db
        self._cabinet_id = cabinet_id
        self._patient_id = patient_id

    async def search(
        self,
        query_vector: list[float],
        *,
        top_k: int = 20,
        threshold: float = _COSINE_THRESHOLD,
    ) -> list[VectorHit]:
        """Cosine-similarity search over NS4 chunks for this patient.

        Args:
            query_vector: 768-dim embedding of the enriched query.
            top_k:        Maximum results to return.
            threshold:    Minimum cosine similarity.

        Returns:
            List of VectorHit sorted by descending score.
        """
        # Build the pgvector query. The WHERE clause double-locks with both
        # RLS (set via rls_context) and explicit application-level filters.
        stmt = text(
            """
            SELECT
                id,
                content,
                1 - (embedding <=> CAST(:vec AS vector)) AS score,
                source,
                metadata
            FROM  chunks
            WHERE source      = 'patient_history'
              AND patient_id  = :patient_id
              AND cabinet_id  = :cabinet_id
              AND embedding   IS NOT NULL
              AND 1 - (embedding <=> CAST(:vec AS vector)) >= :threshold
            ORDER BY embedding <=> CAST(:vec AS vector)
            LIMIT :top_k
            """
        )

        result = await self._db.execute(
            stmt,
            {
                "vec": str(query_vector),
                "patient_id": str(self._patient_id),
                "cabinet_id": str(self._cabinet_id),
                "threshold": threshold,
                "top_k": top_k,
            },
        )

        hits: list[VectorHit] = []
        for row in result.fetchall():
            hits.append(VectorHit(
                chunk_id=uuid.UUID(str(row.id)),
                content=row.content,
                score=float(row.score),
                source=row.source,
                metadata=row.metadata or {},
            ))

        log.debug(
            "[PatientStore] patient=%s cabinet=%s → %d hits",
            self._patient_id,
            self._cabinet_id,
            len(hits),
        )
        return hits

    async def upsert_chunk(
        self,
        *,
        chunk_id: uuid.UUID,
        content: str,
        embedding: list[float],
        metadata: dict,
    ) -> None:
        """Insert or update a patient chunk. Enforces source='patient_history'.

        Called by patient_indexer after each SOAP validation.
        Content must already be AES-256-GCM encrypted before calling this method.
        Embedding must be computed on-premise (never via external API).
        """
        stmt = text(
            """
            INSERT INTO chunks (id, source, content, embedding, metadata)
            VALUES (
                :id,
                'patient_history',
                :content,
                CAST(:embedding AS vector),
                :metadata::jsonb
            )
            ON CONFLICT (id) DO UPDATE
              SET content   = EXCLUDED.content,
                  embedding = EXCLUDED.embedding,
                  metadata  = EXCLUDED.metadata
            """
        )
        meta = {
            **metadata,
            "patient_id": str(self._patient_id),
            "cabinet_id": str(self._cabinet_id),
        }
        import json
        await self._db.execute(
            stmt,
            {
                "id": str(chunk_id),
                "content": content,
                "embedding": str(embedding),
                "metadata": json.dumps(meta),
            },
        )
        log.debug("[PatientStore] Upserted chunk %s for patient %s", chunk_id, self._patient_id)
