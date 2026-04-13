"""AES-256-GCM encryption for patient PII fields.

Key derivation: HKDF(master_key, info=patient_id.bytes, hash=SHA-256, length=32)
One unique key per patient — compromise of one patient key does not expose others.

Storage format (single DB text column): "v1:<nonce_b64>:<ciphertext_b64>"
"""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from functools import lru_cache
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_STORAGE_VERSION = "v1"
_NONCE_BYTES = 12   # 96-bit nonce for GCM (NIST recommendation)
_KEY_BYTES = 32     # 256-bit AES key


# ── Master key loading ────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _load_master_key() -> bytes:
    """Load the master encryption key.

    In development: hex string from PATIENT_ENCRYPTION_MASTER_KEY env var.
    In production: replace this function with an HSM call (see hsm.py).
    """
    from app.core.config import get_settings  # local import avoids circular dep
    raw = get_settings().PATIENT_ENCRYPTION_MASTER_KEY
    if not raw:
        raise RuntimeError(
            "PATIENT_ENCRYPTION_MASTER_KEY is not set. "
            "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    try:
        key = bytes.fromhex(raw)
    except ValueError as exc:
        raise RuntimeError("PATIENT_ENCRYPTION_MASTER_KEY must be a hex string") from exc
    if len(key) not in (16, 24, 32):
        raise RuntimeError("PATIENT_ENCRYPTION_MASTER_KEY must be 32, 48, or 64 hex chars")
    return key


# ── Key derivation ────────────────────────────────────────────────────────────

def _derive_patient_key(master_key: bytes, patient_id: UUID) -> bytes:
    """Derive a per-patient 256-bit AES key using HKDF-SHA256.

    info = patient_id.bytes ensures each patient has a unique derived key.
    A compromise of one patient key does not reveal the master key or other keys.
    """
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=_KEY_BYTES,
        salt=None,           # salt is optional when master_key is already strong
        info=patient_id.bytes,
    )
    return hkdf.derive(master_key)


# ── Public API ────────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class EncryptedField:
    """Encrypted field value, suitable for storing in a single DB text column."""

    nonce_b64: str
    ciphertext_b64: str

    def to_db(self) -> str:
        """Serialize to a single string for storage in a Text column."""
        return f"{_STORAGE_VERSION}:{self.nonce_b64}:{self.ciphertext_b64}"

    @classmethod
    def from_db(cls, value: str) -> "EncryptedField":
        """Parse a value previously produced by to_db()."""
        parts = value.split(":", 2)
        if len(parts) != 3 or parts[0] != _STORAGE_VERSION:
            raise ValueError(f"Invalid encrypted field format: {value[:30]!r}")
        return cls(nonce_b64=parts[1], ciphertext_b64=parts[2])


def encrypt(plaintext: str, patient_id: UUID) -> EncryptedField:
    """Encrypt *plaintext* with a key derived from *patient_id*.

    Returns an EncryptedField; call .to_db() to get the storage string.
    """
    master_key = _load_master_key()
    key = _derive_patient_key(master_key, patient_id)
    nonce = os.urandom(_NONCE_BYTES)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return EncryptedField(
        nonce_b64=base64.b64encode(nonce).decode(),
        ciphertext_b64=base64.b64encode(ciphertext).decode(),
    )


def decrypt(encrypted: EncryptedField | str, patient_id: UUID) -> str:
    """Decrypt an EncryptedField (or a raw db string) for the given patient.

    Raises ValueError on authentication tag mismatch (tampered ciphertext).
    """
    if isinstance(encrypted, str):
        encrypted = EncryptedField.from_db(encrypted)

    master_key = _load_master_key()
    key = _derive_patient_key(master_key, patient_id)
    nonce = base64.b64decode(encrypted.nonce_b64)
    ciphertext = base64.b64decode(encrypted.ciphertext_b64)
    aesgcm = AESGCM(key)
    try:
        plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    except InvalidTag as exc:
        raise ValueError("Decryption failed: authentication tag mismatch") from exc
    return plaintext.decode("utf-8")
