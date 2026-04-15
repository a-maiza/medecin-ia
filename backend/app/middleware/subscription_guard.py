"""Subscription guard middleware.

Blocks mutation requests (POST / PATCH / PUT / DELETE) from cabinets whose
subscription has expired (status ∉ {active} AND trial_ends_at ≤ now).

Read-only access (GET / HEAD / OPTIONS) is always allowed so doctors can
still consult their historical data after a plan expires.

Cabinet ID is extracted from the JWT without full signature verification
(same optimisation as RateLimitMiddleware). A missing or invalid JWT is
passed through so that FastAPI's own auth dependency can handle it normally.

Subscription status is cached in Redis for 60 seconds to avoid a DB round-trip
on every request. Key: ``sub_guard:{cabinet_id}`` → ``"ok"`` | ``"blocked"``.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from jose import jwt as jose_jwt
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

log = logging.getLogger(__name__)

# Methods that write data and require an active subscription
_MUTATION_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Paths exempt from subscription checks (always allowed regardless of status)
_BYPASS_PREFIXES = (
    "/health",
    "/metrics",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/auth/",
    "/billing/",
    "/webhooks/",
)

_CACHE_TTL = 60  # seconds


class SubscriptionGuardMiddleware(BaseHTTPMiddleware):
    """FastAPI/Starlette middleware enforcing active-subscription access control."""

    def __init__(self, app: ASGIApp, *, enabled: bool = True) -> None:
        super().__init__(app)
        self._enabled = enabled

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if not self._enabled:
            return await call_next(request)

        # Only guard mutation methods
        if request.method not in _MUTATION_METHODS:
            return await call_next(request)

        # Bypass exempt paths
        path = request.url.path
        if any(path.startswith(p) for p in _BYPASS_PREFIXES):
            return await call_next(request)

        cabinet_id = _extract_cabinet_id(request)
        if cabinet_id is None:
            # No JWT — pass through; FastAPI auth will reject unauthenticated
            return await call_next(request)

        allowed = await self._is_allowed(cabinet_id, request)
        if not allowed:
            return JSONResponse(
                status_code=402,
                content={
                    "detail": (
                        "Abonnement expiré. Veuillez renouveler votre abonnement "
                        "pour créer de nouvelles consultations."
                    ),
                    "code": "SUBSCRIPTION_EXPIRED",
                },
            )

        return await call_next(request)

    async def _is_allowed(self, cabinet_id: str, request: Request) -> bool:
        """Return True if the cabinet has an active subscription or valid trial."""
        redis = getattr(request.app.state, "redis", None)

        # ── Redis cache ────────────────────────────────────────────────────────
        if redis is not None:
            cache_key = f"sub_guard:{cabinet_id}"
            try:
                cached = await redis.get(cache_key)
                if cached is not None:
                    return cached == "ok"
            except Exception as exc:
                log.debug("[sub_guard] Redis get error: %s", exc)

        # ── DB lookup ──────────────────────────────────────────────────────────
        result = await self._check_db(cabinet_id, request)

        # Write result to cache
        if redis is not None:
            try:
                cache_value = "ok" if result else "blocked"
                await redis.set(cache_key, cache_value, ex=_CACHE_TTL)
            except Exception as exc:
                log.debug("[sub_guard] Redis set error: %s", exc)

        return result

    @staticmethod
    async def _check_db(cabinet_id: str, request: Request) -> bool:
        """Query the DB to check subscription + trial validity.

        Uses the app's SQLAlchemy async engine directly (no dependency injection).
        Returns True (allow) on any DB error to avoid blocking legitimate users.
        """
        from app.core.database import engine

        now = datetime.now(timezone.utc)

        try:
            async with engine.connect() as conn:
                # Check active subscription
                row = await conn.execute(
                    text(
                        "SELECT s.status "
                        "FROM subscription s "
                        "WHERE s.cabinet_id = CAST(:cabinet_id AS uuid) "
                        "LIMIT 1"
                    ),
                    {"cabinet_id": cabinet_id},
                )
                sub_row = row.fetchone()

                if sub_row and sub_row[0] == "active":
                    return True

                # Fallback: check trial period on cabinet
                row2 = await conn.execute(
                    text(
                        "SELECT trial_ends_at "
                        "FROM cabinet "
                        "WHERE id = CAST(:cabinet_id AS uuid) "
                        "LIMIT 1"
                    ),
                    {"cabinet_id": cabinet_id},
                )
                cab_row = row2.fetchone()
                if cab_row and cab_row[0] is not None:
                    trial_ends_at = cab_row[0]
                    # Make timezone-aware if needed
                    if trial_ends_at.tzinfo is None:
                        trial_ends_at = trial_ends_at.replace(tzinfo=timezone.utc)
                    if trial_ends_at > now:
                        return True

            return False

        except Exception as exc:
            # On DB error: allow request through (fail open to avoid outage)
            log.error("[sub_guard] DB check error for cabinet %s: %s", cabinet_id, exc)
            return True


def _extract_cabinet_id(request: Request) -> Optional[str]:
    """Decode JWT (without verification) to extract cabinet_id claim."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None

    token = auth_header.removeprefix("Bearer ")
    try:
        claims = jose_jwt.decode(
            token,
            key="",
            options={
                "verify_signature": False,
                "verify_exp": False,
                "verify_aud": False,
            },
        )
        # cabinet_id is injected as a custom claim by the Auth0 Action
        from app.core.config import get_settings
        ns = get_settings().AUTH0_CLAIM_NAMESPACE
        cabinet_id = claims.get(f"{ns}cabinet_id")
        if cabinet_id:
            return str(cabinet_id)
    except Exception:
        pass

    return None
