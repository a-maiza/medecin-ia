import re
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator


RPPS_PATTERN = re.compile(r"^\d{11}$")


class RegisterRequest(BaseModel):
    """Payload for POST /auth/register — called after Auth0 authentication."""

    rpps: str = Field(..., description="Numéro RPPS du médecin (11 chiffres)")
    nom: str = Field(..., min_length=1, max_length=100)
    prenom: str = Field(..., min_length=1, max_length=100)
    specialite: str = Field(..., min_length=1, max_length=100)
    email: EmailStr

    # Cabinet info (created together with the doctor account)
    nom_cabinet: str = Field(..., min_length=1, max_length=200)
    adresse_cabinet: str = Field(..., min_length=1)
    pays: str = Field(..., pattern="^(FR|DZ)$")
    siret: Optional[str] = Field(None, pattern=r"^\d{14}$")

    @field_validator("rpps")
    @classmethod
    def validate_rpps(cls, v: str) -> str:
        if not RPPS_PATTERN.match(v):
            raise ValueError("Le RPPS doit contenir exactement 11 chiffres")
        return v


class TokenPayload(BaseModel):
    """Claims extracted from a verified Auth0 JWT."""

    sub: str                              # Auth0 subject (auth0_sub)
    rpps: Optional[str] = None            # Custom claim injected by Auth0 Action
    specialite: Optional[str] = None      # From PSC token (e-CPS)
    email: Optional[str] = None


class CurrentUser(BaseModel):
    """Injected into request context by the JWT middleware."""

    medecin_id: UUID
    cabinet_id: UUID
    role: str
    rpps: str
    auth0_sub: str


class MedecinResponse(BaseModel):
    """Returned by POST /auth/register."""

    medecin_id: UUID
    cabinet_id: UUID
    rpps: str
    email: str
    nom: str
    prenom: str
    specialite: str
    role: str
    trial_ends_at: Optional[str] = None

    model_config = {"from_attributes": True}
