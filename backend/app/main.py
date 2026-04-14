from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from app.core.config import get_settings
from app.middleware.rate_limit import RateLimitMiddleware
from app.routers import auth
from app.routers import documents as documents_router
from app.routers import interactions as interactions_router
from app.routers import rag as rag_router
from app.routers import soap as soap_router
from app.routers import transcription as transcription_router

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: open Redis pool. Shutdown: close it."""
    import redis.asyncio as aioredis
    app.state.redis = aioredis.from_url(
        settings.REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
    )
    yield
    await app.state.redis.aclose()


app = FastAPI(
    title="MédecinAI API",
    version="0.1.0",
    docs_url="/docs" if settings.APP_ENV != "production" else None,
    redoc_url="/redoc" if settings.APP_ENV != "production" else None,
    lifespan=lifespan,
)

# ── Rate limiting (Redis) ─────────────────────────────────────────────────────
# Added before CORS so rate limit runs after CORS headers are attached
# (Starlette middleware executes in LIFO order — last added = first executed)
app.add_middleware(
    RateLimitMiddleware,
    redis_url=settings.REDIS_URL,
    enabled=settings.APP_ENV != "test",
)

# ── CORS — must be added last (executed first in LIFO chain) ──────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
    expose_headers=["X-Request-ID", "X-RateLimit-Limit", "X-RateLimit-Remaining"],
)

# ── HSTS (production) ─────────────────────────────────────────────────────────
# TLS 1.3 is enforced by the Nginx reverse proxy; we add the HSTS header here
# so it is also present in FastAPI's responses (belt-and-suspenders).
if settings.APP_ENV == "production":
    @app.middleware("http")
    async def add_hsts(request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains; preload"
        )
        return response

# ── Prometheus metrics ────────────────────────────────────────────────────────
Instrumentator().instrument(app).expose(app, endpoint="/metrics")

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(auth.router)
app.include_router(documents_router.router)
app.include_router(interactions_router.router)
app.include_router(rag_router.router)
app.include_router(soap_router.router)
app.include_router(transcription_router.router)


@app.get("/health", tags=["ops"])
async def health() -> dict:
    return {"status": "ok"}
