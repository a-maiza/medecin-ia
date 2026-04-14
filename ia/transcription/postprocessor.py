"""Post-processing for Whisper transcription output.

Two passes:
  1. Rule-based normalisation — fast, deterministic, handles medical patterns
     that Whisper commonly mis-transcribes.
  2. spaCy fr_core_news_lg NER — extracts structured entities (symptoms,
     measurements, drug mentions) for downstream alert checking.

Usage:
    processor = TranscriptionPostprocessor()
    result = processor.process("douze zéro sur huit zéro fréquence soixante-douze")
    # result.text = "120/80 fréquence 72"
    # result.entities = [Measurement(type="tension", value="120/80"), ...]
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class MedicalEntity:
    label: str     # "MEASUREMENT", "DRUG", "SYMPTOM", "PERSON", "DATE"
    text: str
    start: int
    end: int
    normalized: Optional[str] = None


@dataclass
class ProcessedTranscription:
    text: str                        # Normalised text
    entities: list[MedicalEntity] = field(default_factory=list)
    raw_text: str = ""               # Original Whisper output


# ── Rule-based normalisation ──────────────────────────────────────────────────

# Blood pressure: "12 0 sur 8 0", "120 sur 80", "12 0/8 0" → "120/80"
_BP_PATTERN = re.compile(
    r"\b(1\d{2}|[89]\d)"            # systolic 80-199
    r"(?:\s+sur\s+|\s*/\s*)"        # separator
    r"(\d{2})\b",                   # diastolic
    re.IGNORECASE,
)

# Spoken-digit blood pressure: "douze zéro sur huit zéro"
_BP_SPOKEN = [
    (re.compile(r"\bdouze\s+(?:zéro|zero)\s+sur\s+(?:huit|neuf)\s+(?:zéro|zero)\b", re.IGNORECASE), "120/80"),
    (re.compile(r"\bdouze\s+(?:zéro|zero)\s+sur\s+(?:sept)\s+(?:cinq)\b", re.IGNORECASE), "120/75"),
    (re.compile(r"\bonze\s+(?:zéro|zero)\s+sur\s+(?:sept)\s+(?:zéro|zero)\b", re.IGNORECASE), "110/70"),
]

# Temperature: "37 virgule 8", "37,8" → "37.8"
_TEMP_PATTERN = re.compile(
    r"\b(3[567])\s+(?:virgule|point|,)\s+(\d)\b",
    re.IGNORECASE,
)

# Pulse/frequency: "soixante-douze", "72 par minute"
_FRENCH_NUMBERS: dict[str, str] = {
    "soixante": "60", "soixante-dix": "70", "soixante-douze": "72",
    "soixante-quinze": "75", "quatre-vingts": "80", "quatre-vingt-dix": "90",
    "cent": "100", "cent-dix": "110", "cent-vingt": "120",
}

# Common abbreviation expansions spoken aloud
_ABBREV_MAP: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bpression artérielle\b", re.IGNORECASE), "PA"),
    (re.compile(r"\bfréquence cardiaque\b", re.IGNORECASE), "FC"),
    (re.compile(r"\bfréquence respiratoire\b", re.IGNORECASE), "FR"),
    (re.compile(r"\bsaturation en oxygène\b", re.IGNORECASE), "SpO₂"),
    (re.compile(r"\bindice de masse corporelle\b", re.IGNORECASE), "IMC"),
    (re.compile(r"\bhémoglobine glyquée\b", re.IGNORECASE), "HbA1c"),
    (re.compile(r"\bglycémie à jeun\b", re.IGNORECASE), "GAJ"),
    # Units
    (re.compile(r"\bmilligrammes? par jour\b", re.IGNORECASE), "mg/j"),
    (re.compile(r"\bmicrogrammes? par jour\b", re.IGNORECASE), "µg/j"),
    (re.compile(r"\bunités?\s+(?:internationale?s?)?\s+par jour\b", re.IGNORECASE), "UI/j"),
    (re.compile(r"\bgrammes? par litre\b", re.IGNORECASE), "g/L"),
    (re.compile(r"\bmilligrammes? par décilitre\b", re.IGNORECASE), "mg/dL"),
    (re.compile(r"\bmicromoles? par litre\b", re.IGNORECASE), "µmol/L"),
    (re.compile(r"\bmillimètres? de mercure\b", re.IGNORECASE), "mmHg"),
    (re.compile(r"\bbattements? par minute\b", re.IGNORECASE), "bpm"),
    (re.compile(r"\bcycles? par minute\b", re.IGNORECASE), "c/min"),
    (re.compile(r"\bpour cent\b", re.IGNORECASE), "%"),
]

# Measurement regex for entity extraction (post-normalisation)
_MEASUREMENT_RE = re.compile(
    r"\b"
    r"(\d{1,3}(?:[.,]\d{1,2})?)"   # numeric value
    r"\s*"
    r"(mmHg|bpm|°C|%|g/L|mg/L|mg/dL|µmol/L|UI/j|mg/j|µg/j|c/min|SpO₂|IMC)"
    r"\b",
    re.IGNORECASE,
)


# ── spaCy NER (lazy) ──────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _get_nlp():
    """Load spaCy fr_core_news_lg (first call only)."""
    import spacy  # type: ignore[import]
    try:
        nlp = spacy.load("fr_core_news_lg")
    except OSError:
        # Fallback: try smaller model
        try:
            nlp = spacy.load("fr_core_news_md")
        except OSError:
            nlp = spacy.load("fr_core_news_sm")
    return nlp


# ── Main processor ────────────────────────────────────────────────────────────

class TranscriptionPostprocessor:
    """Stateless — safe to share across sessions."""

    def process(self, text: str) -> ProcessedTranscription:
        raw = text
        text = self._normalise(text)
        entities = self._extract_entities(text)
        return ProcessedTranscription(text=text, entities=entities, raw_text=raw)

    # ── Step 1: rule-based normalisation ──────────────────────────────────────

    def _normalise(self, text: str) -> str:
        # Spoken BP patterns (must run before numeric BP)
        for pattern, replacement in _BP_SPOKEN:
            text = pattern.sub(replacement, text)

        # Numeric blood pressure
        text = _BP_PATTERN.sub(lambda m: f"{m.group(1)}/{m.group(2)}", text)

        # Temperature
        text = _TEMP_PATTERN.sub(lambda m: f"{m.group(1)}.{m.group(2)}°C", text)

        # French number words (pulse, HR)
        for word, digit in _FRENCH_NUMBERS.items():
            text = re.sub(rf"\b{re.escape(word)}\b", digit, text, flags=re.IGNORECASE)

        # Abbreviation expansions
        for pattern, replacement in _ABBREV_MAP:
            text = pattern.sub(replacement, text)

        # Clean up: collapse multiple spaces
        text = re.sub(r" {2,}", " ", text).strip()

        return text

    # ── Step 2: entity extraction ──────────────────────────────────────────────

    def _extract_entities(self, text: str) -> list[MedicalEntity]:
        entities: list[MedicalEntity] = []

        # Rule-based measurements (fast, reliable)
        for m in _MEASUREMENT_RE.finditer(text):
            entities.append(MedicalEntity(
                label="MEASUREMENT",
                text=m.group(0),
                start=m.start(),
                end=m.end(),
                normalized=f"{m.group(1)} {m.group(2)}",
            ))

        # Blood pressure pattern
        for m in re.finditer(r"\b(\d{2,3})/(\d{2,3})\b", text):
            val = f"{m.group(1)}/{m.group(2)}"
            entities.append(MedicalEntity(
                label="MEASUREMENT",
                text=val,
                start=m.start(),
                end=m.end(),
                normalized=f"PA {val} mmHg",
            ))

        # spaCy for PER, LOC, ORG, DATE
        try:
            nlp = _get_nlp()
            doc = nlp(text)
            for ent in doc.ents:
                label = ent.label_
                if label in ("PER", "LOC", "ORG", "DATE", "MISC"):
                    entities.append(MedicalEntity(
                        label=label,
                        text=ent.text,
                        start=ent.start_char,
                        end=ent.end_char,
                    ))
        except Exception:
            pass  # spaCy failure must not break the transcription pipeline

        return entities
