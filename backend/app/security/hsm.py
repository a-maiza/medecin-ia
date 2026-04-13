"""HSM key management — stub for OVHcloud KMS integration (P1).

In development/staging: the master key is loaded from the
PATIENT_ENCRYPTION_MASTER_KEY environment variable.

In production (P1): replace _fetch_from_hsm() with a call to the
OVHcloud Key Management Service (KMS) API:
  https://www.ovhcloud.com/en/public-cloud/key-management-service/

The encryption.py module calls _load_master_key() which delegates here.
Swapping the production path does NOT require changes to encryption.py.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_master_key() -> bytes:
    """Return the 32-byte master encryption key.

    Routing logic:
      APP_ENV == "production" → fetch from HSM (not yet implemented)
      otherwise              → load from environment variable
    """
    app_env = os.environ.get("APP_ENV", "development")
    if app_env == "production":
        return _fetch_from_hsm()
    return _load_from_env()


def _load_from_env() -> bytes:
    raw = os.environ.get("PATIENT_ENCRYPTION_MASTER_KEY", "")
    if not raw:
        raise RuntimeError(
            "PATIENT_ENCRYPTION_MASTER_KEY not set. "
            "Generate with: python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    try:
        key = bytes.fromhex(raw)
    except ValueError as exc:
        raise RuntimeError("PATIENT_ENCRYPTION_MASTER_KEY must be a hex string") from exc
    if len(key) != 32:
        raise RuntimeError("PATIENT_ENCRYPTION_MASTER_KEY must be exactly 64 hex chars (32 bytes)")
    return key


def _fetch_from_hsm() -> bytes:
    """Fetch master key from OVHcloud KMS (P1 — not yet implemented).

    Replace this stub with an actual API call, e.g.:
        import httpx
        resp = httpx.post(
            os.environ["OVH_KMS_ENDPOINT"] + "/decrypt",
            headers={"X-OVH-Token": os.environ["OVH_KMS_TOKEN"]},
            json={"key_id": os.environ["OVH_KMS_KEY_ID"]},
        )
        return bytes.fromhex(resp.json()["plaintext"])
    """
    raise NotImplementedError(
        "HSM integration is a P1 feature. "
        "Set APP_ENV != 'production' to use env-var key loading in non-prod environments."
    )
