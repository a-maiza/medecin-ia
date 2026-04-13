from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.schemas.auth import CurrentUser, MedecinResponse, RegisterRequest, TokenPayload
from app.security.jwt import get_current_user, get_token_payload
from app.services.auth_service import register_medecin

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=MedecinResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Créer un compte médecin après authentification Auth0",
)
async def register(
    payload: RegisterRequest,
    token: Annotated[TokenPayload, Depends(get_token_payload)],
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> MedecinResponse:
    """Create a Medecin + Cabinet + Subscription (trial 14 days).

    The request must carry a valid Auth0 Bearer token. The `auth0_sub` from
    that token is bound permanently to the new Medecin row.
    """
    try:
        return await register_medecin(payload, token, db, settings)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get(
    "/me",
    response_model=CurrentUser,
    summary="Retourner l'identité du médecin connecté",
)
async def me(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
) -> CurrentUser:
    """Return the authenticated doctor's identity (medecin_id, cabinet_id, role)."""
    return current_user
