"""Patient management endpoints.

GET    /patients                — list patients for cabinet (paginated)
POST   /patients                — create patient (encrypts PII)
GET    /patients/search         — search by nom or INS
GET    /patients/{id}           — get single patient (decrypted)
PATCH  /patients/{id}           — partial update (allergies, traitements, DFG…)
DELETE /patients/{id}           — hard-delete (RGPD erasure)
"""
from __future__ import annotations

import logging
import uuid
from datetime import date
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.schemas.auth import CurrentUser
from app.security.audit import log_event
from app.security.jwt import get_current_user
from app.services.patient_service import (
    PatientCreate,
    PatientDecrypted,
    PatientUpdate,
    get_patient_service,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/patients", tags=["patients"])


# ── Request / response schemas ────────────────────────────────────────────────

class PatientCreateRequest(BaseModel):
    nom: str = Field(..., min_length=1, max_length=200)
    date_naissance: date
    sexe: Optional[str] = Field(None, pattern="^(M|F|autre)$")
    ins: Optional[str] = Field(None, max_length=22)
    allergies: list[str] = Field(default_factory=list)
    traitements_actifs: list[str] = Field(default_factory=list)
    antecedents: list[str] = Field(default_factory=list)
    dfg: Optional[float] = Field(None, ge=0.0, le=200.0)
    grossesse: bool = False
    doctolib_patient_id: Optional[str] = Field(None, max_length=100)


class PatientUpdateRequest(BaseModel):
    allergies: Optional[list[str]] = None
    traitements_actifs: Optional[list[str]] = None
    antecedents: Optional[list[str]] = None
    dfg: Optional[float] = Field(None, ge=0.0, le=200.0)
    grossesse: Optional[bool] = None
    doctolib_patient_id: Optional[str] = Field(None, max_length=100)


class PatientResponse(BaseModel):
    id: str
    cabinet_id: str
    ins: Optional[str] = None
    nom: str
    date_naissance_hash: str
    sexe: Optional[str] = None
    allergies: list[str]
    traitements_actifs: list[str]
    antecedents: list[str]
    dfg: Optional[float] = None
    grossesse: bool
    doctolib_patient_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


def _to_response(p: PatientDecrypted) -> PatientResponse:
    return PatientResponse(
        id=p.id,
        cabinet_id=p.cabinet_id,
        ins=p.ins,
        nom=p.nom,
        date_naissance_hash=p.date_naissance_hash,
        sexe=p.sexe,
        allergies=p.allergies,
        traitements_actifs=p.traitements_actifs,
        antecedents=p.antecedents,
        dfg=p.dfg,
        grossesse=p.grossesse,
        doctolib_patient_id=p.doctolib_patient_id,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", summary="List patients for the cabinet")
async def list_patients(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[PatientResponse]:
    redis = getattr(request.app.state, "redis", None)
    svc = get_patient_service()
    patients = await svc.list_by_cabinet(
        cabinet_id=current_user.cabinet_id,
        db=db,
        limit=limit,
        offset=offset,
    )
    return [_to_response(p) for p in patients]


@router.get("/search", summary="Search patients by nom or INS")
async def search_patients(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    nom: Optional[str] = Query(None, min_length=2, max_length=200),
    ins: Optional[str] = Query(None, max_length=22),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> list[PatientResponse]:
    if not nom and not ins:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide at least one of: nom, ins",
        )
    redis = getattr(request.app.state, "redis", None)
    svc = get_patient_service()
    patients = await svc.search(
        cabinet_id=current_user.cabinet_id,
        db=db,
        nom=nom,
        ins=ins,
        limit=limit,
        offset=offset,
    )
    return [_to_response(p) for p in patients]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create a patient (PII encrypted at rest)",
)
async def create_patient(
    request: Request,
    body: PatientCreateRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
) -> PatientResponse:
    redis = getattr(request.app.state, "redis", None)
    svc = get_patient_service()

    patient_orm = await svc.create(
        data=PatientCreate(
            cabinet_id=current_user.cabinet_id,
            nom=body.nom,
            date_naissance=body.date_naissance,
            sexe=body.sexe,
            ins=body.ins,
            allergies=body.allergies,
            traitements_actifs=body.traitements_actifs,
            antecedents=body.antecedents,
            dfg=body.dfg,
            grossesse=body.grossesse,
            doctolib_patient_id=body.doctolib_patient_id,
        ),
        db=db,
        redis=redis,
    )

    await log_event(
        db,
        action="patient_data_accessed",
        resource_type="patient",
        actor_id=current_user.medecin_id,
        cabinet_id=current_user.cabinet_id,
        resource_id=str(patient_orm.id),
        payload={"action": "create"},
    )

    dec = await svc.get(
        patient_id=patient_orm.id,
        cabinet_id=current_user.cabinet_id,
        db=db,
        redis=redis,
    )
    return _to_response(dec)


@router.get("/{patient_id}", summary="Get a single patient (decrypted)")
async def get_patient(
    patient_id: uuid.UUID,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
) -> PatientResponse:
    redis = getattr(request.app.state, "redis", None)
    svc = get_patient_service()

    patient = await svc.get(
        patient_id=patient_id,
        cabinet_id=current_user.cabinet_id,
        db=db,
        redis=redis,
    )
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient not found")

    await log_event(
        db,
        action="patient_data_accessed",
        resource_type="patient",
        actor_id=current_user.medecin_id,
        cabinet_id=current_user.cabinet_id,
        resource_id=str(patient_id),
        payload={"action": "read"},
    )

    return _to_response(patient)


@router.patch("/{patient_id}", summary="Update a patient's clinical data")
async def update_patient(
    patient_id: uuid.UUID,
    body: PatientUpdateRequest,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
) -> PatientResponse:
    redis = getattr(request.app.state, "redis", None)
    svc = get_patient_service()

    updated = await svc.update(
        patient_id=patient_id,
        cabinet_id=current_user.cabinet_id,
        data=PatientUpdate(
            allergies=body.allergies,
            traitements_actifs=body.traitements_actifs,
            antecedents=body.antecedents,
            dfg=body.dfg,
            grossesse=body.grossesse,
            doctolib_patient_id=body.doctolib_patient_id,
        ),
        db=db,
        redis=redis,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Patient not found")

    return _to_response(updated)


@router.delete(
    "/{patient_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Hard-delete patient (RGPD erasure)",
)
async def delete_patient(
    patient_id: uuid.UUID,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
) -> None:
    redis = getattr(request.app.state, "redis", None)
    svc = get_patient_service()

    deleted = await svc.delete(
        patient_id=patient_id,
        cabinet_id=current_user.cabinet_id,
        db=db,
        redis=redis,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Patient not found")
