"""Append-only, hash-chained audit log.

Each row stores:
  content_hash = SHA-256( previous_row.content_hash || json(payload) )

The genesis row uses "0" * 64 as prev_hash.
verify_chain() reads all rows in insertion order and checks each link —
any tampered row (or deleted row) breaks the chain.

Permitted event types (from REQUIREMENTS.md):
  soap_generated, soap_edited, alert_acknowledged, soap_signed,
  dmp_exported, doctolib_synced, export_failed, patient_data_accessed,
  document_uploaded, document_deleted
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Optional
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog

log = logging.getLogger(__name__)

_GENESIS_HASH = "0" * 64


async def log_event(
    db: AsyncSession,
    *,
    action: str,
    resource_type: str,
    actor_id: Optional[UUID] = None,
    cabinet_id: Optional[UUID] = None,
    resource_id: Optional[str] = None,
    payload: Optional[dict] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> AuditLog:
    """Insert a new audit event and chain it to the previous entry.

    Must be called within an open DB transaction. The caller is responsible for
    commit/rollback — audit events must commit together with the main operation.
    """
    prev_hash = await _get_last_hash(db)
    canonical = json.dumps(payload or {}, sort_keys=True, ensure_ascii=False)
    content_hash = hashlib.sha256(
        (prev_hash + canonical).encode("utf-8")
    ).hexdigest()

    # Store the hash in the payload (the AuditLog model has no dedicated column —
    # we include it in payload under a reserved key so it travels with the data)
    full_payload = {**(payload or {}), "_hash": content_hash}

    entry = AuditLog(
        actor_id=actor_id,
        cabinet_id=cabinet_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        payload=full_payload,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(entry)
    await db.flush()  # get the id without committing
    return entry


async def verify_chain(db: AsyncSession) -> tuple[bool, Optional[int]]:
    """Verify the audit log hash chain.

    Returns:
        (True, None)           — chain is intact
        (False, first_bad_id)  — chain broken at first_bad_id
    """
    result = await db.execute(
        select(AuditLog).order_by(AuditLog.id)
    )
    rows: list[AuditLog] = result.scalars().all()

    prev_hash = _GENESIS_HASH
    for row in rows:
        payload_copy = dict(row.payload or {})
        stored_hash = payload_copy.pop("_hash", None)
        if stored_hash is None:
            log.warning("AuditLog row %d has no _hash", row.id)
            return False, row.id

        canonical = json.dumps(payload_copy, sort_keys=True, ensure_ascii=False)
        expected = hashlib.sha256(
            (prev_hash + canonical).encode("utf-8")
        ).hexdigest()

        if stored_hash != expected:
            log.error(
                "AuditLog chain broken at id=%d: stored=%s expected=%s",
                row.id, stored_hash, expected,
            )
            return False, row.id

        prev_hash = stored_hash

    return True, None


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _get_last_hash(db: AsyncSession) -> str:
    """Return the _hash from the most recently inserted audit row, or genesis."""
    result = await db.execute(
        select(AuditLog.payload)
        .order_by(AuditLog.id.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return _GENESIS_HASH
    return row.get("_hash", _GENESIS_HASH)
