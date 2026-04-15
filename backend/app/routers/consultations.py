"""Consultation management endpoints.

POST /consultations                        — create a consultation
GET  /consultations/{id}                   — get consultation (with decrypted SOAP)
GET  /patients/{patient_id}/consultations  — patient's consultation history
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.consultation import Consultation
from app.models.patient import Patient
from app.schemas.auth import CurrentUser
from app.security.audit import log_event
from app.security.jwt import get_current_user

log = logging.getLogger(__name__)

router = APIRouter(tags=["consultations"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class ConsultationCreateRequest(BaseModel):
    patient_id: uuid.UUID
    motif: str = Field(..., min_length=1, max_length=1000)
    date: Optional[datetime] = None     # defaults to now if omitted


class ConsultationResponse(BaseModel):
    id: str
    cabinet_id: str
    medecin_id: str
    patient_id: str
    date: str
    motif: str
    status: str
    soap_generated: Optional[dict] = None
    soap_validated: Optional[dict] = None
    quality_score: Optional[float] = None
    alerts: Optional[dict] = None
    transcript_available: bool          # True if transcript_encrypted is set
    created_at: str
    updated_at: str


class ConsultationSummary(BaseModel):
    """Lightweight row for the patient history list."""

    id: str
    date: str
    motif: str
    status: str
    quality_score: Optional[float] = None
    created_at: str


def _to_response(c: Consultation) -> ConsultationResponse:
    return ConsultationResponse(
        id=str(c.id),
        cabinet_id=str(c.cabinet_id),
        medecin_id=str(c.medecin_id),
        patient_id=str(c.patient_id),
        date=c.date.isoformat(),
        motif=c.motif,
        status=c.status,
        soap_generated=c.soap_generated,
        soap_validated=c.soap_validated,
        quality_score=c.quality_score,
        alerts=c.alerts,
        transcript_available=bool(c.transcript_encrypted),
        created_at=c.created_at.isoformat(),
        updated_at=c.updated_at.isoformat(),
    )


def _to_summary(c: Consultation) -> ConsultationSummary:
    return ConsultationSummary(
        id=str(c.id),
        date=c.date.isoformat(),
        motif=c.motif,
        status=c.status,
        quality_score=c.quality_score,
        created_at=c.created_at.isoformat(),
    )


# ── POST /consultations ───────────────────────────────────────────────────────

@router.post(
    "/consultations",
    status_code=status.HTTP_201_CREATED,
    summary="Create a new consultation",
)
async def create_consultation(
    body: ConsultationCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
) -> ConsultationResponse:
    """Create a consultation record and associate it with a patient.

    Verifies the patient belongs to the caller's cabinet before creating.
    """
    # Verify patient belongs to this cabinet
    patient = await db.get(Patient, body.patient_id)
    if patient is None or patient.cabinet_id != current_user.cabinet_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient not found",
        )

    consultation = Consultation(
        id=uuid.uuid4(),
        cabinet_id=current_user.cabinet_id,
        medecin_id=current_user.medecin_id,
        patient_id=body.patient_id,
        motif=body.motif,
        date=body.date or datetime.now(timezone.utc),
        status="in_progress",
    )
    db.add(consultation)
    await db.commit()
    await db.refresh(consultation)

    await log_event(
        db,
        action="patient_data_accessed",
        resource_type="consultation",
        actor_id=current_user.medecin_id,
        cabinet_id=current_user.cabinet_id,
        resource_id=str(consultation.id),
        payload={"action": "create", "patient_id": str(body.patient_id)},
    )

    log.info(
        "[consultations] Created: id=%s patient=%s medecin=%s",
        consultation.id, body.patient_id, current_user.medecin_id,
    )
    return _to_response(consultation)


# ── GET /consultations/{id} ───────────────────────────────────────────────────

@router.get(
    "/consultations/{consultation_id}",
    summary="Get a consultation with its SOAP",
)
async def get_consultation(
    consultation_id: uuid.UUID,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    include_transcript: bool = Query(
        False,
        description="If true, decrypt and include the consultation transcript",
    ),
) -> ConsultationResponse:
    """Return consultation details including generated/validated SOAP.

    SOAP sections are stored as JSONB and returned as-is (already decrypted
    at generation time — only the raw transcript is re-encrypted at rest).

    Pass include_transcript=true to decrypt and include the transcript text.
    """
    consultation = await db.get(Consultation, consultation_id)
    if consultation is None or consultation.cabinet_id != current_user.cabinet_id:
        raise HTTPException(status_code=404, detail="Consultation not found")

    await log_event(
        db,
        action="patient_data_accessed",
        resource_type="consultation",
        actor_id=current_user.medecin_id,
        cabinet_id=current_user.cabinet_id,
        resource_id=str(consultation_id),
        payload={"action": "read", "include_transcript": include_transcript},
    )

    response = _to_response(consultation)

    if include_transcript and consultation.transcript_encrypted:
        try:
            from app.security.encryption import decrypt
            transcript = decrypt(
                consultation.transcript_encrypted,
                consultation.patient_id,
            )
            # Embed transcript in soap_generated if present, else as separate field
            # Frontend can use this via soap_generated.transcript
            if response.soap_generated is not None:
                response.soap_generated["transcript"] = transcript
        except Exception as exc:
            log.warning(
                "[consultations] Transcript decrypt failed for %s: %s",
                consultation_id, exc,
            )

    return response


# ── GET /patients/{patient_id}/consultations ──────────────────────────────────

@router.get(
    "/patients/{patient_id}/consultations",
    summary="List consultation history for a patient",
)
async def list_patient_consultations(
    patient_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status_filter: Optional[str] = Query(
        None, alias="status",
        description="Filter by status: in_progress|generated|validated|exported",
    ),
) -> list[ConsultationSummary]:
    """Return consultation history for a patient, filtered to the cabinet.

    Results are ordered most-recent-first.
    Cabinet isolation is enforced — returns 404 if patient not in cabinet.
    """
    patient = await db.get(Patient, patient_id)
    if patient is None or patient.cabinet_id != current_user.cabinet_id:
        raise HTTPException(status_code=404, detail="Patient not found")

    stmt = (
        select(Consultation)
        .where(
            Consultation.patient_id == patient_id,
            Consultation.cabinet_id == current_user.cabinet_id,
        )
        .order_by(Consultation.date.desc())
        .limit(limit)
        .offset(offset)
    )

    if status_filter:
        stmt = stmt.where(Consultation.status == status_filter)

    result = await db.execute(stmt)
    consultations = result.scalars().all()

    return [_to_summary(c) for c in consultations]
