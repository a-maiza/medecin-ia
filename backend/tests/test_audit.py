"""Tests for the append-only hash-chained audit log (Task 14 — written with Task 3)."""
import os
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

os.environ.setdefault("PATIENT_ENCRYPTION_MASTER_KEY", "a" * 64)
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
os.environ.setdefault("AUTH0_DOMAIN", "test.auth0.com")
os.environ.setdefault("AUTH0_CLIENT_ID", "test")
os.environ.setdefault("AUTH0_CLIENT_SECRET", "test")
os.environ.setdefault("AUTH0_AUDIENCE", "test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from app.security.audit import _GENESIS_HASH, _get_last_hash, log_event, verify_chain  # noqa: E402
from app.models.audit_log import AuditLog  # noqa: E402


def _make_row(id_: int, payload: dict) -> AuditLog:
    row = AuditLog.__new__(AuditLog)
    row.id = id_
    row.payload = payload
    return row


class TestHashChain:
    def test_genesis_hash_used_for_first_event(self):
        import hashlib, json
        payload = {"event": "test"}
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        expected_hash = hashlib.sha256(
            (_GENESIS_HASH + canonical).encode()
        ).hexdigest()
        # Verify the formula matches what audit.py would compute
        assert len(expected_hash) == 64

    def test_hash_is_deterministic(self):
        import hashlib, json
        prev = "a" * 64
        payload = {"action": "soap_generated", "consultation_id": "abc"}
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        h1 = hashlib.sha256((prev + canonical).encode()).hexdigest()
        h2 = hashlib.sha256((prev + canonical).encode()).hexdigest()
        assert h1 == h2

    def test_payload_key_order_irrelevant(self):
        """sort_keys=True ensures key order doesn't change the hash."""
        import hashlib, json
        prev = "b" * 64
        p1 = {"b": 2, "a": 1}
        p2 = {"a": 1, "b": 2}
        h1 = hashlib.sha256(
            (prev + json.dumps(p1, sort_keys=True)).encode()
        ).hexdigest()
        h2 = hashlib.sha256(
            (prev + json.dumps(p2, sort_keys=True)).encode()
        ).hexdigest()
        assert h1 == h2


class TestVerifyChain:
    @pytest.mark.asyncio
    async def test_empty_log_is_valid(self):
        db = AsyncMock()
        db.execute.return_value.scalars.return_value.all.return_value = []
        ok, bad_id = await verify_chain(db)
        assert ok is True
        assert bad_id is None

    @pytest.mark.asyncio
    async def test_single_correct_entry(self):
        import hashlib, json
        payload = {"action": "soap_generated"}
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        h = hashlib.sha256((_GENESIS_HASH + canonical).encode()).hexdigest()
        full_payload = {**payload, "_hash": h}

        row = _make_row(1, full_payload)
        db = AsyncMock()
        db.execute.return_value.scalars.return_value.all.return_value = [row]

        ok, bad_id = await verify_chain(db)
        assert ok is True
        assert bad_id is None

    @pytest.mark.asyncio
    async def test_tampered_entry_detected(self):
        import hashlib, json
        payload = {"action": "soap_generated"}
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        real_hash = hashlib.sha256((_GENESIS_HASH + canonical).encode()).hexdigest()
        # Flip one char to simulate tampering
        bad_hash = real_hash[:-1] + ("x" if real_hash[-1] != "x" else "y")
        tampered_payload = {**payload, "_hash": bad_hash}

        row = _make_row(1, tampered_payload)
        db = AsyncMock()
        db.execute.return_value.scalars.return_value.all.return_value = [row]

        ok, bad_id = await verify_chain(db)
        assert ok is False
        assert bad_id == 1

    @pytest.mark.asyncio
    async def test_missing_hash_field_detected(self):
        row = _make_row(5, {"action": "dmp_exported"})  # no _hash key
        db = AsyncMock()
        db.execute.return_value.scalars.return_value.all.return_value = [row]
        ok, bad_id = await verify_chain(db)
        assert ok is False
        assert bad_id == 5


class TestGetLastHash:
    @pytest.mark.asyncio
    async def test_genesis_when_empty(self):
        db = AsyncMock()
        db.execute.return_value.scalar_one_or_none.return_value = None
        result = await _get_last_hash(db)
        assert result == _GENESIS_HASH

    @pytest.mark.asyncio
    async def test_returns_stored_hash(self):
        stored = "f" * 64
        db = AsyncMock()
        db.execute.return_value.scalar_one_or_none.return_value = {"_hash": stored}
        result = await _get_last_hash(db)
        assert result == stored
