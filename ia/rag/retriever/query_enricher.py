"""Query enricher: augment the raw transcript/question with clinical context.

Purpose:
    The raw query ("douleur thoracique") is enriched with patient data and
    doctor context so the embedding model has more signal for retrieval:

        Specialité: cardiologie
        Patient: DFG 45 ml/min, traitement: metformine 1g, aspirine 100mg
        Allergies: pénicilline
        "douleur thoracique irradiant au bras gauche"

    This enriched string is embedded and used for both dense and sparse retrieval.
    It is NOT sent to the LLM — only to the embedding model (on-premise).

Usage:
    enriched = enrich_query(
        query="douleur thoracique",
        specialty="cardiologie",
        active_drugs=["metformine 1g", "aspirine 100mg"],
        allergies=["pénicilline"],
        dfg=45,
        is_pregnant=False,
    )
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class PatientContext:
    """Clinical context for query enrichment. All fields are optional."""
    specialty: str = ""
    active_drugs: list[str] = field(default_factory=list)
    allergies: list[str] = field(default_factory=list)
    antecedents: list[str] = field(default_factory=list)
    dfg: Optional[float] = None          # mL/min/1.73m²
    is_pregnant: bool = False
    poids_kg: Optional[float] = None
    age: Optional[int] = None


@dataclass(frozen=True, slots=True)
class EnrichedQuery:
    text: str                    # Full enriched string for embedding
    raw_query: str               # Original query
    has_ccam_code: bool          # True → boost sparse weight
    has_cim10_code: bool         # True → boost sparse weight
    sparse_boost: float          # RRF weight for BM25 (0.3 default, 0.7 if codes detected)
    dense_boost: float           # RRF weight for dense (0.7 default, 0.3 if codes detected)


# ── Regex for code detection ──────────────────────────────────────────────────

_CCAM_RE = re.compile(r"\b[A-Z]{4}\d{3}(?:\+\d+)?\b")
_CIM10_RE = re.compile(r"\b[A-Z]\d{2}(?:\.\d{1,2})?\b")

_DFG_SEVERE_THRESHOLD = 60.0   # mL/min — below this → flag IRC in enrichment


def enrich_query(
    query: str,
    *,
    specialty: str = "",
    active_drugs: Optional[list[str]] = None,
    allergies: Optional[list[str]] = None,
    antecedents: Optional[list[str]] = None,
    dfg: Optional[float] = None,
    is_pregnant: bool = False,
    poids_kg: Optional[float] = None,
    age: Optional[int] = None,
) -> EnrichedQuery:
    """Build an enriched query string for embedding.

    The enrichment is designed to improve recall for medically relevant chunks
    without polluting the semantic space with unrelated terms.

    Args:
        query:        Raw transcript excerpt or clinical question.
        specialty:    Doctor specialty (e.g. "cardiologie").
        active_drugs: Patient's current medications (DCI names preferred).
        allergies:    Known allergies.
        antecedents:  Relevant past history terms.
        dfg:          GFR value; triggers IRC flag if < 60.
        is_pregnant:  Triggers pregnancy flag in enrichment.
        poids_kg:     Patient weight for dosage context.
        age:          Patient age.

    Returns:
        EnrichedQuery with the assembled text and RRF weight hints.
    """
    active_drugs = active_drugs or []
    allergies = allergies or []
    antecedents = antecedents or []

    parts: list[str] = []

    # 1. Doctor specialty context
    if specialty:
        parts.append(f"Spécialité: {specialty.lower()}")

    # 2. Patient demographic flags
    flags: list[str] = []
    if dfg is not None and dfg < _DFG_SEVERE_THRESHOLD:
        flags.append(f"IRC (DFG {dfg:.0f} mL/min)")
    if is_pregnant:
        flags.append("grossesse")
    if age is not None:
        flags.append(f"âge {age} ans")
    if poids_kg is not None:
        flags.append(f"poids {poids_kg:.1f} kg")
    if flags:
        parts.append(f"Patient: {', '.join(flags)}")

    # 3. Active medications (for interaction context)
    if active_drugs:
        drugs_str = ", ".join(d.lower() for d in active_drugs[:10])  # cap at 10
        parts.append(f"Traitements actifs: {drugs_str}")

    # 4. Allergies
    if allergies:
        allerg_str = ", ".join(a.lower() for a in allergies[:5])
        parts.append(f"Allergies: {allerg_str}")

    # 5. Relevant antecedents
    if antecedents:
        ant_str = ", ".join(a.lower() for a in antecedents[:5])
        parts.append(f"Antécédents: {ant_str}")

    # 6. The actual query (always last)
    parts.append(query.strip())

    enriched_text = "\n".join(parts)

    # Detect code patterns to adjust RRF weights
    has_ccam = bool(_CCAM_RE.search(query))
    has_cim10 = bool(_CIM10_RE.search(query))
    code_detected = has_ccam or has_cim10

    return EnrichedQuery(
        text=enriched_text,
        raw_query=query,
        has_ccam_code=has_ccam,
        has_cim10_code=has_cim10,
        # Boost sparse when structured codes are detected (exact-match matters more)
        sparse_boost=0.7 if code_detected else 0.3,
        dense_boost=0.3 if code_detected else 0.7,
    )


def enrich_from_context(query: str, ctx: PatientContext) -> EnrichedQuery:
    """Convenience wrapper accepting a PatientContext dataclass."""
    return enrich_query(
        query,
        specialty=ctx.specialty,
        active_drugs=ctx.active_drugs,
        allergies=ctx.allergies,
        antecedents=ctx.antecedents,
        dfg=ctx.dfg,
        is_pregnant=ctx.is_pregnant,
        poids_kg=ctx.poids_kg,
        age=ctx.age,
    )
