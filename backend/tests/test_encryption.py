"""Tests for AES-256-GCM encryption (Task 14 — written alongside Task 3)."""
import os
from uuid import UUID, uuid4

import pytest

# Set a deterministic master key for tests before importing encryption
os.environ.setdefault(
    "PATIENT_ENCRYPTION_MASTER_KEY",
    "a" * 64,  # 32 bytes of 0xaa — valid test key, never use in prod
)
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
os.environ.setdefault("AUTH0_DOMAIN", "test.auth0.com")
os.environ.setdefault("AUTH0_CLIENT_ID", "test")
os.environ.setdefault("AUTH0_CLIENT_SECRET", "test")
os.environ.setdefault("AUTH0_AUDIENCE", "test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from app.security.encryption import (  # noqa: E402
    EncryptedField,
    decrypt,
    encrypt,
)


PATIENT_A = uuid4()
PATIENT_B = uuid4()


class TestRoundTrip:
    def test_encrypt_decrypt_basic(self):
        plaintext = "Aspirine 100mg, Metformine 850mg"
        ef = encrypt(plaintext, PATIENT_A)
        result = decrypt(ef, PATIENT_A)
        assert result == plaintext

    def test_empty_string(self):
        ef = encrypt("", PATIENT_A)
        assert decrypt(ef, PATIENT_A) == ""

    def test_unicode_chars(self):
        plaintext = "Allergie: œufs, noix, pénicilline — aucune autre"
        ef = encrypt(plaintext, PATIENT_A)
        assert decrypt(ef, PATIENT_A) == plaintext

    def test_nonce_is_random(self):
        ef1 = encrypt("même texte", PATIENT_A)
        ef2 = encrypt("même texte", PATIENT_A)
        # Two encryptions of the same plaintext must produce different nonces
        assert ef1.nonce_b64 != ef2.nonce_b64
        assert ef1.ciphertext_b64 != ef2.ciphertext_b64

    def test_different_patients_different_keys(self):
        """Keys are patient-specific — decrypt with wrong patient must fail."""
        ef = encrypt("données sensibles", PATIENT_A)
        with pytest.raises(ValueError, match="authentication tag"):
            decrypt(ef, PATIENT_B)

    def test_tampered_ciphertext_raises(self):
        ef = encrypt("original", PATIENT_A)
        # Flip a character in the ciphertext
        bad_ct = ef.ciphertext_b64[:-1] + ("A" if ef.ciphertext_b64[-1] != "A" else "B")
        bad_ef = EncryptedField(nonce_b64=ef.nonce_b64, ciphertext_b64=bad_ct)
        with pytest.raises(ValueError, match="authentication tag"):
            decrypt(bad_ef, PATIENT_A)

    def test_tampered_nonce_raises(self):
        ef = encrypt("original", PATIENT_A)
        bad_nonce = ef.nonce_b64[:-1] + ("A" if ef.nonce_b64[-1] != "A" else "B")
        bad_ef = EncryptedField(nonce_b64=bad_nonce, ciphertext_b64=ef.ciphertext_b64)
        with pytest.raises(ValueError, match="authentication tag"):
            decrypt(bad_ef, PATIENT_A)


class TestDbSerialization:
    def test_to_from_db_roundtrip(self):
        plaintext = "INS 123456789012345"
        ef = encrypt(plaintext, PATIENT_A)
        db_str = ef.to_db()
        assert db_str.startswith("v1:")
        restored = EncryptedField.from_db(db_str)
        assert restored.nonce_b64 == ef.nonce_b64
        assert restored.ciphertext_b64 == ef.ciphertext_b64
        assert decrypt(restored, PATIENT_A) == plaintext

    def test_decrypt_from_db_string(self):
        plaintext = "allergie latex"
        db_str = encrypt(plaintext, PATIENT_A).to_db()
        # decrypt() accepts a raw db string directly
        assert decrypt(db_str, PATIENT_A) == plaintext

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError, match="Invalid encrypted field"):
            EncryptedField.from_db("not:a:valid:v1:format")

    def test_wrong_version_raises(self):
        with pytest.raises(ValueError, match="Invalid encrypted field"):
            EncryptedField.from_db("v2:abc:def")


class TestHkdfKeyDerivation:
    def test_same_patient_same_key(self):
        """Deterministic: same patient_id always produces same derived key."""
        ef1 = encrypt("hello", PATIENT_A)
        # If key derivation were random, decryption would fail
        assert decrypt(ef1, PATIENT_A) == "hello"

    def test_known_uuid_derivation(self):
        """Regression: fixed UUID must always decrypt correctly."""
        fixed_uuid = UUID("12345678-1234-5678-1234-567812345678")
        ef = encrypt("régression", fixed_uuid)
        assert decrypt(ef, fixed_uuid) == "régression"
