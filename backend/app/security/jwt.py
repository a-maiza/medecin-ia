"""Auth0 JWT verification and current-user dependency."""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Annotated, Optional

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.models.medecin import Medecin
from app.schemas.auth import CurrentUser, TokenPayload

log = logging.getLogger(__name__)
bearer_scheme = HTTPBearer(auto_error=True)


@lru_cache(maxsize=1)
def _get_jwks(domain: str) -> dict:
    """Fetch and cache Auth0 JWKS (JSON Web Key Set).

    The cache is intentionally module-level so it survives across requests.
    On key rotation Auth0 typically serves both old and new keys for 24h,
    so stale cache is acceptable; the server restarts naturally pick up new keys.
    """
    url = f"https://{domain}/.well-known/jwks.json"
    resp = httpx.get(url, timeout=10)
    resp.raise_for_status()
    return resp.json()


def _verify_token(token: str, settings: Settings) -> TokenPayload:
    """Decode and verify an Auth0 JWT. Raises HTTPException on failure."""
    try:
        jwks = _get_jwks(settings.AUTH0_DOMAIN)
        header = jwt.get_unverified_header(token)
        # Find the matching key by kid
        rsa_key: dict = {}
        for key in jwks.get("keys", []):
            if key.get("kid") == header.get("kid"):
                rsa_key = {
                    "kty": key["kty"],
                    "kid": key["kid"],
                    "use": key["use"],
                    "n": key["n"],
                    "e": key["e"],
                }
                break

        if not rsa_key:
            raise JWTError("No matching RSA key found in JWKS")

        payload = jwt.decode(
            token,
            rsa_key,
            algorithms=["RS256"],
            audience=settings.AUTH0_AUDIENCE,
            issuer=f"https://{settings.AUTH0_DOMAIN}/",
        )

        ns = settings.AUTH0_CLAIM_NAMESPACE
        return TokenPayload(
            sub=payload["sub"],
            rpps=payload.get(f"{ns}rpps"),
            specialite=payload.get(f"{ns}specialite"),
            email=payload.get("email"),
        )

    except JWTError as exc:
        log.warning("JWT verification failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalide ou expiré",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> CurrentUser:
    """FastAPI dependency — verifies JWT and returns the authenticated medecin."""
    token_data = _verify_token(credentials.credentials, settings)

    result = await db.execute(
        select(Medecin).where(Medecin.auth0_sub == token_data.sub)
    )
    medecin: Optional[Medecin] = result.scalar_one_or_none()

    if medecin is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Compte médecin introuvable — veuillez vous inscrire",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return CurrentUser(
        medecin_id=medecin.id,
        cabinet_id=medecin.cabinet_id,
        role=medecin.role,
        rpps=medecin.rpps,
        auth0_sub=medecin.auth0_sub,
    )


async def get_token_payload(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> TokenPayload:
    """Lighter dependency — only verifies the token without a DB lookup.

    Used by POST /auth/register where the Medecin row does not exist yet.
    """
    return _verify_token(credentials.credentials, settings)
