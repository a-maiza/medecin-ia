"""SOAP generation and management endpoints.

Routes:
    POST   /soap/generate          — trigger SOAP generation (streaming SSE)
    GET    /soap/{consultation_id} — return current SOAP for a consultation
    PATCH  /soap/{consultation_id} — save inline edits before validation
    POST   /soap/{consultation_id}/validate — doctor signs the SOAP
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.consultation import Consultation
from app.schemas.soap import (
    ConsultationOut,
    SoapGenerateRequest,
    SoapPatchRequest,
    SoapValidateRequest,
    SoapValidateResponse,
)
from app.security.jwt import get_current_user
from app.models.medecin import Medecin

log = logging.getLogger(__name__)

router = APIRouter(prefix="/soap", tags=["soap"])


# ── Redis dependency ──────────────────────────────────────────────────────────

async def get_redis(request: Request):
    """Retrieve the shared Redis connection from app state."""
    return request.app.state.redis


# ── POST /soap/generate  (Server-Sent Events streaming) ───────────────────────

@router.post("/generate", summary="Generate SOAP (streaming SSE)")
async def generate_soap(
    body: SoapGenerateRequest,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
    current_user: Medecin = Depends(get_current_user),
) -> StreamingResponse:
    """Stream SOAP generation as Server-Sent Events.

    Client reads an SSE stream; each event is a JSON object:
        data: {"type": "alert",   "data": {...}}
        data: {"type": "token",   "data": "<text>"}
        data: {"type": "done",    "data": {"soap": {...}, "metadata": {...}}}
        data: {"type": "blocked", "data": {"alerts": [...]}}
        data: {"type": "error",   "data": {"message": "..."}}
    """
    from app.services.soap_generator import soap_generator

    async def event_stream() -> AsyncIterator[str]:
        try:
            async for chunk in soap_generator.generate(
                consultation_id=body.consultation_id,
                db=db,
                redis=redis,
                current_user_id=current_user.id,
                cabinet_id=current_user.cabinet_id,
                clinical_justification=body.clinical_justification,
            ):
                yield f"data: {chunk}\n\n"
        except Exception as exc:
            log.error("[soap/generate] Unhandled error: %s", exc, exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'data': {'message': str(exc)}})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable Nginx buffering
        },
    )


# ── GET /soap/{consultation_id} ───────────────────────────────────────────────

@router.get("/{consultation_id}", response_model=ConsultationOut, summary="Get consultation SOAP")
async def get_soap(
    consultation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: Medecin = Depends(get_current_user),
) -> ConsultationOut:
    """Return the consultation record with generated/validated SOAP."""
    consultation = await db.get(Consultation, consultation_id)
    if consultation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Consultation not found")
    if str(consultation.cabinet_id) != str(current_user.cabinet_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    return ConsultationOut.model_validate(consultation)


# ── PATCH /soap/{consultation_id} ─────────────────────────────────────────────

@router.patch("/{consultation_id}", response_model=ConsultationOut, summary="Save inline SOAP edits")
async def patch_soap(
    consultation_id: uuid.UUID,
    body: SoapPatchRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Medecin = Depends(get_current_user),
) -> ConsultationOut:
    """Overwrite soap_generated with doctor's inline edits.

    Does NOT set status=validated — use /validate for that.
    """
    consultation = await db.get(Consultation, consultation_id)
    if consultation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Consultation not found")
    if str(consultation.cabinet_id) != str(current_user.cabinet_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")
    if consultation.status == "validated":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot edit a validated SOAP — create a new consultation",
        )

    consultation.soap_generated = {"soap": body.soap.model_dump(), **({
        "alerts": consultation.soap_generated.get("alerts", [])
        if consultation.soap_generated else []
    })}
    await db.commit()

    return ConsultationOut.model_validate(consultation)


# ── POST /soap/{consultation_id}/validate ─────────────────────────────────────

@router.post(
    "/{consultation_id}/validate",
    response_model=SoapValidateResponse,
    summary="Validate (sign) a SOAP",
)
async def validate_soap(
    consultation_id: uuid.UUID,
    body: SoapValidateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Medecin = Depends(get_current_user),
) -> SoapValidateResponse:
    """Doctor signs the SOAP note.

    - Computes quality_score (cosine sim generated↔validated)
    - Creates ValidationMetric + TrainingPair
    - Indexes NS5 if quality_score > 0.7
    - Logs soap_signed to audit_log
    """
    from app.services.soap_generator import soap_generator

    # Check CI_RELATIVE justification requirement
    consultation = await db.get(Consultation, consultation_id)
    if consultation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Consultation not found")
    if str(consultation.cabinet_id) != str(current_user.cabinet_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    alerts = []
    if consultation.alerts:
        alerts = consultation.alerts.get("alerts", [])

    has_ci_relative_unacknowledged = any(
        a.get("severity") == "CI_RELATIVE" for a in alerts
    ) and not body.clinical_justification

    if has_ci_relative_unacknowledged:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="clinical_justification is required to validate a SOAP with CI_RELATIVE alerts",
        )

    try:
        result = await soap_generator.validate_soap(
            consultation_id=consultation_id,
            validated_soap=body.soap_validated.model_dump(),
            db=db,
            current_user_id=current_user.id,
            cabinet_id=current_user.cabinet_id,
            time_to_validate_seconds=body.time_to_validate_seconds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    return SoapValidateResponse(
        consultation_id=consultation_id,
        status="validated",
        quality_score=result["quality_score"],
        ns5_indexed=result["ns5_indexed"],
    )
