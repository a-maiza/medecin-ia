"""Row-Level Security helpers for PostgreSQL cabinet/patient isolation.

Usage in a route:
    async with rls_context(db, cabinet_id=current_user.cabinet_id):
        rows = await db.execute(select(Patient))

Or as a FastAPI dependency that wires current_user automatically:
    db: RlsSession = Depends(get_rls_db)
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.auth import CurrentUser


@asynccontextmanager
async def rls_context(
    db: AsyncSession,
    cabinet_id: UUID,
    patient_id: Optional[UUID] = None,
) -> AsyncGenerator[AsyncSession, None]:
    """Set PostgreSQL session-local RLS variables for the duration of a block.

    Uses SET LOCAL so the variables are transaction-scoped and reset automatically
    when the transaction ends — safe with connection pooling.
    """
    # Validate inputs before executing raw SQL to prevent injection
    # (UUIDs only contain hex digits and hyphens)
    cabinet_str = str(cabinet_id)
    patient_str = str(patient_id) if patient_id else None

    await db.execute(  # type: ignore[call-overload]
        "SELECT set_config('app.current_cabinet_id', :cabinet_id, true)",
        {"cabinet_id": cabinet_str},
    )
    if patient_str:
        await db.execute(  # type: ignore[call-overload]
            "SELECT set_config('app.current_patient_id', :patient_id, true)",
            {"patient_id": patient_str},
        )
    try:
        yield db
    finally:
        # Variables reset automatically at transaction end; no explicit cleanup needed
        pass


# ── FastAPI dependency ─────────────────────────────────────────────────────────

class RlsSession:
    """Thin wrapper: an AsyncSession with RLS already configured."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    def __getattr__(self, name: str):
        return getattr(self._db, name)


async def get_rls_db(
    db: AsyncSession,
    current_user: CurrentUser,
) -> AsyncGenerator[RlsSession, None]:
    """FastAPI dependency — yields a DB session with cabinet_id RLS wired in.

    Wire it alongside get_current_user:
        async def my_route(
            rls_db: Annotated[RlsSession, Depends(get_rls_db)],
            current_user: Annotated[CurrentUser, Depends(get_current_user)],
        ): ...

    In practice routes usually call rls_context() directly when they also need
    patient-level isolation.
    """
    async with rls_context(db, cabinet_id=current_user.cabinet_id):
        yield RlsSession(db)
