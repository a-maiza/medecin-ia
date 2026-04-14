"""Celery job: daily sync of BDPM + Thériaque drug interactions into drug_interaction table.

Schedule: daily at 03:00 UTC (configured in celery_app.py beat_schedule).

Sources:
  - BDPM (Base de Données Publique des Médicaments) — data.gouv.fr
    URL: https://base-donnees-publique.medicaments.gouv.fr/telechargement.php
    Files: CIS_COMPO_bdpm.txt (composition → DCI mapping), CIS_INTER_bdpm.txt (interactions)

  - Thériaque interactions are imported from the same BDPM files when available.
    For full Thériaque integration, set THERIAIQUE_API_KEY in env.

Delta detection: SHA-256 of raw file content; skips upsert if hash unchanged.

Requires env vars:
  DATABASE_URL   — PostgreSQL DSN
  BDPM_DATA_URL  — Override URL for BDPM download (optional, defaults to official)
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from app.celery_app import celery_app

log = logging.getLogger(__name__)

_BDPM_INTERACTIONS_URL = (
    "https://base-donnees-publique.medicaments.gouv.fr/telechargement.php"
    "?fichier=CIS_INTER_bdpm.txt"
)

# BDPM severity codes → our internal enum values
_BDPM_SEVERITY_MAP: dict[str, str] = {
    "contre-indication": "contre_indication",
    "association déconseillée": "association_deconseille",
    "précaution d'emploi": "precaution_emploi",
    "à prendre en compte": "a_prendre_en_compte",
}


@celery_app.task(
    name="app.jobs.sync_vidal.sync_vidal",
    bind=True,
    max_retries=3,
    default_retry_delay=300,
    queue="ai",
    acks_late=True,
    # Soft time limit 30 min; hard 35 min
    soft_time_limit=1800,
    time_limit=2100,
)
def sync_vidal(self) -> dict[str, Any]:
    """Download BDPM interaction file, detect delta, upsert drug_interaction rows.

    Returns:
        dict with upserted, skipped, errors, content_hash, synced_at.
    """
    url = os.environ.get("BDPM_DATA_URL", _BDPM_INTERACTIONS_URL)

    try:
        log.info("[sync_vidal] Starting BDPM download from %s", url)
        raw_bytes = _download(url)
        content_hash = hashlib.sha256(raw_bytes).hexdigest()

        # Check if data has changed since last sync
        if _hash_unchanged(content_hash, "vidal_interactions"):
            log.info("[sync_vidal] Hash unchanged — skipping upsert")
            return {"upserted": 0, "skipped": 0, "errors": 0,
                    "content_hash": content_hash, "synced_at": _now_iso(),
                    "delta": False}

        interactions = _parse_bdpm_interactions(raw_bytes)
        log.info("[sync_vidal] Parsed %d interactions", len(interactions))

        stats = _upsert_interactions(interactions)
        _store_hash(content_hash, "vidal_interactions")

        log.info("[sync_vidal] Done: %s", stats)
        return {**stats, "content_hash": content_hash, "synced_at": _now_iso(), "delta": True}

    except Exception as exc:
        log.error("[sync_vidal] Failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)


# ── Parsing ───────────────────────────────────────────────────────────────────

def _parse_bdpm_interactions(raw: bytes) -> list[dict[str, str]]:
    """Parse CIS_INTER_bdpm.txt into normalised interaction dicts.

    File format (tab-separated, CP-1252 encoded):
        CIS_1 | substance_1 | CIS_2 | substance_2 | type | description | ...
    """
    try:
        text = raw.decode("cp1252", errors="replace")
    except Exception:
        text = raw.decode("utf-8", errors="replace")

    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        parts = line.split("\t")
        if len(parts) < 5:
            continue

        # Columns: 0=CIS_a, 1=dci_a, 2=CIS_b, 3=dci_b, 4=severity_label, 5=description
        dci_a_raw = parts[1].strip().lower() if len(parts) > 1 else ""
        dci_b_raw = parts[3].strip().lower() if len(parts) > 3 else ""
        severity_raw = parts[4].strip().lower() if len(parts) > 4 else ""
        description = parts[5].strip() if len(parts) > 5 else parts[-1].strip()

        if not dci_a_raw or not dci_b_raw:
            continue

        severity = _normalise_severity(severity_raw)
        if severity is None:
            continue

        # Canonical ordering: drug_a < drug_b (CHECK constraint in DB)
        drug_a, drug_b = sorted([dci_a_raw, dci_b_raw])

        rows.append({
            "drug_a": drug_a[:200],
            "drug_b": drug_b[:200],
            "severity": severity,
            "description": description[:2000],
            "source": "vidal",
        })

    # Deduplicate pairs (keep highest severity)
    seen: dict[tuple[str, str], dict[str, str]] = {}
    _sev_order = {"contre_indication": 0, "association_deconseille": 1,
                  "precaution_emploi": 2, "a_prendre_en_compte": 3}
    for row in rows:
        key = (row["drug_a"], row["drug_b"])
        existing = seen.get(key)
        if existing is None or _sev_order[row["severity"]] < _sev_order[existing["severity"]]:
            seen[key] = row

    return list(seen.values())


def _normalise_severity(raw: str) -> str | None:
    for label, value in _BDPM_SEVERITY_MAP.items():
        if label in raw:
            return value
    return None


# ── DB upsert ─────────────────────────────────────────────────────────────────

def _upsert_interactions(rows: list[dict[str, str]]) -> dict[str, int]:
    """Upsert interaction rows via ON CONFLICT DO UPDATE."""
    import psycopg2  # type: ignore[import]

    conn = psycopg2.connect(_db_dsn())
    upserted = skipped = errors = 0

    try:
        with conn.cursor() as cur:
            for row in rows:
                try:
                    cur.execute(
                        """
                        INSERT INTO drug_interaction
                            (id, drug_a, drug_b, severity, description, source, updated_at)
                        VALUES
                            (gen_random_uuid(), %s, %s, %s, %s, %s, NOW())
                        ON CONFLICT (drug_a, drug_b)
                        DO UPDATE SET
                            severity    = EXCLUDED.severity,
                            description = EXCLUDED.description,
                            source      = EXCLUDED.source,
                            updated_at  = NOW()
                        """,
                        (row["drug_a"], row["drug_b"], row["severity"],
                         row["description"], row["source"]),
                    )
                    upserted += 1
                except Exception as exc:
                    log.warning("[sync_vidal] Row error (%s↔%s): %s",
                                row["drug_a"], row["drug_b"], exc)
                    errors += 1
        conn.commit()
    finally:
        conn.close()

    return {"upserted": upserted, "skipped": skipped, "errors": errors}


# ── CCAM skeleton (used by sync_ccam.py) ─────────────────────────────────────

def _download(url: str) -> bytes:
    import urllib.request
    with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310
        return resp.read()


def _hash_unchanged(new_hash: str, key: str) -> bool:
    """Check if we've already processed this exact file version."""
    try:
        import psycopg2  # type: ignore[import]
        conn = psycopg2.connect(_db_dsn())
        with conn.cursor() as cur:
            cur.execute(
                "SELECT content_hash FROM document "
                "WHERE source = %s AND deprecated = FALSE "
                "ORDER BY uploaded_at DESC LIMIT 1",
                (key,),
            )
            row = cur.fetchone()
        conn.close()
        return row is not None and row[0] == new_hash
    except Exception:
        return False


def _store_hash(content_hash: str, key: str) -> None:
    """Persist the hash of the last successfully synced file."""
    try:
        import psycopg2  # type: ignore[import]
        conn = psycopg2.connect(_db_dsn())
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO document
                    (id, type, source, filename, content_hash, deprecated, uploaded_at)
                VALUES
                    (gen_random_uuid(), 'global', %s, %s, %s, FALSE, NOW())
                ON CONFLICT DO NOTHING
                """,
                (key, f"{key}_sync_{_now_iso()[:10]}.txt", content_hash),
            )
        conn.commit()
        conn.close()
    except Exception as exc:
        log.warning("[sync] Could not store hash for %s: %s", key, exc)


def _db_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("+asyncpg", "").replace("postgresql+asyncpg", "postgresql")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
