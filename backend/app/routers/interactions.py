"""Drug interaction check endpoint.

POST /interactions/check
    Body: { "new_drugs": [...], "active_drugs": [...] }
    Returns: list of interactions sorted by severity (CI_ABSOLUE first).

Used by the frontend for real-time feedback during medication entry.
Also used internally by the SOAP generator before calling Claude.

Security: requires valid JWT (cabinet_id extracted from token).
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.auth import CurrentUser
from app.security.jwt import get_current_user
from app.services.interaction_checker import (
    InteractionCheckResult,
    get_interaction_checker,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/interactions", tags=["clinical-alerts"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class InteractionCheckRequest(BaseModel):
    """New medications to check against the patient's current regimen."""

    new_drugs: list[str] = Field(
        default_factory=list,
        description="Newly prescribed drug names (commercial or DCI)",
        max_length=50,
    )
    active_drugs: list[str] = Field(
        default_factory=list,
        description="Patient's currently active medications",
        max_length=100,
    )


class InteractionAlertResponse(BaseModel):
    """Single drug-drug interaction alert."""

    drug_a: str
    drug_b: str
    severity: str  # CI_ABSOLUE | CI_RELATIVE | PRECAUTION | INFO
    description: str
    source: str


class InteractionCheckResponse(BaseModel):
    """Full interaction check result."""

    alerts: list[InteractionAlertResponse]
    checked_drugs: list[str]  # normalised DCIs that were checked
    has_ci_absolue: bool
    has_ci_relative: bool
    from_cache: bool


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.post(
    "/check",
    summary="Check drug-drug interactions",
    description=(
        "Returns all interactions between new_drugs and active_drugs, "
        "sorted by severity. CI_ABSOLUE interactions block SOAP generation. "
        "CI_RELATIVE requires clinical justification before validation."
    ),
)
async def check_interactions(
    request: Request,
    body: InteractionCheckRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
) -> InteractionCheckResponse:
    """Check interactions between newly prescribed and active medications."""
    redis = getattr(request.app.state, "redis", None)

    checker = get_interaction_checker()
    result: InteractionCheckResult = await checker.check(
        new_drugs=body.new_drugs,
        active_drugs=body.active_drugs,
        db=db,
        redis=redis,
    )

    log.info(
        "[interactions/check] %d drugs → %d alerts (ci_absolue=%s, cache=%s)",
        len(result.checked_drugs),
        len(result.alerts),
        result.has_ci_absolue,
        result.from_cache,
    )

    return InteractionCheckResponse(
        alerts=[
            InteractionAlertResponse(
                drug_a=a.drug_a,
                drug_b=a.drug_b,
                severity=a.severity,
                description=a.description,
                source=a.source,
            )
            for a in result.alerts
        ],
        checked_drugs=result.checked_drugs,
        has_ci_absolue=result.has_ci_absolue,
        has_ci_relative=result.has_ci_relative,
        from_cache=result.from_cache,
    )
