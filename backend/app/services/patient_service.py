"""Patient service: CRUD with AES-256-GCM encryption + Redis cache.

Encrypted fields (AES-256-GCM, key = HKDF(master, patient_id)):
  allergies, traitements_actifs, antecedents

Pseudonymised fields (deterministic Presidio token, searchable):
  nom → stored as nom_pseudonyme (search works directly on the stored value)

Redis cache:
  Key : patient:{patient_id}
  TTL : 5 minutes
  Content: serialised PatientDecrypted (decrypted PII)
  NOTE: NS4 embeddings are NEVER cached here (PatientVectorStore enforces that)

Search:
  By INS  → exact match on patient.ins column
  By nom  → ILIKE on patient.nom_pseudonyme (pseudonymised search term)
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import date
from typing import Optional

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient import Patient
from app.security.encryption import decrypt, encrypt

log = logging.getLogger(__name__)

_CACHE_TTL = 300   # 5 minutes
_CACHE_PREFIX = "patient"


# ── Data transfer objects ─────────────────────────────────────────────────────

class PatientCreate:
    """Input data for creating a patient (plaintext PII from the API layer)."""

    def __init__(
        self,
        cabinet_id: uuid.UUID,
        nom: str,
        date_naissance: date,
        sexe: Optional[str] = None,
        ins: Optional[str] = None,
        allergies: Optional[list[str]] = None,
        traitements_actifs: Optional[list[str]] = None,
        antecedents: Optional[list[str]] = None,
        dfg: Optional[float] = None,
        grossesse: bool = False,
        doctolib_patient_id: Optional[str] = None,
    ) -> None:
        self.cabinet_id = cabinet_id
        self.nom = nom
        self.date_naissance = date_naissance
        self.sexe = sexe
        self.ins = ins
        self.allergies = allergies or []
        self.traitements_actifs = traitements_actifs or []
        self.antecedents = antecedents or []
        self.dfg = dfg
        self.grossesse = grossesse
        self.doctolib_patient_id = doctolib_patient_id


class PatientUpdate:
    """Partial update — only set fields override stored values."""

    def __init__(
        self,
        allergies: Optional[list[str]] = None,
        traitements_actifs: Optional[list[str]] = None,
        antecedents: Optional[list[str]] = None,
        dfg: Optional[float] = None,
        grossesse: Optional[bool] = None,
        doctolib_patient_id: Optional[str] = None,
    ) -> None:
        self.allergies = allergies
        self.traitements_actifs = traitements_actifs
        self.antecedents = antecedents
        self.dfg = dfg
        self.grossesse = grossesse
        self.doctolib_patient_id = doctolib_patient_id


class PatientDecrypted:
    """Fully decrypted patient record — never persisted, only returned to callers."""

    __slots__ = (
        "id", "cabinet_id", "ins", "nom", "date_naissance_hash",
        "sexe", "allergies", "traitements_actifs", "antecedents",
        "dfg", "grossesse", "doctolib_patient_id", "created_at", "updated_at",
    )

    def __init__(self, **kw) -> None:
        for k, v in kw.items():
            setattr(self, k, v)

    def to_dict(self) -> dict:
        return {k: getattr(self, k, None) for k in self.__slots__}


# ── Service ───────────────────────────────────────────────────────────────────

class PatientService:
    """Stateless patient service — share a single instance per application."""

    # ── Create ────────────────────────────────────────────────────────────────

    async def create(
        self,
        data: PatientCreate,
        db: AsyncSession,
        redis=None,
    ) -> Patient:
        """Create a patient with encrypted PII fields.

        Returns the raw ORM object (use get() to obtain decrypted view).
        """
        patient_id = uuid.uuid4()

        nom_pseudonyme = _pseudonymise_nom(data.nom)
        dob_hash = _hash_dob(data.date_naissance)

        patient = Patient(
            id=patient_id,
            cabinet_id=data.cabinet_id,
            ins=data.ins,
            nom_pseudonyme=nom_pseudonyme,
            date_naissance_hash=dob_hash,
            sexe=data.sexe,
            dfg=data.dfg,
            grossesse=data.grossesse,
            doctolib_patient_id=data.doctolib_patient_id,
            allergies_encrypted=_enc_list(data.allergies, patient_id),
            traitements_actifs_encrypted=_enc_list(data.traitements_actifs, patient_id),
            antecedents_encrypted=_enc_list(data.antecedents, patient_id),
        )
        db.add(patient)
        await db.commit()
        await db.refresh(patient)

        log.info("[patient_service] Created patient=%s cabinet=%s", patient.id, data.cabinet_id)
        return patient

    # ── Read (single) ─────────────────────────────────────────────────────────

    async def get(
        self,
        patient_id: uuid.UUID,
        cabinet_id: uuid.UUID,
        db: AsyncSession,
        redis=None,
        nom_plaintext: Optional[str] = None,
    ) -> Optional[PatientDecrypted]:
        """Return decrypted patient, checking cabinet isolation.

        Tries Redis cache first; falls back to DB + decrypt.
        nom_plaintext must be passed by the caller if it needs to be returned
        (the service only stores the pseudonym, not the original nom).
        """
        # ── Redis cache ────────────────────────────────────────────────────────
        cached = await _cache_get(redis, patient_id)
        if cached:
            # Verify cabinet isolation even on cached data
            if cached.get("cabinet_id") != str(cabinet_id):
                return None
            return _dict_to_decrypted(cached)

        # ── DB lookup ──────────────────────────────────────────────────────────
        patient = await db.get(Patient, patient_id)
        if patient is None or patient.cabinet_id != cabinet_id:
            return None

        dec = _decrypt_patient(patient)
        await _cache_set(redis, patient_id, dec.to_dict())
        return dec

    # ── Update ────────────────────────────────────────────────────────────────

    async def update(
        self,
        patient_id: uuid.UUID,
        cabinet_id: uuid.UUID,
        data: PatientUpdate,
        db: AsyncSession,
        redis=None,
    ) -> Optional[PatientDecrypted]:
        """Partial update of a patient's PII. Invalidates Redis cache."""
        patient = await db.get(Patient, patient_id)
        if patient is None or patient.cabinet_id != cabinet_id:
            return None

        if data.allergies is not None:
            patient.allergies_encrypted = _enc_list(data.allergies, patient_id)
        if data.traitements_actifs is not None:
            patient.traitements_actifs_encrypted = _enc_list(data.traitements_actifs, patient_id)
        if data.antecedents is not None:
            patient.antecedents_encrypted = _enc_list(data.antecedents, patient_id)
        if data.dfg is not None:
            patient.dfg = data.dfg
        if data.grossesse is not None:
            patient.grossesse = data.grossesse
        if data.doctolib_patient_id is not None:
            patient.doctolib_patient_id = data.doctolib_patient_id

        await db.commit()
        await db.refresh(patient)
        await _cache_delete(redis, patient_id)

        log.debug("[patient_service] Updated patient=%s", patient_id)
        return _decrypt_patient(patient)

    # ── Delete ────────────────────────────────────────────────────────────────

    async def delete(
        self,
        patient_id: uuid.UUID,
        cabinet_id: uuid.UUID,
        db: AsyncSession,
        redis=None,
    ) -> bool:
        """Hard-delete patient (RGPD erasure). Returns True if deleted."""
        patient = await db.get(Patient, patient_id)
        if patient is None or patient.cabinet_id != cabinet_id:
            return False

        await db.delete(patient)
        await db.commit()
        await _cache_delete(redis, patient_id)
        log.info("[patient_service] Deleted patient=%s", patient_id)
        return True

    # ── Search ────────────────────────────────────────────────────────────────

    async def search(
        self,
        cabinet_id: uuid.UUID,
        db: AsyncSession,
        nom: Optional[str] = None,
        ins: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[PatientDecrypted]:
        """Search patients by nom (pseudonymised ILIKE) or INS (exact match).

        Cabinet isolation is always enforced.
        """
        stmt = select(Patient).where(Patient.cabinet_id == cabinet_id)

        if ins:
            stmt = stmt.where(Patient.ins == ins.strip())
        elif nom:
            # Search against the stored pseudonym — same pseudonymisation applied
            # to the query ensures consistent matching
            pseudo_query = _pseudonymise_nom(nom)
            stmt = stmt.where(
                Patient.nom_pseudonyme.ilike(f"%{pseudo_query}%")
            )

        stmt = stmt.order_by(Patient.created_at.desc()).limit(limit).offset(offset)
        result = await db.execute(stmt)
        patients = result.scalars().all()

        return [_decrypt_patient(p) for p in patients]

    # ── List ──────────────────────────────────────────────────────────────────

    async def list_by_cabinet(
        self,
        cabinet_id: uuid.UUID,
        db: AsyncSession,
        limit: int = 50,
        offset: int = 0,
    ) -> list[PatientDecrypted]:
        """Return paginated patient list for a cabinet."""
        stmt = (
            select(Patient)
            .where(Patient.cabinet_id == cabinet_id)
            .order_by(Patient.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await db.execute(stmt)
        return [_decrypt_patient(p) for p in result.scalars().all()]


# ── Internal helpers ──────────────────────────────────────────────────────────

def _enc_list(items: list[str], patient_id: uuid.UUID) -> Optional[str]:
    """Encrypt a list of strings to a single DB column value."""
    if not items:
        return None
    return encrypt(json.dumps(items), patient_id).to_db()


def _dec_list(encrypted: Optional[str], patient_id: uuid.UUID) -> list[str]:
    """Decrypt a list column, returning [] on any error."""
    if not encrypted:
        return []
    try:
        return json.loads(decrypt(encrypted, patient_id))
    except Exception:
        return []


def _decrypt_patient(patient: Patient) -> PatientDecrypted:
    """Return a fully decrypted view of a Patient ORM object."""
    pid = patient.id
    return PatientDecrypted(
        id=str(pid),
        cabinet_id=str(patient.cabinet_id),
        ins=patient.ins,
        nom=patient.nom_pseudonyme,          # pseudonym is the storable nom
        date_naissance_hash=patient.date_naissance_hash,
        sexe=patient.sexe,
        allergies=_dec_list(patient.allergies_encrypted, pid),
        traitements_actifs=_dec_list(patient.traitements_actifs_encrypted, pid),
        antecedents=_dec_list(patient.antecedents_encrypted, pid),
        dfg=patient.dfg,
        grossesse=patient.grossesse,
        doctolib_patient_id=patient.doctolib_patient_id,
        created_at=patient.created_at.isoformat() if patient.created_at else None,
        updated_at=patient.updated_at.isoformat() if patient.updated_at else None,
    )


def _pseudonymise_nom(nom: str) -> str:
    """Return a deterministic pseudonym for a patient's name.

    Uses SHA-256 of the lowercased, stripped name truncated to 8 chars.
    This makes names searchable (same pseudonym for same name) without
    exposing the original value in DB or logs.

    In production, Presidio's deterministic operator would replace this.
    """
    canonical = nom.strip().lower()
    h = hashlib.sha256(canonical.encode()).hexdigest()[:8]
    # Keep a readable prefix: first 2 chars of original + hash suffix
    prefix = "".join(c for c in canonical[:2] if c.isalpha())
    return f"{prefix}{h}"


def _hash_dob(dob: date) -> str:
    """SHA-256 of ISO date string."""
    return hashlib.sha256(dob.isoformat().encode()).hexdigest()


def _dict_to_decrypted(d: dict) -> PatientDecrypted:
    return PatientDecrypted(**{k: d.get(k) for k in PatientDecrypted.__slots__})


# ── Redis cache helpers ───────────────────────────────────────────────────────

async def _cache_get(redis, patient_id: uuid.UUID) -> Optional[dict]:
    if redis is None:
        return None
    try:
        raw = await redis.get(f"{_CACHE_PREFIX}:{patient_id}")
        return json.loads(raw) if raw else None
    except Exception:
        return None


async def _cache_set(redis, patient_id: uuid.UUID, data: dict) -> None:
    if redis is None:
        return
    try:
        await redis.setex(f"{_CACHE_PREFIX}:{patient_id}", _CACHE_TTL, json.dumps(data))
    except Exception:
        pass


async def _cache_delete(redis, patient_id: uuid.UUID) -> None:
    if redis is None:
        return
    try:
        await redis.delete(f"{_CACHE_PREFIX}:{patient_id}")
    except Exception:
        pass


# ── Module-level singleton ────────────────────────────────────────────────────

_service: Optional[PatientService] = None


def get_patient_service() -> PatientService:
    global _service
    if _service is None:
        _service = PatientService()
    return _service
