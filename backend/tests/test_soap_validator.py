"""Tests for SOAP output validator (Task 14).

Covers:
- JSON parse errors
- Structural schema validation (required keys/sections)
- CCAM code regex extraction
- CIM-10 code regex extraction
- Code traceability (strict mode blocks, non-strict warns)
- Confidence score threshold (< 0.70 → low_confidence flag)
- Markdown fence stripping
- assert_valid_soap helper
"""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("PATIENT_ENCRYPTION_MASTER_KEY", "a" * 64)
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
os.environ.setdefault("AUTH0_DOMAIN", "test.auth0.com")
os.environ.setdefault("AUTH0_CLIENT_ID", "test")
os.environ.setdefault("AUTH0_CLIENT_SECRET", "test")
os.environ.setdefault("AUTH0_AUDIENCE", "test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from ia.soap.output_validator import (  # noqa: E402
    ValidationError,
    ValidationResult,
    _CCAM_RE,
    _CIM10_RE,
    _LOW_CONFIDENCE_THRESHOLD,
    assert_valid_soap,
    validate_soap_output,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _minimal_soap(
    confidence: float = 0.85,
    ccam: str | None = None,
    cim10: str | None = None,
) -> dict:
    """Build a minimal valid SOAP output dict."""
    plan_item: dict = {
        "medicament": "Paracétamol",
        "posologie": "1g x3/j",
        "duree": "5 jours",
        "interaction_flag": False,
    }
    if ccam:
        plan_item["ccam_code"] = ccam

    return {
        "soap": {
            "S": {
                "motif": "Fièvre",
                "plaintes": ["Céphalées", "Asthénie"],
                "context": "Adulte sain sans antécédent notable",
            },
            "O": {
                "constantes": {"TA": "120/80", "FC": "72"},
                "examen_clinique": "Examen normal",
                "resultats": [],
            },
            "A": {
                "diagnostic_principal": {
                    "libelle": "Syndrome grippal",
                    "cim10": cim10 or "J11",
                },
                "diagnostics_diff": [],
                "synthese": "Tableau évocateur de grippe non compliquée",
            },
            "P": {
                "prescriptions": [plan_item],
                "examens": [],
                "arret_travail": {},
                "prochaine_consultation": "Dans 7 jours si pas d'amélioration",
                "messages_patient": [],
            },
        },
        "metadata": {
            "confidence_score": confidence,
            "model": "claude-sonnet-4-6",
            "chunks_used": [],
        },
        "alerts": [],
    }


def _to_raw(d: dict) -> str:
    return json.dumps(d, ensure_ascii=False)


# ── JSON parse errors ─────────────────────────────────────────────────────────

class TestJsonParsing:
    def test_invalid_json_fails(self):
        result = validate_soap_output("not json at all")
        assert result.is_valid is False
        assert any("JSON" in e for e in result.errors)
        assert result.soap_dict is None

    def test_valid_json_succeeds(self):
        result = validate_soap_output(_to_raw(_minimal_soap()))
        assert result.is_valid is True
        assert result.soap_dict is not None

    def test_empty_string_fails(self):
        result = validate_soap_output("")
        assert result.is_valid is False

    def test_non_object_json_fails(self):
        result = validate_soap_output('["array", "not", "object"]')
        assert result.is_valid is False

    def test_markdown_fences_stripped(self):
        raw = "```json\n" + _to_raw(_minimal_soap()) + "\n```"
        result = validate_soap_output(raw)
        assert result.is_valid is True

    def test_markdown_fence_no_lang_stripped(self):
        raw = "```\n" + _to_raw(_minimal_soap()) + "\n```"
        result = validate_soap_output(raw)
        assert result.is_valid is True


# ── Schema structure validation ───────────────────────────────────────────────

class TestSchemaStructure:
    def test_missing_soap_key_fails(self):
        d = _minimal_soap()
        del d["soap"]
        result = validate_soap_output(_to_raw(d))
        assert result.is_valid is False
        assert any("'soap'" in e for e in result.errors)

    def test_missing_metadata_key_fails(self):
        d = _minimal_soap()
        del d["metadata"]
        result = validate_soap_output(_to_raw(d))
        assert result.is_valid is False
        assert any("metadata" in e for e in result.errors)

    def test_soap_not_object_fails(self):
        d = _minimal_soap()
        d["soap"] = "string not object"
        result = validate_soap_output(_to_raw(d))
        assert result.is_valid is False

    def test_missing_S_section_fails(self):
        d = _minimal_soap()
        del d["soap"]["S"]
        result = validate_soap_output(_to_raw(d))
        assert result.is_valid is False
        assert any("'S'" in e for e in result.errors)

    def test_missing_O_section_fails(self):
        d = _minimal_soap()
        del d["soap"]["O"]
        result = validate_soap_output(_to_raw(d))
        assert result.is_valid is False

    def test_missing_A_section_fails(self):
        d = _minimal_soap()
        del d["soap"]["A"]
        result = validate_soap_output(_to_raw(d))
        assert result.is_valid is False

    def test_missing_P_section_fails(self):
        d = _minimal_soap()
        del d["soap"]["P"]
        result = validate_soap_output(_to_raw(d))
        assert result.is_valid is False

    def test_S_missing_motif_fails(self):
        d = _minimal_soap()
        del d["soap"]["S"]["motif"]
        result = validate_soap_output(_to_raw(d))
        assert result.is_valid is False
        assert any("motif" in e for e in result.errors)

    def test_S_missing_plaintes_fails(self):
        d = _minimal_soap()
        del d["soap"]["S"]["plaintes"]
        result = validate_soap_output(_to_raw(d))
        assert result.is_valid is False

    def test_S_missing_context_fails(self):
        d = _minimal_soap()
        del d["soap"]["S"]["context"]
        result = validate_soap_output(_to_raw(d))
        assert result.is_valid is False

    def test_O_missing_examen_clinique_fails(self):
        d = _minimal_soap()
        del d["soap"]["O"]["examen_clinique"]
        result = validate_soap_output(_to_raw(d))
        assert result.is_valid is False

    def test_O_missing_constantes_fails(self):
        d = _minimal_soap()
        del d["soap"]["O"]["constantes"]
        result = validate_soap_output(_to_raw(d))
        assert result.is_valid is False

    def test_A_missing_diagnostic_principal_fails(self):
        d = _minimal_soap()
        del d["soap"]["A"]["diagnostic_principal"]
        result = validate_soap_output(_to_raw(d))
        assert result.is_valid is False

    def test_A_diagnostic_principal_missing_libelle_fails(self):
        d = _minimal_soap()
        del d["soap"]["A"]["diagnostic_principal"]["libelle"]
        result = validate_soap_output(_to_raw(d))
        assert result.is_valid is False

    def test_P_missing_prescriptions_fails(self):
        d = _minimal_soap()
        del d["soap"]["P"]["prescriptions"]
        result = validate_soap_output(_to_raw(d))
        assert result.is_valid is False

    def test_P_prescriptions_not_list_fails(self):
        d = _minimal_soap()
        d["soap"]["P"]["prescriptions"] = "not a list"
        result = validate_soap_output(_to_raw(d))
        assert result.is_valid is False

    def test_metadata_missing_confidence_score_fails(self):
        d = _minimal_soap()
        del d["metadata"]["confidence_score"]
        result = validate_soap_output(_to_raw(d))
        assert result.is_valid is False

    def test_metadata_missing_chunks_used_fails(self):
        d = _minimal_soap()
        del d["metadata"]["chunks_used"]
        result = validate_soap_output(_to_raw(d))
        assert result.is_valid is False


# ── CCAM code regex ───────────────────────────────────────────────────────────

class TestCcamRegex:
    def test_valid_ccam_format(self):
        assert _CCAM_RE.search("CCAM: AHQP001")
        assert _CCAM_RE.search("code GLQP003")
        assert _CCAM_RE.search("ZZQQ999")

    def test_ccam_with_modifier(self):
        assert _CCAM_RE.search("AHQP001+7")

    def test_invalid_ccam_too_few_letters(self):
        # Only 3 uppercase letters — should not match
        assert not _CCAM_RE.search("ABC001")

    def test_invalid_ccam_too_few_digits(self):
        assert not _CCAM_RE.search("ABCD01")

    def test_invalid_ccam_lowercase(self):
        assert not _CCAM_RE.search("ahqp001")

    def test_multiple_codes_extracted(self):
        text = "AHQP001 and GLQP003 both used"
        codes = _CCAM_RE.findall(text)
        assert "AHQP001" in codes
        assert "GLQP003" in codes


# ── CIM-10 code regex ─────────────────────────────────────────────────────────

class TestCim10Regex:
    def test_valid_3char_code(self):
        assert _CIM10_RE.search("J11")
        assert _CIM10_RE.search("I10")

    def test_valid_with_decimal(self):
        assert _CIM10_RE.search("J11.1")
        assert _CIM10_RE.search("E11.65")

    def test_invalid_no_letter_prefix(self):
        assert not _CIM10_RE.search("111")

    def test_invalid_lowercase(self):
        assert not _CIM10_RE.search("j11")

    def test_multiple_extracted(self):
        text = "Diagnoses: J11 and I10 confirmed"
        codes = _CIM10_RE.findall(text)
        assert "J11" in codes
        assert "I10" in codes


# ── Code traceability ─────────────────────────────────────────────────────────

class TestCodeTraceability:
    def test_valid_code_in_chunks_used_metadata(self):
        d = _minimal_soap(ccam="GLQP003")
        # Include both CCAM and the default CIM-10 code (J11) in chunks_used
        d["metadata"]["chunks_used"] = ["chunk-with-GLQP003-in-name", "chunk-has-J11-code"]
        result = validate_soap_output(_to_raw(d), allowed_chunk_ids=[], strict=True)
        assert result.is_valid is True
        assert "GLQP003" not in result.untraceable_codes

    def test_untraceable_code_strict_fails(self):
        d = _minimal_soap(ccam="AHQP001")
        d["metadata"]["chunks_used"] = []
        result = validate_soap_output(_to_raw(d), allowed_chunk_ids=[], strict=True)
        assert result.is_valid is False
        assert "AHQP001" in result.untraceable_codes

    def test_untraceable_code_non_strict_warns(self):
        d = _minimal_soap(ccam="AHQP001")
        d["metadata"]["chunks_used"] = []
        result = validate_soap_output(_to_raw(d), allowed_chunk_ids=[], strict=False)
        assert result.is_valid is True  # non-strict passes
        assert "AHQP001" in result.untraceable_codes
        assert any("AHQP001" in w for w in result.warnings)

    def test_code_in_allowed_chunk_ids_is_traceable(self):
        d = _minimal_soap(ccam="GLQP003")
        d["metadata"]["chunks_used"] = []
        result = validate_soap_output(
            _to_raw(d),
            # Include both CCAM and CIM-10 (J11) codes from the fixture
            allowed_chunk_ids=["ccam-chunk-GLQP003-uuid", "has-chunk-J11-code"],
            strict=True,
        )
        assert result.is_valid is True
        assert "GLQP003" not in result.untraceable_codes

    def test_no_codes_no_traceability_check(self):
        d = _minimal_soap()
        # Remove CIM-10 from diagnosis
        d["soap"]["A"]["diagnostic_principal"].pop("cim10", None)
        result = validate_soap_output(_to_raw(d), allowed_chunk_ids=[], strict=True)
        assert result.is_valid is True
        assert result.untraceable_codes == []

    def test_codes_extracted_from_result(self):
        d = _minimal_soap(cim10="J11")
        raw = _to_raw(d)
        result = validate_soap_output(raw)
        assert result.is_valid is True
        assert "J11" in result.codes_used


# ── Confidence score ──────────────────────────────────────────────────────────

class TestConfidenceScore:
    def test_high_confidence_no_flag(self):
        result = validate_soap_output(_to_raw(_minimal_soap(confidence=0.90)))
        assert result.is_valid is True
        assert result.low_confidence is False
        assert result.confidence_score == pytest.approx(0.90)

    def test_exactly_at_threshold_no_flag(self):
        result = validate_soap_output(_to_raw(_minimal_soap(confidence=0.70)))
        assert result.low_confidence is False

    def test_below_threshold_sets_flag(self):
        result = validate_soap_output(_to_raw(_minimal_soap(confidence=0.65)))
        assert result.low_confidence is True

    def test_below_threshold_adds_warning(self):
        result = validate_soap_output(_to_raw(_minimal_soap(confidence=0.50)))
        assert any("Low confidence" in w or "0.50" in w for w in result.warnings)

    def test_low_confidence_does_not_invalidate(self):
        result = validate_soap_output(_to_raw(_minimal_soap(confidence=0.30)))
        assert result.is_valid is True  # low confidence → warning, not error

    def test_invalid_confidence_type_warns(self):
        d = _minimal_soap()
        d["metadata"]["confidence_score"] = "not-a-number"
        result = validate_soap_output(_to_raw(d))
        assert any("confidence_score" in w for w in result.warnings)


# ── assert_valid_soap helper ──────────────────────────────────────────────────

class TestAssertValidSoap:
    def test_returns_dict_on_valid_input(self):
        result = assert_valid_soap(_to_raw(_minimal_soap()))
        assert isinstance(result, dict)
        assert "soap" in result

    def test_raises_validation_error_on_invalid(self):
        with pytest.raises(ValidationError) as exc_info:
            assert_valid_soap("{}")
        err = exc_info.value
        assert len(err.errors) > 0

    def test_raises_on_non_json(self):
        with pytest.raises(ValidationError):
            assert_valid_soap("garbage data")

    def test_error_message_contains_count(self):
        with pytest.raises(ValidationError) as exc_info:
            assert_valid_soap("{}")
        assert "errors" in str(exc_info.value).lower() or len(exc_info.value.errors) > 0


# ── ValidationResult dataclass ────────────────────────────────────────────────

class TestValidationResult:
    def test_default_fields(self):
        r = ValidationResult(is_valid=False, soap_dict=None)
        assert r.errors == []
        assert r.warnings == []
        assert r.codes_used == []
        assert r.untraceable_codes == []
        assert r.confidence_score is None
        assert r.low_confidence is False

    def test_full_valid_result(self):
        raw = _to_raw(_minimal_soap(confidence=0.85))
        result = validate_soap_output(raw)
        assert result.is_valid is True
        assert result.soap_dict is not None
        assert isinstance(result.soap_dict, dict)
        assert result.errors == []
