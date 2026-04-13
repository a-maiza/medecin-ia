# ── Stage 1 : builder ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Dépendances système nécessaires à la compilation (asyncpg, cryptography…)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2 : runtime ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Utilisateur non-root
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

WORKDIR /app

# Dépendances runtime uniquement
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copie des packages installés depuis le builder
COPY --from=builder /install /usr/local

# Code applicatif
COPY --chown=appuser:appgroup app/ ./app/

USER appuser

EXPOSE 8000

# Uvicorn sans reload — le reload est activé uniquement en dev via bind mount
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*"]
