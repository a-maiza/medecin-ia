"""Pydantic schemas for SOAP generation endpoints."""
from __future__ import annotations

import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ── Sub-schemas matching OUTPUT_SCHEMA from REQUIREMENTS.md §6 ────────────────

class SoapAlert(BaseModel):
    type: str       # ALLERGIE | INTERACTION | DOSAGE | CLINIQUE
    severity: str   # CRITIQUE | ATTENTION | INFO
    message: str
    drug: Optional[str] = None
    source: str


class SoapConstantes(BaseModel):
    TA: Optional[str] = None
    FC: Optional[str] = None
    SpO2: Optional[str] = None
    poids: Optional[str] = None
    IMC: Optional[str] = None


class SoapDiagnosis(BaseModel):
    libelle: str
    cim10: Optional[str] = None


class SoapPrescription(BaseModel):
    medicament: str
    posologie: str
    duree: str
    ccam_code: Optional[str] = None
    interaction_flag: bool = False


class SoapExamen(BaseModel):
    libelle: str
    ccam_code: Optional[str] = None


class SoapArretTravail(BaseModel):
    duree: Optional[str] = None
    motif: Optional[str] = None


class SoapS(BaseModel):
    motif: str
    plaintes: list[str]
    context: str


class SoapO(BaseModel):
    constantes: SoapConstantes
    examen_clinique: str
    resultats: list[str] = Field(default_factory=list)


class SoapA(BaseModel):
    diagnostic_principal: SoapDiagnosis
    diagnostics_diff: list[SoapDiagnosis] = Field(default_factory=list)
    synthese: str


class SoapP(BaseModel):
    prescriptions: list[SoapPrescription] = Field(default_factory=list)
    examens: list[SoapExamen] = Field(default_factory=list)
    arret_travail: SoapArretTravail = Field(default_factory=SoapArretTravail)
    prochaine_consultation: Optional[str] = None
    messages_patient: list[str] = Field(default_factory=list)


class SoapContent(BaseModel):
    S: SoapS
    O: SoapO
    A: SoapA
    P: SoapP


class SoapMetadata(BaseModel):
    confidence_score: float
    missing_info: list[str] = Field(default_factory=list)
    chunks_used: list[str] = Field(default_factory=list)
    generated_at: str


class SoapOutput(BaseModel):
    """Full SOAP output matching OUTPUT_SCHEMA."""
    alerts: list[SoapAlert] = Field(default_factory=list)
    soap: Optional[SoapContent] = None   # None when CI_ABSOLUE blocks generation
    metadata: SoapMetadata


# ── Request / Response schemas ────────────────────────────────────────────────

class SoapGenerateRequest(BaseModel):
    consultation_id: UUID
    # These can be omitted if the service fetches them from the Consultation row
    clinical_justification: Optional[str] = None  # Required for CI_RELATIVE unblock


class SoapGenerateResponse(BaseModel):
    consultation_id: UUID
    status: str           # "generating" (streaming) | "blocked" (CI_ABSOLUE)
    alerts: list[SoapAlert] = Field(default_factory=list)
    soap: Optional[SoapContent] = None
    metadata: Optional[SoapMetadata] = None
    chunks_used: list[str] = Field(default_factory=list)


class SoapPatchRequest(BaseModel):
    """Inline edit of a generated SOAP before validation."""
    soap: SoapContent


class SoapValidateRequest(BaseModel):
    """Doctor validates (signs) the SOAP note."""
    soap_validated: SoapContent
    # Seconds elapsed between generation and validation (for ValidationMetric)
    time_to_validate_seconds: Optional[float] = None
    clinical_justification: Optional[str] = None  # Required to unblock CI_RELATIVE


class SoapValidateResponse(BaseModel):
    consultation_id: UUID
    status: str           # "validated"
    quality_score: float
    ns5_indexed: bool     # True if quality_score > 0.7 and NS5 chunk was created


class ConsultationOut(BaseModel):
    id: UUID
    patient_id: UUID
    medecin_id: UUID
    cabinet_id: UUID
    date: datetime.datetime
    motif: str
    status: str
    soap_generated: Optional[dict[str, Any]] = None
    soap_validated: Optional[dict[str, Any]] = None
    alerts: Optional[dict[str, Any]] = None
    quality_score: Optional[float] = None
    chunks_used: Optional[list[str]] = None

    model_config = {"from_attributes": True}
