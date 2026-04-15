"""VIDAL / BDPM indexer — NS3 + drug_interactions table.

Two operations:

1. index_interactions(csv_path)
   Reads a drug interaction CSV (BDPM / Thériaque format), normalises DCI names
   to lowercase, deduplicates by canonical pair order (drug_a < drug_b), and
   upserts into the `drug_interactions` table.

2. index_notices(data)
   Embeds drug notice texts (name + indication + posologie + CI) into the
   `chunk` table (namespace='vidal') for RAG retrieval.

Supported input formats:
    - BDPM interactions CSV from data.gouv.fr
    - Thériaque-style CSV (semicolon-separated)

Usage:
    from ia.rag.indexer.vidal_indexer import VidalIndexer

    indexer = VidalIndexer()

    # Index interactions:
    stats = indexer.index_interactions("/tmp/bdpm_interactions.csv")

    # Index VIDAL notices (list of dicts with 'dci', 'indication', 'posologie', 'ci'):
    stats = indexer.index_notices(notices_list)
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)

_EMBED_BATCH = 64

# Known commercial → DCI mappings (subset; full table in interaction_checker.py)
_COMMERCIAL_TO_DCI: dict[str, str] = {
    "doliprane": "paracetamol",
    "efferalgan": "paracetamol",
    "dafalgan": "paracetamol",
    "aspegic": "aspirin",
    "kardegic": "aspirin",
    "plavix": "clopidogrel",
    "previscan": "fluindione",
    "coumadine": "warfarin",
    "sintrom": "acenocoumarol",
    "xarelto": "rivaroxaban",
    "eliquis": "apixaban",
    "pradaxa": "dabigatran",
    "tahor": "atorvastatin",
    "zocor": "simvastatin",
    "crestor": "rosuvastatin",
    "glucophage": "metformin",
    "diamicron": "gliclazide",
    "lantus": "insulin_glargine",
    "levothyrox": "levothyroxine",
    "lasilix": "furosemide",
    "aldactone": "spironolactone",
    "lopressor": "metoprolol",
    "tenormin": "atenolol",
    "amlor": "amlodipine",
    "adalate": "nifedipine",
    "kardegic": "aspirin",
    "ibuprofen": "ibuprofen",
    "brufen": "ibuprofen",
    "nurofen": "ibuprofen",
    "voltarene": "diclofenac",
    "ketoprofene": "ketoprofen",
    "profenid": "ketoprofen",
    "celebrex": "celecoxib",
    "zithromax": "azithromycin",
    "augmentin": "amoxicillin_clavulanate",
    "amoxicilline": "amoxicillin",
    "clamoxyl": "amoxicillin",
}


def normalise_dci(name: str) -> str:
    """Normalise a drug name to lowercase canonical DCI form."""
    cleaned = name.strip().lower()
    cleaned = re.sub(r"\s+", " ", cleaned)
    # Strip dosage suffixes (e.g. "metformin 500mg" → "metformin")
    cleaned = re.sub(r"\s+\d+\s*(mg|g|ml|ui|mcg|μg|%)\b.*$", "", cleaned)
    return _COMMERCIAL_TO_DCI.get(cleaned, cleaned)


@dataclass
class InteractionStats:
    upserted: int = 0
    skipped: int = 0
    errors: int = 0


@dataclass
class IndexStats:
    upserted: int = 0
    skipped: int = 0
    errors: int = 0
    content_hash: str = ""
    extra: dict = field(default_factory=dict)


class VidalIndexer:
    """Indexes BDPM/Thériaque drug interactions and VIDAL notices."""

    # ── Interactions ─────────────────────────────────────────────────────────

    def index_interactions(self, csv_path: str) -> InteractionStats:
        """Parse drug interaction CSV and upsert into drug_interactions table.

        Canonical pair ordering: drug_a < drug_b (enforced by DB CHECK constraint).
        Duplicates are silently skipped (ON CONFLICT DO UPDATE).
        """
        raw = open(csv_path, "rb").read()
        rows = _parse_interactions_csv(raw)
        log.info("[VidalIndexer] Parsed %d interaction rows", len(rows))
        return self._upsert_interactions(rows)

    def index_interactions_from_bytes(self, raw: bytes) -> InteractionStats:
        rows = _parse_interactions_csv(raw)
        return self._upsert_interactions(rows)

    def _upsert_interactions(self, rows: list[dict]) -> InteractionStats:
        import psycopg2  # type: ignore[import]
        conn = psycopg2.connect(_db_dsn())
        stats = InteractionStats()

        try:
            with conn.cursor() as cur:
                for row in rows:
                    drug_a = normalise_dci(row.get("drug_a", ""))
                    drug_b = normalise_dci(row.get("drug_b", ""))
                    severity = row.get("severity", "PRECAUTION").upper()
                    description = row.get("description", "")

                    if not drug_a or not drug_b or drug_a == drug_b:
                        stats.skipped += 1
                        continue

                    # Enforce canonical pair ordering
                    if drug_a > drug_b:
                        drug_a, drug_b = drug_b, drug_a

                    try:
                        cur.execute(
                            """
                            INSERT INTO drug_interactions
                                (id, drug_a, drug_b, severity, description, source, created_at)
                            VALUES
                                (uuid_generate_v4(), %s, %s, %s, %s, 'bdpm', NOW())
                            ON CONFLICT (drug_a, drug_b) DO UPDATE
                              SET severity    = EXCLUDED.severity,
                                  description = EXCLUDED.description,
                                  source      = EXCLUDED.source
                            """,
                            (drug_a, drug_b, severity, description),
                        )
                        stats.upserted += 1
                    except Exception as exc:
                        log.warning("[VidalIndexer] Interaction insert failed (%s, %s): %s",
                                    drug_a, drug_b, exc)
                        stats.errors += 1

            conn.commit()

        finally:
            conn.close()

        log.info("[VidalIndexer] Interactions: upserted=%d skipped=%d errors=%d",
                 stats.upserted, stats.skipped, stats.errors)
        return stats

    # ── VIDAL notices (NS3) ───────────────────────────────────────────────────

    def index_notices(
        self,
        notices: list[dict],
        doc_id: Optional[str] = None,
    ) -> IndexStats:
        """Embed and index VIDAL drug notices into the chunk table (namespace='vidal').

        Each notice dict should have at minimum: 'dci' (str).
        Optional keys: 'indication', 'posologie', 'ci', 'grossesse', 'insuffisance_renale'.

        The text for each chunk:
            "{dci}. Indication: {indication}. Posologie: {posologie}. CI: {ci}."
        """
        if not notices:
            return IndexStats()

        content_hash = hashlib.sha256(
            json.dumps(notices, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()

        if doc_id is None:
            doc_id = _ensure_global_document(
                source="vidal",
                filename=f"vidal_{content_hash[:8]}.json",
                content_hash=content_hash,
            )

        chunks = [_notice_to_chunk(n) for n in notices]
        stats = self._upsert_notices(doc_id, chunks, notices)
        stats.content_hash = content_hash
        return stats

    def _upsert_notices(
        self,
        doc_id: str,
        chunks: list[str],
        notices: list[dict],
    ) -> IndexStats:
        import numpy as np
        import psycopg2  # type: ignore[import]
        from psycopg2.extras import register_vector  # type: ignore[import]
        from ia.embedding.service import get_embedding_service

        service = get_embedding_service()
        conn = psycopg2.connect(_db_dsn())
        stats = IndexStats()

        try:
            register_vector(conn)

            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM chunk WHERE document_id = %s AND namespace = 'vidal'",
                    (uuid.UUID(doc_id),),
                )
            conn.commit()

            for batch_start in range(0, len(chunks), _EMBED_BATCH):
                batch_texts = chunks[batch_start: batch_start + _EMBED_BATCH]
                batch_notices = notices[batch_start: batch_start + _EMBED_BATCH]

                try:
                    vectors = service.embed(batch_texts)
                except Exception as exc:
                    log.error("[VidalIndexer] Embed failed at offset %d: %s", batch_start, exc)
                    stats.errors += len(batch_texts)
                    continue

                with conn.cursor() as cur:
                    for idx, (text, notice, vec) in enumerate(
                        zip(batch_texts, batch_notices, vectors)
                    ):
                        try:
                            cur.execute(
                                """
                                INSERT INTO chunk
                                    (id, document_id, namespace, text, chunk_index,
                                     metadata, embedding, created_at)
                                VALUES
                                    (%s, %s, 'vidal', %s, %s, %s, %s, NOW())
                                """,
                                (
                                    uuid.uuid4(),
                                    uuid.UUID(doc_id),
                                    text,
                                    batch_start + idx,
                                    json.dumps({
                                        "dci": notice.get("dci", ""),
                                        "grossesse": bool(notice.get("grossesse")),
                                        "insuffisance_renale": notice.get("insuffisance_renale"),
                                        "specialty": None,
                                        "has_grade": None,
                                    }),
                                    np.array(vec, dtype=np.float32),
                                ),
                            )
                            stats.upserted += 1
                        except Exception as exc:
                            log.warning("[VidalIndexer] Notice insert failed: %s", exc)
                            stats.errors += 1

                conn.commit()
                log.debug("[VidalIndexer] Progress: %d/%d",
                          batch_start + len(batch_texts), len(chunks))

        finally:
            conn.close()

        return stats


# ── CSV parsing ───────────────────────────────────────────────────────────────

def _parse_interactions_csv(raw: bytes) -> list[dict]:
    """Parse BDPM or Thériaque drug interaction CSV.

    BDPM format (data.gouv.fr):
        substance_1;substance_2;niveau_de_gravite;description

    Thériaque format:
        nom_1,nom_2,gravite,libelle
    """
    try:
        text = raw.decode("utf-8-sig", errors="replace")
    except Exception:
        text = raw.decode("latin-1", errors="replace")

    # Detect delimiter
    first = text.split("\n", 1)[0]
    delimiter = ";" if first.count(";") >= first.count(",") else ","

    rows: list[dict] = []
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)

    # Normalise column names
    _DRUG_A_KEYS = {"substance_1", "nom_1", "dci_1", "drug_a", "medicament_1"}
    _DRUG_B_KEYS = {"substance_2", "nom_2", "dci_2", "drug_b", "medicament_2"}
    _SEVERITY_KEYS = {"niveau_de_gravite", "gravite", "severity", "niveau"}
    _DESC_KEYS = {"description", "libelle", "detail", "texte"}

    def _find(row, keys):
        for k in keys:
            if k in row:
                return row[k]
        # Case-insensitive fallback
        for col in row:
            if col.lower() in keys:
                return row[col]
        return ""

    # Map severity labels to enum values
    _SEVERITY_MAP = {
        "contre-indication": "CI_ABSOLUE",
        "contre indication": "CI_ABSOLUE",
        "ci absolue": "CI_ABSOLUE",
        "ci_absolue": "CI_ABSOLUE",
        "association déconseillée": "CI_RELATIVE",
        "association deconseillee": "CI_RELATIVE",
        "ci_relative": "CI_RELATIVE",
        "précaution d'emploi": "PRECAUTION",
        "precaution d'emploi": "PRECAUTION",
        "a prendre en compte": "PRECAUTION",
        "precaution": "PRECAUTION",
    }

    for row in reader:
        drug_a = _find(row, _DRUG_A_KEYS)
        drug_b = _find(row, _DRUG_B_KEYS)
        sev_raw = _find(row, _SEVERITY_KEYS).strip().lower()
        severity = _SEVERITY_MAP.get(sev_raw, "PRECAUTION")
        description = _find(row, _DESC_KEYS)

        if drug_a and drug_b:
            rows.append({
                "drug_a": drug_a,
                "drug_b": drug_b,
                "severity": severity,
                "description": description,
            })

    return rows


def _notice_to_chunk(notice: dict) -> str:
    """Convert a drug notice dict to a single text string for embedding."""
    parts = [notice.get("dci", "")]

    if indication := notice.get("indication"):
        parts.append(f"Indication : {indication}")
    if posologie := notice.get("posologie"):
        parts.append(f"Posologie : {posologie}")
    if ci := notice.get("ci"):
        parts.append(f"Contre-indications : {ci}")
    if ir := notice.get("insuffisance_renale"):
        parts.append(f"Insuffisance rénale : {ir}")
    if gro := notice.get("grossesse"):
        parts.append(f"Grossesse : {gro}")

    return ". ".join(p for p in parts if p) + "."


# ── DB helpers ────────────────────────────────────────────────────────────────

def _db_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("+asyncpg", "").replace("postgresql+asyncpg", "postgresql")


def _ensure_global_document(source: str, filename: str, content_hash: str) -> str:
    import psycopg2  # type: ignore[import]
    conn = psycopg2.connect(_db_dsn())
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM document WHERE source = %s AND deprecated = FALSE LIMIT 1",
                (source,),
            )
            row = cur.fetchone()
            if row:
                doc_id = str(row[0])
                cur.execute(
                    "UPDATE document SET content_hash = %s, filename = %s WHERE id = %s",
                    (content_hash, filename, uuid.UUID(doc_id)),
                )
                conn.commit()
                return doc_id

            doc_id = str(uuid.uuid4())
            cur.execute(
                """
                INSERT INTO document
                    (id, type, source, filename, content_hash, deprecated, uploaded_at)
                VALUES
                    (%s, 'global', %s, %s, %s, FALSE, NOW())
                """,
                (uuid.UUID(doc_id), source, filename, content_hash),
            )
            conn.commit()
            return doc_id
    finally:
        conn.close()
