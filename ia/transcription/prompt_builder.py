"""Build Whisper initial_prompt from medical context.

A well-crafted initial_prompt significantly improves Whisper's recognition of
medical terminology, drug names, and specialty-specific vocabulary by biasing
its language model towards the relevant domain.

The prompt must stay under 224 tokens (Whisper hard limit — ~900 French chars).
"""
from __future__ import annotations

# ── Specialty-specific medical vocabulary ─────────────────────────────────────
_SPECIALTY_VOCAB: dict[str, list[str]] = {
    "Médecine générale": [
        "tension artérielle", "fréquence cardiaque", "saturation", "auscultation",
        "douleur thoracique", "dyspnée", "prescription", "ordonnance", "renouvellement",
        "antécédents", "ATCD", "HTA", "diabète", "hypothyroïdie", "BPCO",
    ],
    "Cardiologie": [
        "ECG", "électrocardiogramme", "fibrillation auriculaire", "flutter",
        "insuffisance cardiaque", "fraction d'éjection", "coronaropathie",
        "stent", "angioplastie", "Holter", "échocardiographie", "BNP",
        "troponine", "anticoagulant", "antiagrégant", "bêtabloquant",
    ],
    "Pneumologie": [
        "spirométrie", "VEMS", "CVF", "ratio de Tiffeneau", "asthme",
        "BPCO", "emphysème", "pleurésie", "pneumothorax", "scanner thoracique",
        "bronchodilatateur", "corticoïde inhalé", "oxygénothérapie",
    ],
    "Dermatologie": [
        "lésion érythémateuse", "prurit", "eczéma", "psoriasis", "mélanome",
        "biopsie cutanée", "dermoscopie", "crème émolliente", "corticoïde topique",
        "antihistaminique", "rétinol", "acide salicylique",
    ],
    "Gynécologie-obstétrique": [
        "grossesse", "terme", "accouchement", "écho obstétricale",
        "col de l'utérus", "endomètre", "ovaires", "HCG", "progestérone",
        "contraception", "DIU", "ménopause", "FSH", "LH",
    ],
    "Pédiatrie": [
        "poids", "taille", "périmètre crânien", "développement psychomoteur",
        "vaccin", "antibiotique pédiatrique", "fièvre", "otite", "bronchiolite",
        "amoxicilline", "poids en kilos", "posologie pédiatrique",
    ],
    "Neurologie": [
        "AVC", "AIT", "épilepsie", "migraine", "Parkinson", "sclérose en plaques",
        "IRM cérébrale", "EEG", "NIHSS", "antiépileptique", "neuropathie",
        "paresthésies", "déficit moteur",
    ],
    "Psychiatrie": [
        "trouble dépressif majeur", "anxiété généralisée", "TOC", "schizophrénie",
        "trouble bipolaire", "antidépresseur", "anxiolytique", "neuroleptique",
        "benzodiazépine", "ISRS", "IRSN", "psychothérapie", "TCC",
    ],
}

# Common French medical abbreviations and measurement patterns
_UNIVERSAL_VOCAB = [
    "DFG", "créatinine", "glycémie", "HbA1c", "hémoglobine", "leucocytes",
    "plaquettes", "INR", "TP", "TCA", "CRP", "VS", "ionogramme",
    "bilan hépatique", "TSH", "PSA", "β2 microglobuline",
    "mg par litre", "grammes par litre", "micromoles par litre",
    "milligrammes par décilitre", "millimètres de mercure",
    "international normalised ratio",
]


def build_initial_prompt(
    specialite: str,
    traitements_actifs: list[str] | None = None,
    allergies: list[str] | None = None,
) -> str:
    """Build a Whisper initial_prompt from medical context.

    Whisper uses the prompt as soft guidance — it biases but does not force
    particular word choices. Including drug names and specialty terms significantly
    reduces transcription errors on medical vocabulary.

    Args:
        specialite:         Doctor's speciality (must match a key in _SPECIALTY_VOCAB
                            or fall back to Médecine générale vocabulary).
        traitements_actifs: List of active medication DCI names (lowercase).
        allergies:          List of known allergens (for context only — not safety use).

    Returns:
        Prompt string ≤ ~800 chars (well under Whisper's 224-token limit).
    """
    parts: list[str] = []

    # 1. Speciality vocabulary
    vocab = _SPECIALTY_VOCAB.get(specialite, _SPECIALTY_VOCAB["Médecine générale"])
    parts.append(", ".join(vocab))

    # 2. Universal medical vocabulary
    parts.append(", ".join(_UNIVERSAL_VOCAB))

    # 3. Patient's active medications (most valuable for drug recognition)
    if traitements_actifs:
        meds = [m.strip().lower() for m in traitements_actifs[:20]]  # cap at 20
        parts.append("Traitements : " + ", ".join(meds))

    # 4. Allergens (helps Whisper recognise specific substance names)
    if allergies:
        allergens = [a.strip().lower() for a in allergies[:5]]
        parts.append("Allergies : " + ", ".join(allergens))

    prompt = ". ".join(parts)

    # Truncate to ~800 chars (conservative buffer before 224-token Whisper limit)
    if len(prompt) > 800:
        prompt = prompt[:797] + "..."

    return prompt
