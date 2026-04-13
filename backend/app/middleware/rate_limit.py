"""Redis-backed sliding-window rate limiter middleware.

Limits per 1-minute bucket:
  - General endpoints:      100 req/min per cabinet_id (or IP if unauthenticated)
  - /embed and /llm paths:   10 req/min per cabinet_id (expensive GPU/LLM ops)

cabinet_id is extracted from the Bearer JWT without full signature verification
(performance optimisation — full verification still happens in route handlers).
If no JWT is present, the client IP is used as the rate limit key.

Counter key: rate:{key}:{minute_bucket}   (auto-expires after 120s)
"""
from __future__ import annotations

import logging
import math
import time
from typing import Optional

from jose import jwt as jose_jwt
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

log = logging.getLogger(__name__)

# Limits (requests per 60-second window)
_DEFAULT_LIMIT = 100
_HEAVY_LIMIT = 10
_HEAVY_PATHS = ("/embed", "/llm", "/soap/generate", "/rag/query")

_WINDOW_SECONDS = 60
_KEY_TTL = _WINDOW_SECONDS * 2  # keep keys for 2 windows to avoid thundering herd


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, redis_url: str, *, enabled: bool = True) -> None:
        super().__init__(app)
        self._enabled = enabled
        self._redis_url = redis_url
        self._redis: Optional[object] = None

    async def _get_redis(self):
        if self._redis is None:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not self._enabled:
            return await call_next(request)

        # Skip rate limiting on health / metrics endpoints
        path = request.url.path
        if path in ("/health", "/metrics", "/docs", "/redoc", "/openapi.json"):
            return await call_next(request)

        limit = _HEAVY_LIMIT if any(path.startswith(p) for p in _HEAVY_PATHS) else _DEFAULT_LIMIT
        rate_key = _extract_rate_key(request)
        bucket = math.floor(time.time() / _WINDOW_SECONDS)
        redis_key = f"rate:{rate_key}:{bucket}"

        try:
            redis = await self._get_redis()
            count = await redis.incr(redis_key)
            if count == 1:
                await redis.expire(redis_key, _KEY_TTL)

            # Attach remaining quota headers (useful for clients)
            remaining = max(0, limit - count)
            response: Response

            if count > limit:
                log.warning("Rate limit exceeded: key=%s path=%s count=%d", rate_key, path, count)
                response = JSONResponse(
                    status_code=429,
                    content={
                        "detail": "Trop de requêtes. Veuillez patienter une minute.",
                        "retry_after": _WINDOW_SECONDS - (int(time.time()) % _WINDOW_SECONDS),
                    },
                    headers={
                        "Retry-After": str(_WINDOW_SECONDS),
                        "X-RateLimit-Limit": str(limit),
                        "X-RateLimit-Remaining": "0",
                    },
                )
                return response

            response = await call_next(request)
            response.headers["X-RateLimit-Limit"] = str(limit)
            response.headers["X-RateLimit-Remaining"] = str(remaining)
            return response

        except Exception as exc:
            # Redis failure must not block the request
            log.error("Rate limiter Redis error: %s", exc)
            return await call_next(request)


def _extract_rate_key(request: Request) -> str:
    """Return cabinet_id from JWT (if present) or client IP as fallback."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.removeprefix("Bearer ")
        try:
            # Decode without verification — we just need the sub/cabinet claim
            claims = jose_jwt.decode(
                token,
                key="",
                options={
                    "verify_signature": False,
                    "verify_exp": False,
                    "verify_aud": False,
                },
            )
            # Use sub (auth0_sub) as rate key — maps 1:1 to a cabinet in practice
            sub = claims.get("sub", "")
            if sub:
                return f"jwt:{sub}"
        except Exception:
            pass

    # Fall back to client IP
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        return f"ip:{forwarded_for.split(',')[0].strip()}"
    host = request.client.host if request.client else "unknown"
    return f"ip:{host}"
