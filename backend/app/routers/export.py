"""Export & interoperability endpoints.

GET  /export/pdf/{consultation_id}      — streaming PDF (reportlab)
GET  /export/fhir/{consultation_id}     — FHIR R4 Bundle (JSON)
POST /export/dmp/{consultation_id}      — push to DMP via MSSanté gateway
POST /export/doctolib/{consultation_id} — sync to Doctolib + DMP in parallel

Failure isolation: DMP and Doctolib channels fail independently.
Partial success is always returned so the UI can display channel-specific errors.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.cabinet import Cabinet
from app.models.consultation import Consultation
from app.models.medecin import Medecin
from app.models.patient import Patient
from app.schemas.auth import CurrentUser
from app.security.audit import log_event
from app.security.jwt import get_current_user
from app.services.export_service import (
    build_fhir_bundle,
    generate_soap_pdf,
    push_to_dmp,
    push_to_doctolib,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/export", tags=["export"])


# ── Shared helpers ────────────────────────────────────────────────────────────

async def _get_consultation(
    consultation_id: uuid.UUID,
    cabinet_id: uuid.UUID,
    db: AsyncSession,
) -> Consultation:
    c = await db.get(Consultation, consultation_id)
    if c is None or c.cabinet_id != cabinet_id:
        raise HTTPException(status_code=404, detail="Consultation not found")
    return c


async def _get_medecin(medecin_id: uuid.UUID, db: AsyncSession) -> Medecin:
    m = await db.get(Medecin, medecin_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Médecin not found")
    return m


async def _get_patient(
    patient_id: uuid.UUID,
    cabinet_id: uuid.UUID,
    db: AsyncSession,
) -> Patient:
    p = await db.get(Patient, patient_id)
    if p is None or p.cabinet_id != cabinet_id:
        raise HTTPException(status_code=404, detail="Patient not found")
    return p


async def _get_cabinet(cabinet_id: uuid.UUID, db: AsyncSession) -> Cabinet:
    cab = await db.get(Cabinet, cabinet_id)
    if cab is None:
        raise HTTPException(status_code=404, detail="Cabinet not found")
    return cab


async def _load_context(
    consultation_id: uuid.UUID,
    current_user: CurrentUser,
    db: AsyncSession,
) -> tuple[Consultation, Medecin, Patient, Cabinet]:
    """Load all four ORM rows needed for any export, enforcing cabinet isolation."""
    consultation = await _get_consultation(consultation_id, current_user.cabinet_id, db)
    medecin, patient, cabinet = await asyncio.gather(
        _get_medecin(current_user.medecin_id, db),
        _get_patient(consultation.patient_id, current_user.cabinet_id, db),
        _get_cabinet(current_user.cabinet_id, db),
    )
    return consultation, medecin, patient, cabinet


# ── GET /export/pdf/{consultation_id} ─────────────────────────────────────────

@router.get(
    "/pdf/{consultation_id}",
    summary="Export SOAP consultation as PDF",
    responses={
        200: {
            "content": {"application/pdf": {}},
            "description": "PDF document attachment",
        }
    },
    response_class=Response,
)
async def export_pdf(
    consultation_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
) -> Response:
    """Generate a signed PDF of the SOAP note with cabinet letterhead.

    Includes: cabinet name, médecin RPPS, patient pseudonym, date,
    4 SOAP sections, CCAM and CIM-10 codes if present.
    """
    consultation, medecin, patient, cabinet = await _load_context(
        consultation_id, current_user, db
    )

    if not (consultation.soap_validated or consultation.soap_generated):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No SOAP note available for this consultation",
        )

    # Use the pseudonym hash as the patient label (PII not sent to rendering)
    patient_label = patient.nom_pseudonyme

    try:
        pdf_bytes = generate_soap_pdf(consultation, patient_label, medecin, cabinet)
    except Exception as exc:
        log.error("[export] PDF generation error consultation=%s: %s", consultation_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="PDF generation failed",
        )

    await log_event(
        db,
        action="dmp_exported",
        resource_type="consultation",
        actor_id=current_user.medecin_id,
        cabinet_id=current_user.cabinet_id,
        resource_id=str(consultation_id),
        payload={"format": "pdf"},
    )
    await db.commit()

    filename = f"consultation_{consultation_id}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── GET /export/fhir/{consultation_id} ───────────────────────────────────────

@router.get(
    "/fhir/{consultation_id}",
    summary="Export consultation as FHIR R4 Bundle",
)
async def export_fhir(
    consultation_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
) -> dict:
    """Return a FHIR R4 Bundle (document) containing Composition + Patient + Practitioner.

    SOAP sections are mapped to LOINC codes. RPPS and INS identifiers use
    official French OIDs.
    """
    consultation, medecin, patient, cabinet = await _load_context(
        consultation_id, current_user, db
    )

    bundle = build_fhir_bundle(consultation, patient, medecin, cabinet)

    await log_event(
        db,
        action="dmp_exported",
        resource_type="consultation",
        actor_id=current_user.medecin_id,
        cabinet_id=current_user.cabinet_id,
        resource_id=str(consultation_id),
        payload={"format": "fhir_r4"},
    )
    await db.commit()

    return bundle


# ── POST /export/dmp/{consultation_id} ────────────────────────────────────────

class DMPExportResponse(BaseModel):
    dmp_document_id: str
    errors: list[str] = []


@router.post(
    "/dmp/{consultation_id}",
    summary="Push consultation to DMP (requires active e-CPS)",
    response_model=DMPExportResponse,
    status_code=status.HTTP_200_OK,
)
async def export_dmp(
    consultation_id: uuid.UUID,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
) -> DMPExportResponse:
    """Push the FHIR R4 Bundle to the MSSanté DMP gateway.

    Requires an active e-CPS bearer token in the Authorization header.
    Stores the returned dmp_document_id on the consultation row.
    Logs a `dmp_exported` audit event on success.
    """
    # Extract the e-CPS Bearer token; the caller must obtain it via Pro Santé Connect
    auth_header = request.headers.get("Authorization", "")
    ecps_token = auth_header.removeprefix("Bearer ").strip() or None

    consultation, medecin, patient, cabinet = await _load_context(
        consultation_id, current_user, db
    )

    fhir_bundle = build_fhir_bundle(consultation, patient, medecin, cabinet)

    try:
        dmp_doc_id = await push_to_dmp(str(consultation_id), fhir_bundle, ecps_token)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except Exception as exc:
        log.error("[export] DMP gateway error consultation=%s: %s", consultation_id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"DMP gateway error: {exc}",
        )

    consultation.dmp_document_id = dmp_doc_id
    await log_event(
        db,
        action="dmp_exported",
        resource_type="consultation",
        actor_id=current_user.medecin_id,
        cabinet_id=current_user.cabinet_id,
        resource_id=str(consultation_id),
        payload={"dmp_document_id": dmp_doc_id},
    )
    await db.commit()

    log.info("[export] DMP success consultation=%s doc_id=%s", consultation_id, dmp_doc_id)
    return DMPExportResponse(dmp_document_id=dmp_doc_id)


# ── POST /export/doctolib/{consultation_id} ───────────────────────────────────

class DoctolibExportResponse(BaseModel):
    doctolib_consultation_id: Optional[str] = None
    dmp_document_id: Optional[str] = None
    errors: list[str] = []


@router.post(
    "/doctolib/{consultation_id}",
    summary="Sync consultation to Doctolib + DMP in parallel",
    response_model=DoctolibExportResponse,
    status_code=status.HTTP_200_OK,
)
async def export_doctolib(
    consultation_id: uuid.UUID,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
) -> DoctolibExportResponse:
    """Push to Doctolib and DMP simultaneously using asyncio.gather.

    Each channel fails independently — a DMP error will not block the Doctolib
    push and vice versa. Partial success is returned with an `errors` list so
    the frontend can display channel-specific notifications.

    Preconditions (422 if not met):
    - médecin must have a `doctolib_token` in preferences
    - patient must have a `doctolib_patient_id`
    """
    auth_header = request.headers.get("Authorization", "")
    ecps_token = auth_header.removeprefix("Bearer ").strip() or None

    consultation, medecin, patient, cabinet = await _load_context(
        consultation_id, current_user, db
    )

    doctolib_token = (medecin.preferences or {}).get("doctolib_token")
    if not doctolib_token:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No Doctolib token configured for this médecin",
        )
    if not patient.doctolib_patient_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Patient has no Doctolib patient ID",
        )

    fhir_bundle = build_fhir_bundle(consultation, patient, medecin, cabinet)
    soap = consultation.soap_validated or consultation.soap_generated or {}

    # Run DMP + Doctolib in parallel; capture exceptions without raising
    dmp_result, doctolib_result = await asyncio.gather(
        push_to_dmp(str(consultation_id), fhir_bundle, ecps_token),
        push_to_doctolib(
            str(consultation_id),
            patient.doctolib_patient_id,
            soap,
            doctolib_token,
        ),
        return_exceptions=True,
    )

    errors: list[str] = []
    dmp_doc_id: Optional[str] = None
    doctolib_consult_id: Optional[str] = None

    if isinstance(dmp_result, Exception):
        log.warning("[export] DMP channel failed consultation=%s: %s", consultation_id, dmp_result)
        errors.append(f"DMP: {dmp_result}")
    else:
        dmp_doc_id = dmp_result
        consultation.dmp_document_id = dmp_doc_id

    if isinstance(doctolib_result, Exception):
        log.warning(
            "[export] Doctolib channel failed consultation=%s: %s",
            consultation_id, doctolib_result,
        )
        errors.append(f"Doctolib: {doctolib_result}")
    else:
        doctolib_consult_id = doctolib_result
        consultation.doctolib_consultation_id = doctolib_consult_id

    await log_event(
        db,
        action="doctolib_synced",
        resource_type="consultation",
        actor_id=current_user.medecin_id,
        cabinet_id=current_user.cabinet_id,
        resource_id=str(consultation_id),
        payload={
            "dmp_document_id": dmp_doc_id,
            "doctolib_consultation_id": doctolib_consult_id,
            "errors": errors,
        },
    )
    await db.commit()

    log.info(
        "[export] Doctolib+DMP consultation=%s dmp=%s doctolib=%s errors=%s",
        consultation_id, dmp_doc_id, doctolib_consult_id, errors,
    )
    return DoctolibExportResponse(
        dmp_document_id=dmp_doc_id,
        doctolib_consultation_id=doctolib_consult_id,
        errors=errors,
    )
