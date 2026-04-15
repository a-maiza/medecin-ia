from functools import lru_cache

from pydantic import AnyHttpUrl, EmailStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ── Database ──────────────────────────────────────────────────────────────
    DATABASE_URL: str

    # ── Auth0 ─────────────────────────────────────────────────────────────────
    AUTH0_DOMAIN: str  # e.g. "medecinai.eu.auth0.com"
    AUTH0_CLIENT_ID: str
    AUTH0_CLIENT_SECRET: str
    AUTH0_AUDIENCE: str  # API identifier, e.g. "https://api.medecinai.fr"
    # Custom claim namespace injected via Auth0 Action
    AUTH0_CLAIM_NAMESPACE: str = "https://medecinai.fr/"

    # ── SMTP (email confirmation) ─────────────────────────────────────────────
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    EMAIL_FROM: str = "noreply@medecinai.fr"
    EMAIL_FROM_NAME: str = "MédecinAI"

    # ── Redis / Celery ────────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"

    # ── Anthropic ─────────────────────────────────────────────────────────────
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-sonnet-4-6"

    # ── App ───────────────────────────────────────────────────────────────────
    APP_ENV: str = "development"
    APP_BASE_URL: str = "http://localhost:3000"
    ALLOWED_ORIGINS: list[str] = ["http://localhost:3000"]

    # ── Security ──────────────────────────────────────────────────────────────
    PATIENT_ENCRYPTION_MASTER_KEY: str = ""

    # ── Stripe ────────────────────────────────────────────────────────────────
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    STRIPE_PRICE_SOLO: str = ""  # Price ID for Plan Solo (~150 €/mois)
    STRIPE_PRICE_CABINET: str = ""  # Price ID for Plan Cabinet
    STRIPE_PRICE_RESEAU: str = ""  # Price ID for Plan Réseau

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def parse_origins(cls, v: str | list) -> list[str]:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",")]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
