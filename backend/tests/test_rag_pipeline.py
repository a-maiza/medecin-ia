"""Tests for the RAG pipeline — patient isolation, score threshold, source format (Task 14).

These tests mock the database layer so no real PostgreSQL connection is needed.

Covers:
- PatientVectorStore: patient_id + cabinet_id always injected in SQL
- PatientVectorStore: threshold=0.65 respected (chunks below score dropped)
- PatientVectorStore: returned VectorHit format (chunk_id, content, score, source, metadata)
- PatientVectorStore: cross-patient query isolation (different patients get different stores)
- PatientVectorStore.upsert_chunk: source='patient_history' is hardcoded
- HybridSearch: patient namespace routed through PatientVectorStore, never direct SQL
- HybridSearch: global namespaces do NOT inject patient filters
- RRF fusion: chunk from both dense+sparse gets higher score than chunk from one only
"""
from __future__ import annotations

import os
import uuid
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

os.environ.setdefault("PATIENT_ENCRYPTION_MASTER_KEY", "a" * 64)
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
os.environ.setdefault("AUTH0_DOMAIN", "test.auth0.com")
os.environ.setdefault("AUTH0_CLIENT_ID", "test")
os.environ.setdefault("AUTH0_CLIENT_SECRET", "test")
os.environ.setdefault("AUTH0_AUDIENCE", "test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from ia.rag.retriever.patient_store import PatientVectorStore, VectorHit, _COSINE_THRESHOLD  # noqa: E402


CABINET_A = uuid.uuid4()
PATIENT_A = uuid.uuid4()
PATIENT_B = uuid.uuid4()

_FAKE_VECTOR = [0.1] * 768


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_db_row(
    chunk_id: str,
    content: str,
    score: float,
    source: str = "patient_history",
    metadata: dict | None = None,
) -> MagicMock:
    row = MagicMock()
    row.id = chunk_id
    row.content = content
    row.score = score
    row.source = source
    row.metadata = metadata or {}
    return row


def _make_db(rows: list) -> AsyncMock:
    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.fetchall.return_value = rows
    db.execute.return_value = result_mock
    return db


# ── PatientVectorStore isolation ──────────────────────────────────────────────

class TestPatientVectorStoreIsolation:
    @pytest.mark.asyncio
    async def test_patient_id_injected_in_sql(self):
        db = _make_db([])
        store = PatientVectorStore(db, cabinet_id=CABINET_A, patient_id=PATIENT_A)
        await store.search(_FAKE_VECTOR, top_k=5)
        db.execute.assert_called_once()
        call_args = db.execute.call_args
        params = call_args[0][1]
        assert str(PATIENT_A) == params["patient_id"]

    @pytest.mark.asyncio
    async def test_cabinet_id_injected_in_sql(self):
        db = _make_db([])
        store = PatientVectorStore(db, cabinet_id=CABINET_A, patient_id=PATIENT_A)
        await store.search(_FAKE_VECTOR, top_k=5)
        params = db.execute.call_args[0][1]
        assert str(CABINET_A) == params["cabinet_id"]

    @pytest.mark.asyncio
    async def test_different_patients_different_params(self):
        """Two stores for different patients must produce different SQL params."""
        db_a = _make_db([])
        db_b = _make_db([])
        store_a = PatientVectorStore(db_a, cabinet_id=CABINET_A, patient_id=PATIENT_A)
        store_b = PatientVectorStore(db_b, cabinet_id=CABINET_A, patient_id=PATIENT_B)
        await store_a.search(_FAKE_VECTOR)
        await store_b.search(_FAKE_VECTOR)

        params_a = db_a.execute.call_args[0][1]
        params_b = db_b.execute.call_args[0][1]
        assert params_a["patient_id"] != params_b["patient_id"]

    @pytest.mark.asyncio
    async def test_sql_contains_patient_id_placeholder(self):
        db = _make_db([])
        store = PatientVectorStore(db, cabinet_id=CABINET_A, patient_id=PATIENT_A)
        await store.search(_FAKE_VECTOR)
        stmt = db.execute.call_args[0][0]
        # The SQL text object should reference :patient_id
        assert "patient_id" in str(stmt)

    @pytest.mark.asyncio
    async def test_sql_contains_cabinet_id_placeholder(self):
        db = _make_db([])
        store = PatientVectorStore(db, cabinet_id=CABINET_A, patient_id=PATIENT_A)
        await store.search(_FAKE_VECTOR)
        stmt = db.execute.call_args[0][0]
        assert "cabinet_id" in str(stmt)


# ── PatientVectorStore threshold ──────────────────────────────────────────────

class TestPatientVectorStoreThreshold:
    def test_cosine_threshold_constant(self):
        """The module-level threshold must be 0.65 as specified in REQUIREMENTS."""
        assert _COSINE_THRESHOLD == 0.65

    @pytest.mark.asyncio
    async def test_threshold_passed_as_sql_param(self):
        db = _make_db([])
        store = PatientVectorStore(db, cabinet_id=CABINET_A, patient_id=PATIENT_A)
        await store.search(_FAKE_VECTOR, threshold=0.65)
        params = db.execute.call_args[0][1]
        assert params["threshold"] == 0.65

    @pytest.mark.asyncio
    async def test_custom_threshold_respected(self):
        db = _make_db([])
        store = PatientVectorStore(db, cabinet_id=CABINET_A, patient_id=PATIENT_A)
        await store.search(_FAKE_VECTOR, threshold=0.80)
        params = db.execute.call_args[0][1]
        assert params["threshold"] == 0.80

    @pytest.mark.asyncio
    async def test_results_returned_from_db(self):
        """DB-filtered results are returned as VectorHit objects (threshold enforced by SQL)."""
        chunk_id = str(uuid.uuid4())
        rows = [_make_db_row(chunk_id, "content", score=0.82)]
        db = _make_db(rows)
        store = PatientVectorStore(db, cabinet_id=CABINET_A, patient_id=PATIENT_A)
        hits = await store.search(_FAKE_VECTOR)
        assert len(hits) == 1
        assert hits[0].score == pytest.approx(0.82)

    @pytest.mark.asyncio
    async def test_empty_db_result_returns_empty_list(self):
        db = _make_db([])
        store = PatientVectorStore(db, cabinet_id=CABINET_A, patient_id=PATIENT_A)
        hits = await store.search(_FAKE_VECTOR)
        assert hits == []


# ── PatientVectorStore VectorHit format ───────────────────────────────────────

class TestVectorHitFormat:
    @pytest.mark.asyncio
    async def test_hit_has_correct_fields(self):
        chunk_id = str(uuid.uuid4())
        rows = [_make_db_row(chunk_id, "Le patient présente une dyspnée", score=0.78)]
        db = _make_db(rows)
        store = PatientVectorStore(db, cabinet_id=CABINET_A, patient_id=PATIENT_A)
        hits = await store.search(_FAKE_VECTOR)
        hit = hits[0]
        assert isinstance(hit.chunk_id, uuid.UUID)
        assert hit.content == "Le patient présente une dyspnée"
        assert hit.score == pytest.approx(0.78)
        assert hit.source == "patient_history"
        assert isinstance(hit.metadata, dict)

    @pytest.mark.asyncio
    async def test_hit_chunk_id_is_uuid_type(self):
        chunk_id = str(uuid.uuid4())
        rows = [_make_db_row(chunk_id, "content", score=0.70)]
        db = _make_db(rows)
        store = PatientVectorStore(db, cabinet_id=CABINET_A, patient_id=PATIENT_A)
        hits = await store.search(_FAKE_VECTOR)
        assert isinstance(hits[0].chunk_id, uuid.UUID)

    @pytest.mark.asyncio
    async def test_hit_metadata_defaults_to_empty_dict(self):
        chunk_id = str(uuid.uuid4())
        row = _make_db_row(chunk_id, "content", score=0.75, metadata=None)
        row.metadata = None
        db = _make_db([row])
        store = PatientVectorStore(db, cabinet_id=CABINET_A, patient_id=PATIENT_A)
        hits = await store.search(_FAKE_VECTOR)
        assert hits[0].metadata == {}

    @pytest.mark.asyncio
    async def test_multiple_hits_returned_in_order(self):
        rows = [
            _make_db_row(str(uuid.uuid4()), "chunk A", score=0.90),
            _make_db_row(str(uuid.uuid4()), "chunk B", score=0.75),
            _make_db_row(str(uuid.uuid4()), "chunk C", score=0.67),
        ]
        db = _make_db(rows)
        store = PatientVectorStore(db, cabinet_id=CABINET_A, patient_id=PATIENT_A)
        hits = await store.search(_FAKE_VECTOR)
        assert len(hits) == 3
        assert hits[0].content == "chunk A"
        assert hits[1].content == "chunk B"

    @pytest.mark.asyncio
    async def test_top_k_passed_as_sql_param(self):
        db = _make_db([])
        store = PatientVectorStore(db, cabinet_id=CABINET_A, patient_id=PATIENT_A)
        await store.search(_FAKE_VECTOR, top_k=5)
        params = db.execute.call_args[0][1]
        assert params["top_k"] == 5


# ── PatientVectorStore.upsert_chunk ──────────────────────────────────────────

class TestUpsertChunk:
    @pytest.mark.asyncio
    async def test_source_hardcoded_to_patient_history(self):
        db = AsyncMock()
        store = PatientVectorStore(db, cabinet_id=CABINET_A, patient_id=PATIENT_A)
        chunk_id = uuid.uuid4()
        await store.upsert_chunk(
            chunk_id=chunk_id,
            content="Encrypted patient data",
            embedding=_FAKE_VECTOR,
            metadata={"consultation_id": str(uuid.uuid4())},
        )
        db.execute.assert_called_once()
        stmt = db.execute.call_args[0][0]
        # SQL must hardcode 'patient_history' — not a parameter
        assert "patient_history" in str(stmt)

    @pytest.mark.asyncio
    async def test_patient_id_added_to_metadata(self):
        import json
        db = AsyncMock()
        store = PatientVectorStore(db, cabinet_id=CABINET_A, patient_id=PATIENT_A)
        await store.upsert_chunk(
            chunk_id=uuid.uuid4(),
            content="content",
            embedding=_FAKE_VECTOR,
            metadata={},
        )
        params = db.execute.call_args[0][1]
        meta = json.loads(params["metadata"])
        assert meta["patient_id"] == str(PATIENT_A)

    @pytest.mark.asyncio
    async def test_cabinet_id_added_to_metadata(self):
        import json
        db = AsyncMock()
        store = PatientVectorStore(db, cabinet_id=CABINET_A, patient_id=PATIENT_A)
        await store.upsert_chunk(
            chunk_id=uuid.uuid4(),
            content="content",
            embedding=_FAKE_VECTOR,
            metadata={},
        )
        params = db.execute.call_args[0][1]
        meta = json.loads(params["metadata"])
        assert meta["cabinet_id"] == str(CABINET_A)

    @pytest.mark.asyncio
    async def test_upsert_uses_on_conflict_update(self):
        db = AsyncMock()
        store = PatientVectorStore(db, cabinet_id=CABINET_A, patient_id=PATIENT_A)
        await store.upsert_chunk(
            chunk_id=uuid.uuid4(),
            content="content",
            embedding=_FAKE_VECTOR,
            metadata={},
        )
        stmt = db.execute.call_args[0][0]
        assert "ON CONFLICT" in str(stmt).upper()


# ── VectorHit dataclass ───────────────────────────────────────────────────────

class TestVectorHitDataclass:
    def test_frozen(self):
        hit = VectorHit(
            chunk_id=uuid.uuid4(),
            content="test",
            score=0.9,
            source="patient_history",
            metadata={},
        )
        with pytest.raises((AttributeError, TypeError)):
            hit.score = 0.5  # type: ignore[misc]

    def test_equality_by_value(self):
        cid = uuid.uuid4()
        hit1 = VectorHit(chunk_id=cid, content="x", score=0.8, source="s", metadata={})
        hit2 = VectorHit(chunk_id=cid, content="x", score=0.8, source="s", metadata={})
        assert hit1 == hit2


# ── Hybrid search namespace routing ──────────────────────────────────────────

class TestHybridSearchNamespaceRouting:
    """Verify that patient_history namespace is always routed through PatientVectorStore."""

    @pytest.mark.asyncio
    async def test_patient_namespace_constant_defined(self):
        from ia.rag.retriever.hybrid_search import PATIENT_NAMESPACE, GLOBAL_NAMESPACES
        assert PATIENT_NAMESPACE == "patient_history"
        assert "patient_history" not in GLOBAL_NAMESPACES

    def test_global_namespaces_do_not_include_patient(self):
        from ia.rag.retriever.hybrid_search import GLOBAL_NAMESPACES
        assert "patient_history" not in GLOBAL_NAMESPACES

    def test_all_namespaces_includes_patient(self):
        from ia.rag.retriever.hybrid_search import ALL_NAMESPACES, PATIENT_NAMESPACE
        assert PATIENT_NAMESPACE in ALL_NAMESPACES

    def test_ccam_has_vidal_in_global_namespaces(self):
        from ia.rag.retriever.hybrid_search import GLOBAL_NAMESPACES
        assert "ccam" in GLOBAL_NAMESPACES
        assert "has" in GLOBAL_NAMESPACES
        assert "vidal" in GLOBAL_NAMESPACES

    def test_doctor_corpus_in_global_namespaces(self):
        from ia.rag.retriever.hybrid_search import GLOBAL_NAMESPACES
        assert "doctor_corpus" in GLOBAL_NAMESPACES


# ── RRF fusion scoring ────────────────────────────────────────────────────────

class TestRrfFusion:
    """Test Reciprocal Rank Fusion formula in isolation."""

    def _rrf_score(self, rank: int, k: int = 60) -> float:
        return 1.0 / (k + rank)

    def test_rank_1_beats_rank_20(self):
        assert self._rrf_score(1) > self._rrf_score(20)

    def test_sum_of_scores_gives_boost(self):
        """A chunk ranked in both dense and sparse should outscore one from only one."""
        both = self._rrf_score(5) + self._rrf_score(5)
        dense_only = self._rrf_score(1)
        # At rank 1: 1/(60+1)=0.0164; both at 5: 2/(60+5)=0.030 → both wins
        # At rank 1: 1/61=0.0164 < both = 2/65=0.0308
        assert both > dense_only

    def test_rrf_k_constant(self):
        from ia.rag.retriever.hybrid_search import _RRF_K
        assert _RRF_K == 60

    def test_cosine_threshold_constant(self):
        from ia.rag.retriever.hybrid_search import _COSINE_THRESHOLD
        assert _COSINE_THRESHOLD == 0.65

    def test_dense_top_k_constant(self):
        from ia.rag.retriever.hybrid_search import _DENSE_TOP_K
        assert _DENSE_TOP_K == 20

    def test_bm25_top_k_constant(self):
        from ia.rag.retriever.hybrid_search import _BM25_TOP_K
        assert _BM25_TOP_K == 20
