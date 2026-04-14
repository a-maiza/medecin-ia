"""SOAP output validator: JSON schema check + code traceability.

Validates the raw string returned by Claude against:
    1. Valid JSON (parse check)
    2. OUTPUT_SCHEMA structure (required keys, types)
    3. CCAM code traceability — every code must exist in chunks_used
    4. CIM-10 code traceability — same
    5. Confidence score threshold — score < 0.70 → INFO alert logged

On failure: raises ValidationError with structured details.
On low confidence: logs INFO + sets flag in result.

Usage:
    result = validate_soap_output(raw_json_str, allowed_chunk_ids=["uuid1", ...])
    if result.is_valid:
        soap = result.soap_dict
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger(__name__)

# ── Code regex patterns ───────────────────────────────────────────────────────

# CCAM codes: 4 uppercase letters + 3 digits, optional +modifier
_CCAM_RE = re.compile(r"\b([A-Z]{4}\d{3}(?:\+\d+)?)\b")

# CIM-10 codes: letter + 2 digits, optional .1-2 more digits
_CIM10_RE = re.compile(r"\b([A-Z]\d{2}(?:\.\d{1,2})?)\b")

# Confidence threshold
_LOW_CONFIDENCE_THRESHOLD = 0.70


# ── Validation result ─────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    is_valid: bool
    soap_dict: Optional[dict[str, Any]]
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    codes_used: list[str] = field(default_factory=list)     # all codes found
    untraceable_codes: list[str] = field(default_factory=list)  # codes not in chunks_used
    confidence_score: Optional[float] = None
    low_confidence: bool = False


class ValidationError(Exception):
    def __init__(self, message: str, errors: list[str]) -> None:
        super().__init__(message)
        self.errors = errors


# ── Schema validation ─────────────────────────────────────────────────────────

def _check_structure(d: dict) -> list[str]:
    """Validate the OUTPUT_SCHEMA structure. Returns a list of error messages."""
    errors: list[str] = []

    # Top-level keys
    if "soap" not in d:
        errors.append("Missing top-level 'soap' key")
        return errors  # Can't validate further

    if "metadata" not in d:
        errors.append("Missing top-level 'metadata' key")

    soap = d["soap"]
    if not isinstance(soap, dict):
        errors.append("'soap' must be a JSON object")
        return errors

    # SOAP sections
    for section in ("S", "O", "A", "P"):
        if section not in soap:
            errors.append(f"Missing SOAP section '{section}'")

    # Section S
    s = soap.get("S", {})
    if not isinstance(s, dict):
        errors.append("SOAP.S must be an object")
    else:
        for key in ("motif", "plaintes", "context"):
            if key not in s:
                errors.append(f"SOAP.S missing key '{key}'")

    # Section O
    o = soap.get("O", {})
    if not isinstance(o, dict):
        errors.append("SOAP.O must be an object")
    else:
        if "examen_clinique" not in o:
            errors.append("SOAP.O missing key 'examen_clinique'")
        if "constantes" not in o:
            errors.append("SOAP.O missing key 'constantes'")

    # Section A
    a = soap.get("A", {})
    if not isinstance(a, dict):
        errors.append("SOAP.A must be an object")
    else:
        if "diagnostic_principal" not in a:
            errors.append("SOAP.A missing key 'diagnostic_principal'")
        elif not isinstance(a["diagnostic_principal"], dict):
            errors.append("SOAP.A.diagnostic_principal must be an object")
        else:
            if "libelle" not in a["diagnostic_principal"]:
                errors.append("SOAP.A.diagnostic_principal missing 'libelle'")

    # Section P
    p = soap.get("P", {})
    if not isinstance(p, dict):
        errors.append("SOAP.P must be an object")
    else:
        if "prescriptions" not in p:
            errors.append("SOAP.P missing key 'prescriptions'")
        elif not isinstance(p["prescriptions"], list):
            errors.append("SOAP.P.prescriptions must be an array")

    # Metadata
    meta = d.get("metadata", {})
    if isinstance(meta, dict):
        if "confidence_score" not in meta:
            errors.append("metadata missing 'confidence_score'")
        if "chunks_used" not in meta:
            errors.append("metadata missing 'chunks_used'")

    return errors


def _extract_codes(d: dict) -> list[str]:
    """Extract all CCAM and CIM-10 codes from the SOAP dict."""
    text = json.dumps(d, ensure_ascii=False)
    ccam_codes = _CCAM_RE.findall(text)
    cim10_codes = _CIM10_RE.findall(text)
    return list(set(ccam_codes + cim10_codes))


def _check_code_traceability(
    codes: list[str],
    allowed_chunk_ids: list[str],
    soap_dict: dict,
) -> list[str]:
    """Verify every code in the SOAP came from a provided chunk.

    Strategy: extract codes from the chunks_used list stored in metadata,
    then cross-check. If a code in the SOAP is not in any chunk_id provided
    by the caller (the RAG retriever), flag it.

    Note: We can't check the full chunk content here (would require DB access).
    The caller passes allowed_chunk_ids — any code NOT traceable to those chunks
    is a hallucination risk.

    For MVP: treat as a warning (not a hard block) unless the code format is
    clearly invalid (e.g. made-up 7-char code).
    """
    # Extract codes actually declared in metadata.chunks_used
    meta_chunks = soap_dict.get("metadata", {}).get("chunks_used", [])
    untraceable: list[str] = []

    for code in codes:
        # If the code is referenced in metadata.chunks_used, it's traceable
        code_in_meta = any(code in str(c) for c in meta_chunks)
        code_in_allowed = any(code in cid for cid in allowed_chunk_ids)
        if not code_in_meta and not code_in_allowed:
            untraceable.append(code)

    return untraceable


# ── Main validator ────────────────────────────────────────────────────────────

def validate_soap_output(
    raw: str,
    *,
    allowed_chunk_ids: Optional[list[str]] = None,
    strict: bool = False,
) -> ValidationResult:
    """Validate a raw JSON string from Claude against the SOAP output schema.

    Args:
        raw:               Raw string returned by Claude (must be pure JSON).
        allowed_chunk_ids: IDs of chunks used in retrieval — codes must be traceable.
        strict:            If True, untraceable codes → validation failure.
                           If False (default), untraceable codes → warning only.

    Returns:
        ValidationResult with .is_valid, .soap_dict, .errors, .warnings.
    """
    result = ValidationResult(is_valid=False, soap_dict=None)
    allowed_chunk_ids = allowed_chunk_ids or []

    # Step 1: Strip potential markdown fences
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        cleaned = "\n".join(lines[1:])
        if cleaned.endswith("```"):
            cleaned = cleaned[: cleaned.rfind("```")]

    # Step 2: Parse JSON
    try:
        soap_dict = json.loads(cleaned)
    except json.JSONDecodeError as e:
        result.errors.append(f"JSON parse error: {e}")
        log.warning("[validator] JSON parse failure: %s", e)
        return result

    if not isinstance(soap_dict, dict):
        result.errors.append("Top-level JSON value must be an object")
        return result

    # Step 3: Schema structure check
    schema_errors = _check_structure(soap_dict)
    result.errors.extend(schema_errors)

    # Step 4: Extract codes
    codes = _extract_codes(soap_dict)
    result.codes_used = codes

    # Step 5: Code traceability
    if codes and allowed_chunk_ids is not None:
        untraceable = _check_code_traceability(codes, allowed_chunk_ids, soap_dict)
        result.untraceable_codes = untraceable
        if untraceable:
            msg = f"Codes not traceable to provided chunks: {', '.join(untraceable)}"
            if strict:
                result.errors.append(msg)
            else:
                result.warnings.append(msg)
                log.warning("[validator] %s", msg)

    # Step 6: Confidence score
    confidence = soap_dict.get("metadata", {}).get("confidence_score")
    if confidence is not None:
        try:
            confidence = float(confidence)
            result.confidence_score = confidence
            if confidence < _LOW_CONFIDENCE_THRESHOLD:
                result.low_confidence = True
                result.warnings.append(
                    f"Low confidence score: {confidence:.2f} < {_LOW_CONFIDENCE_THRESHOLD}"
                )
                log.info("[validator] Low confidence SOAP: score=%.2f", confidence)
        except (TypeError, ValueError):
            result.warnings.append(f"Invalid confidence_score: {confidence!r}")

    # Step 7: Determine validity
    result.is_valid = len(result.errors) == 0
    result.soap_dict = soap_dict if result.is_valid else None

    if result.is_valid:
        log.debug(
            "[validator] SOAP valid — %d codes, confidence=%.2f, warnings=%d",
            len(codes),
            result.confidence_score or 0.0,
            len(result.warnings),
        )
    else:
        log.warning("[validator] SOAP invalid — %d errors: %s", len(result.errors), result.errors)

    return result


def assert_valid_soap(raw: str, allowed_chunk_ids: Optional[list[str]] = None) -> dict:
    """Validate and return the parsed dict, raising ValidationError on failure."""
    result = validate_soap_output(raw, allowed_chunk_ids=allowed_chunk_ids, strict=False)
    if not result.is_valid:
        raise ValidationError(
            f"SOAP validation failed ({len(result.errors)} errors)",
            result.errors,
        )
    return result.soap_dict  # type: ignore[return-value]
