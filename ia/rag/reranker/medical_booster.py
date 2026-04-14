"""Medical booster: apply clinical boost factors to cross-encoder scores.

Applied AFTER cross-encoder reranking to promote clinically critical chunks
based on the patient's specific context.

Boost factors (from REQUIREMENTS.md §6):
    ×2.0  — IRC (DFG < 60 mL/min) — renal dosage chunks
    ×2.0  — Grossesse — pregnancy contraindication chunks
    ×1.8  — Interaction détectée — drug interaction chunks
    ×1.3  — Spécialité — specialty-matching chunks

Boost is applied by multiplying the existing cross-encoder score.
Multiple factors stack multiplicatively.

Usage:
    boosted = boost(
        hits=reranked_hits,
        specialty="cardiologie",
        dfg=45.0,
        is_pregnant=False,
        has_interaction=True,
    )
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from ia.rag.reranker.cross_encoder import RankedPassage


# ── Boost factors ─────────────────────────────────────────────────────────────

_BOOST_IRC = 2.0
_BOOST_GROSSESSE = 2.0
_BOOST_INTERACTION = 1.8
_BOOST_SPECIALTY = 1.3

_DFG_IRC_THRESHOLD = 60.0  # mL/min

# Keywords that identify IRC-relevant chunks
_IRC_KEYWORDS = re.compile(
    r"\b(insuffisance r[eé]nale|DFG|cl[aé]arance cr[eé]atinine|n[eé]phrotoxique|"
    r"ajustement.*dose|posologie.*r[eé]nale|contre.indiqu[eé].*r[eé]nal|"
    r"KDIGO|dialyse|h[eé]modialyse)\b",
    re.IGNORECASE,
)

# Keywords that identify pregnancy-relevant chunks
_PREGNANCY_KEYWORDS = re.compile(
    r"\b(grossesse|enceinte|allaitement|f[oœ]tus|t[eé]ratog[eè]ne|embryotoxique|"
    r"trimestre|CRAT|contre.indiqu[eé].*grossesse)\b",
    re.IGNORECASE,
)

# Keywords that identify drug interaction chunks
_INTERACTION_KEYWORDS = re.compile(
    r"\b(interaction|association d[eé]conseill[eé]e|contre.indiqu[eé]e?|"
    r"potentialise|inhibe|inducteur|substrate|CYP|P-gp|inhibiteur)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class BoostedPassage:
    text: str
    original_score: float   # Cross-encoder score before boosting
    boosted_score: float    # Score after clinical multiplication
    boost_applied: list[str]  # Which factors were applied


def boost(
    hits: list[RankedPassage],
    *,
    specialty: str = "",
    dfg: Optional[float] = None,
    is_pregnant: bool = False,
    has_interaction: bool = False,
) -> list[BoostedPassage]:
    """Apply clinical boost factors and re-sort.

    Args:
        hits:            RankedPassage list from cross-encoder (already sorted).
        specialty:       Doctor specialty for specialty-boost matching.
        dfg:             Patient GFR. If < 60 → IRC boost on renal chunks.
        is_pregnant:     Triggers pregnancy boost on relevant chunks.
        has_interaction: At least one drug interaction was detected → boost interaction chunks.

    Returns:
        List of BoostedPassage sorted by boosted_score descending.
    """
    irc_active = dfg is not None and dfg < _DFG_IRC_THRESHOLD
    specialty_lower = specialty.lower()

    # Build specialty keyword from specialty string
    specialty_pattern = re.compile(
        r"\b" + re.escape(specialty_lower) + r"\b", re.IGNORECASE
    ) if specialty_lower else None

    results: list[BoostedPassage] = []

    for hit in hits:
        text = hit.text
        score = hit.score
        applied: list[str] = []

        if irc_active and _IRC_KEYWORDS.search(text):
            score *= _BOOST_IRC
            applied.append("IRC")

        if is_pregnant and _PREGNANCY_KEYWORDS.search(text):
            score *= _BOOST_GROSSESSE
            applied.append("grossesse")

        if has_interaction and _INTERACTION_KEYWORDS.search(text):
            score *= _BOOST_INTERACTION
            applied.append("interaction")

        if specialty_pattern and specialty_pattern.search(text):
            score *= _BOOST_SPECIALTY
            applied.append("spécialité")

        results.append(BoostedPassage(
            text=text,
            original_score=hit.score,
            boosted_score=score,
            boost_applied=applied,
        ))

    results.sort(key=lambda x: x.boosted_score, reverse=True)
    return results
