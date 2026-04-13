"""Presidio-based PII pseudonymisation for text sent to the Anthropic API.

Workflow (per transcription session):
  1. pseudonymize(text, session_id) → anonymised text + Redis mapping
  2. Call Claude with anonymised text
  3. restore(text, session_id) → original text (if needed for display)

Token format: <TYPE_hextoken>  e.g. <PERSON_a3f1b2>
Redis key:    pseudo:{session_id}:{token_value}  → original span
TTL:          28800s (8h — matches Auth0 session duration)
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from functools import lru_cache
from typing import Optional

import redis.asyncio as aioredis

log = logging.getLogger(__name__)

_SESSION_TTL = 28800  # 8h

# Entities to detect and replace (Presidio entity types)
_ENTITIES = [
    "PERSON",
    "PHONE_NUMBER",
    "EMAIL_ADDRESS",
    "LOCATION",
    "DATE_TIME",
    "NRP",           # National Registration / Social security
    "IBAN_CODE",
    "CREDIT_CARD",
    "IP_ADDRESS",
    "URL",
    "MEDICAL_LICENSE",
]

# Pattern matching back all pseudonymised tokens for restore()
_TOKEN_RE = re.compile(r"<([A-Z_]+)_([0-9a-f]{6})>")


# ── Lazy-initialised Presidio engines ─────────────────────────────────────────

@lru_cache(maxsize=1)
def _get_analyzer():
    """Build and cache the Presidio AnalyzerEngine for French."""
    from presidio_analyzer import AnalyzerEngine
    from presidio_analyzer.nlp_engine import NlpEngineProvider

    configuration = {
        "nlp_engine_name": "spacy",
        "models": [{"lang_code": "fr", "model_name": "fr_core_news_lg"}],
    }
    provider = NlpEngineProvider(nlp_configuration=configuration)
    nlp_engine = provider.create_engine()
    return AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=["fr"])


@lru_cache(maxsize=1)
def _get_anonymizer():
    from presidio_anonymizer import AnonymizerEngine
    return AnonymizerEngine()


# ── Redis client ───────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _get_redis() -> aioredis.Redis:
    from app.core.config import get_settings
    return aioredis.from_url(get_settings().REDIS_URL, decode_responses=True)


# ── Public API ─────────────────────────────────────────────────────────────────

async def pseudonymize(text: str, session_id: str) -> str:
    """Replace PII in *text* with opaque tokens and store the mapping in Redis.

    Safe to call multiple times within the same session — tokens are stable
    (derived from a hash of the original span).
    """
    analyzer = _get_analyzer()
    anonymizer = _get_anonymizer()

    results = analyzer.analyze(text=text, language="fr", entities=_ENTITIES)

    if not results:
        return text

    # Build Presidio operator configs: replace each entity with a deterministic token
    from presidio_anonymizer.entities import OperatorConfig

    operators: dict = {}
    token_map: dict[str, str] = {}

    for result in results:
        entity_type = result.entity_type
        span = text[result.start:result.end]
        # Short deterministic hash of the span (not cryptographic — just stable within session)
        token_hex = hashlib.sha256(span.encode()).hexdigest()[:6]
        token = f"<{entity_type}_{token_hex}>"
        token_map[token] = span

        operators[entity_type] = OperatorConfig(
            operator_name="replace",
            params={"new_value": token},
        )

    from presidio_anonymizer.entities import AnonymizedResult
    anonymized: AnonymizedResult = anonymizer.anonymize(
        text=text, analyzer_results=results, operators=operators
    )
    anonymised_text: str = anonymized.text

    # Persist token → original mapping in Redis
    redis = _get_redis()
    pipeline = redis.pipeline()
    for token, original in token_map.items():
        key = f"pseudo:{session_id}:{token}"
        pipeline.setex(key, _SESSION_TTL, original)
    await pipeline.execute()

    return anonymised_text


async def restore(text: str, session_id: str) -> str:
    """Replace pseudonymisation tokens in *text* with their original values.

    Tokens absent from Redis (expired or unknown) are left as-is.
    """
    tokens_found = _TOKEN_RE.findall(text)
    if not tokens_found:
        return text

    redis = _get_redis()
    token_strings = [f"<{etype}_{thex}>" for etype, thex in tokens_found]
    keys = [f"pseudo:{session_id}:{t}" for t in token_strings]
    values = await redis.mget(*keys)

    mapping = {
        token: original
        for token, original in zip(token_strings, values)
        if original is not None
    }

    result = text
    for token, original in mapping.items():
        result = result.replace(token, original)
    return result


async def clear_session(session_id: str) -> None:
    """Delete all pseudonymisation tokens for a session (call on logout)."""
    redis = _get_redis()
    pattern = f"pseudo:{session_id}:*"
    cursor = 0
    while True:
        cursor, keys = await redis.scan(cursor=cursor, match=pattern, count=100)
        if keys:
            await redis.delete(*keys)
        if cursor == 0:
            break
