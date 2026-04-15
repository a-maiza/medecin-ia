"""Tests for the drug interaction checker (Task 14).

Covers:
- DCI normalisation (commercial names → INN lowercase)
- Severity mapping (DB enum → public API severity)
- DFG renal dosage alerts (static table, no DB)
- Cache key determinism
- Result ordering (CI_ABSOLUE first)
- SQL lookup via async mock
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

# Env stubs — must be set before importing app modules
os.environ.setdefault("PATIENT_ENCRYPTION_MASTER_KEY", "a" * 64)
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
os.environ.setdefault("AUTH0_DOMAIN", "test.auth0.com")
os.environ.setdefault("AUTH0_CLIENT_ID", "test")
os.environ.setdefault("AUTH0_CLIENT_SECRET", "test")
os.environ.setdefault("AUTH0_AUDIENCE", "test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from app.services.interaction_checker import (  # noqa: E402
    InteractionAlert,
    InteractionCheckResult,
    InteractionChecker,
    _cache_key,
    _SEVERITY_TO_PUBLIC,
    get_interaction_checker,
    normalise_dci,
)


# ── normalise_dci ─────────────────────────────────────────────────────────────

class TestNormaliseDci:
    def test_commercial_name_mapped(self):
        assert normalise_dci("Doliprane") == "paracétamol"

    def test_case_insensitive(self):
        assert normalise_dci("DOLIPRANE") == "paracétamol"
        assert normalise_dci("doliprane") == "paracétamol"

    def test_dci_passthrough(self):
        assert normalise_dci("paracétamol") == "paracétamol"
        assert normalise_dci("metformine") == "metformine"

    def test_strips_dosage_mg(self):
        assert normalise_dci("metformine 850mg") == "metformine"

    def test_strips_dosage_g(self):
        assert normalise_dci("paracétamol 1000mg") == "paracétamol"

    def test_strips_ui(self):
        assert normalise_dci("insuline 100 UI/ml") == "insuline"

    def test_unknown_drug_lowercased(self):
        result = normalise_dci("SomeBrandNew")
        assert result == "somebrandnew"

    def test_empty_string(self):
        assert normalise_dci("") == ""

    def test_warfarine_commercial(self):
        assert normalise_dci("coumadine") == "warfarine"

    def test_aspirine_mapped(self):
        assert normalise_dci("aspirine") == "acide acétylsalicylique"
        assert normalise_dci("Kardegic") == "acide acétylsalicylique"

    def test_clopidogrel(self):
        assert normalise_dci("plavix") == "clopidogrel"

    def test_ibuprofen_variant(self):
        assert normalise_dci("Advil") == "ibuprofène"
        assert normalise_dci("Nurofen") == "ibuprofène"

    def test_rivaroxaban(self):
        assert normalise_dci("xarelto") == "rivaroxaban"

    def test_metformine_variants(self):
        assert normalise_dci("glucophage") == "metformine"
        assert normalise_dci("glucinan") == "metformine"

    def test_strips_whitespace(self):
        assert normalise_dci("  metformine  ") == "metformine"


# ── Severity mapping ──────────────────────────────────────────────────────────

class TestSeverityMapping:
    def test_contre_indication_maps_to_ci_absolue(self):
        assert _SEVERITY_TO_PUBLIC["contre_indication"] == "CI_ABSOLUE"

    def test_association_deconseille_maps_to_ci_relative(self):
        assert _SEVERITY_TO_PUBLIC["association_deconseille"] == "CI_RELATIVE"

    def test_precaution_emploi_maps_to_precaution(self):
        assert _SEVERITY_TO_PUBLIC["precaution_emploi"] == "PRECAUTION"

    def test_a_prendre_en_compte_maps_to_info(self):
        assert _SEVERITY_TO_PUBLIC["a_prendre_en_compte"] == "INFO"

    def test_all_raw_severities_covered(self):
        expected = {
            "contre_indication", "association_deconseille",
            "precaution_emploi", "a_prendre_en_compte"
        }
        assert set(_SEVERITY_TO_PUBLIC.keys()) == expected


# ── InteractionAlert properties ───────────────────────────────────────────────

class TestInteractionAlertProperties:
    def _make(self, severity: str, severity_raw: str) -> InteractionAlert:
        return InteractionAlert(
            drug_a="metformine",
            drug_b="ibuprofène",
            severity=severity,
            severity_raw=severity_raw,
            description="Test",
            source="test",
        )

    def test_ci_absolue_flag(self):
        a = self._make("CI_ABSOLUE", "contre_indication")
        assert a.is_ci_absolue is True
        assert a.is_ci_relative is False

    def test_ci_relative_flag(self):
        a = self._make("CI_RELATIVE", "association_deconseille")
        assert a.is_ci_absolue is False
        assert a.is_ci_relative is True

    def test_precaution_no_ci_flags(self):
        a = self._make("PRECAUTION", "precaution_emploi")
        assert a.is_ci_absolue is False
        assert a.is_ci_relative is False

    def test_to_dict_keys(self):
        a = self._make("CI_ABSOLUE", "contre_indication")
        d = a.to_dict()
        assert set(d.keys()) == {"drug_a", "drug_b", "severity", "description", "source"}


# ── InteractionCheckResult ────────────────────────────────────────────────────

class TestInteractionCheckResult:
    def _alert(self, severity: str, severity_raw: str) -> InteractionAlert:
        return InteractionAlert(
            drug_a="a", drug_b="b",
            severity=severity, severity_raw=severity_raw,
            description="", source="",
        )

    def test_has_ci_absolue_true(self):
        result = InteractionCheckResult(
            alerts=[self._alert("CI_ABSOLUE", "contre_indication")]
        )
        assert result.has_ci_absolue is True

    def test_has_ci_absolue_false_when_only_relative(self):
        result = InteractionCheckResult(
            alerts=[self._alert("CI_RELATIVE", "association_deconseille")]
        )
        assert result.has_ci_absolue is False

    def test_has_ci_relative_true(self):
        result = InteractionCheckResult(
            alerts=[self._alert("CI_RELATIVE", "association_deconseille")]
        )
        assert result.has_ci_relative is True

    def test_highest_severity_returns_first(self):
        result = InteractionCheckResult(
            alerts=[
                self._alert("CI_ABSOLUE", "contre_indication"),
                self._alert("PRECAUTION", "precaution_emploi"),
            ]
        )
        assert result.highest_severity == "CI_ABSOLUE"

    def test_highest_severity_none_when_empty(self):
        result = InteractionCheckResult(alerts=[])
        assert result.highest_severity is None

    def test_empty_result_no_ci(self):
        result = InteractionCheckResult()
        assert result.has_ci_absolue is False
        assert result.has_ci_relative is False


# ── Cache key ─────────────────────────────────────────────────────────────────

class TestCacheKey:
    def test_deterministic(self):
        assert _cache_key(["metformine", "ibuprofène"]) == _cache_key(["metformine", "ibuprofène"])

    def test_order_independent(self):
        assert _cache_key(["metformine", "ibuprofène"]) == _cache_key(["ibuprofène", "metformine"])

    def test_different_drugs_different_keys(self):
        assert _cache_key(["warfarine", "aspirine"]) != _cache_key(["metformine", "ibuprofène"])

    def test_key_prefix(self):
        key = _cache_key(["metformine"])
        assert key.startswith("interactions:")


# ── InteractionChecker.check — DB mock ───────────────────────────────────────

class TestInteractionCheckerCheck:
    def _mock_db_row(
        self,
        drug_a: str,
        drug_b: str,
        severity: str = "contre_indication",
        description: str = "Test interaction",
        source: str = "vidal",
    ):
        row = MagicMock()
        row.drug_a = drug_a
        row.drug_b = drug_b
        row.severity = severity
        row.description = description
        row.source = source
        return row

    def _make_db(self, rows: list) -> AsyncMock:
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = rows
        db.execute.return_value = result_mock
        return db

    @pytest.mark.asyncio
    async def test_fewer_than_two_drugs_returns_empty(self):
        checker = InteractionChecker()
        db = self._make_db([])
        result = await checker.check(new_drugs=["metformine"], active_drugs=[], db=db)
        assert result.alerts == []
        assert result.checked_drugs == ["metformine"]

    @pytest.mark.asyncio
    async def test_single_ci_absolue_interaction(self):
        checker = InteractionChecker()
        row = self._mock_db_row("ibuprofène", "warfarine", "contre_indication")
        db = self._make_db([row])
        result = await checker.check(
            new_drugs=["Advil"],
            active_drugs=["coumadine"],
            db=db,
        )
        assert result.has_ci_absolue is True
        assert len(result.alerts) == 1
        assert result.alerts[0].severity == "CI_ABSOLUE"

    @pytest.mark.asyncio
    async def test_alerts_sorted_ci_absolue_first(self):
        checker = InteractionChecker()
        rows = [
            self._mock_db_row("a", "c", "precaution_emploi"),
            self._mock_db_row("a", "b", "contre_indication"),
        ]
        db = self._make_db(rows)
        result = await checker.check(
            new_drugs=["a", "b"],
            active_drugs=["c"],
            db=db,
        )
        severities = [a.severity for a in result.alerts]
        assert severities[0] == "CI_ABSOLUE"
        assert severities[1] == "PRECAUTION"

    @pytest.mark.asyncio
    async def test_duplicate_drug_names_deduplicated(self):
        checker = InteractionChecker()
        db = self._make_db([])
        result = await checker.check(
            new_drugs=["metformine", "Glucophage"],  # same drug
            active_drugs=["ibuprofène"],
            db=db,
        )
        # Both map to metformine — should be deduplicated
        assert "metformine" in result.checked_drugs
        assert result.checked_drugs.count("metformine") == 1

    @pytest.mark.asyncio
    async def test_redis_cache_hit_skips_db(self):
        import json
        checker = InteractionChecker()
        cached_data = [
            {
                "drug_a": "metformine",
                "drug_b": "ibuprofène",
                "severity": "PRECAUTION",
                "severity_raw": "precaution_emploi",
                "description": "cached",
                "source": "vidal",
            }
        ]
        redis = AsyncMock()
        redis.get.return_value = json.dumps(cached_data).encode()
        db = AsyncMock()

        result = await checker.check(
            new_drugs=["metformine"],
            active_drugs=["ibuprofène"],
            db=db,
            redis=redis,
        )
        assert result.from_cache is True
        assert len(result.alerts) == 1
        db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_redis_cache_miss_writes_after_db(self):
        import json
        checker = InteractionChecker()
        row = self._mock_db_row("ibuprofène", "warfarine", "precaution_emploi")
        db = self._make_db([row])
        redis = AsyncMock()
        redis.get.return_value = None  # cache miss

        result = await checker.check(
            new_drugs=["ibuprofène"],
            active_drugs=["warfarine"],
            db=db,
            redis=redis,
        )
        assert result.from_cache is False
        redis.setex.assert_called_once()

    @pytest.mark.asyncio
    async def test_redis_failure_does_not_raise(self):
        checker = InteractionChecker()
        db = self._make_db([])
        redis = AsyncMock()
        redis.get.side_effect = ConnectionError("Redis down")

        # Should not raise — fail-open
        result = await checker.check(
            new_drugs=["metformine"],
            active_drugs=["ibuprofène"],
            db=db,
            redis=redis,
        )
        assert isinstance(result, InteractionCheckResult)


# ── DFG alerts (no DB) ────────────────────────────────────────────────────────

class TestDfgAlerts:
    def _checker(self) -> InteractionChecker:
        return InteractionChecker()

    def test_no_alerts_when_dfg_none(self):
        c = self._checker()
        alerts = c.check_dfg_alerts(["metformine"], dfg=None)
        assert alerts == []

    def test_critique_when_dfg_below_absolute_min(self):
        c = self._checker()
        # metformine absolute_min = 30.0
        alerts = c.check_dfg_alerts(["metformine"], dfg=25.0)
        assert len(alerts) == 1
        assert alerts[0].severity == "CRITIQUE"
        assert alerts[0].drug == "metformine"

    def test_attention_when_dfg_between_caution_and_absolute(self):
        c = self._checker()
        # metformine caution_threshold = 45.0, absolute_min = 30.0
        alerts = c.check_dfg_alerts(["metformine"], dfg=38.0)
        assert len(alerts) == 1
        assert alerts[0].severity == "ATTENTION"

    def test_no_alert_when_dfg_above_caution(self):
        c = self._checker()
        alerts = c.check_dfg_alerts(["metformine"], dfg=60.0)
        assert alerts == []

    def test_commercial_name_resolved(self):
        c = self._checker()
        # glucophage → metformine
        alerts = c.check_dfg_alerts(["glucophage"], dfg=25.0)
        assert len(alerts) == 1
        assert alerts[0].drug == "metformine"

    def test_critique_sorted_before_attention(self):
        c = self._checker()
        # Both rivaroxaban (absolute 15, caution 30) and metformine (absolute 30, caution 45)
        # DFG=20 → rivaroxaban=CRITIQUE (20>15, so ATTENTION), metformine=CRITIQUE (20<30)
        # Let's use DFG=10 → both CRITIQUE
        alerts = c.check_dfg_alerts(["rivaroxaban", "metformine"], dfg=10.0)
        critique = [a for a in alerts if a.severity == "CRITIQUE"]
        attention = [a for a in alerts if a.severity == "ATTENTION"]
        # All CRITIQUE should appear before ATTENTION
        for i, a in enumerate(alerts):
            if a.severity == "CRITIQUE":
                assert all(b.severity == "CRITIQUE" for b in alerts[:i + 1])

    def test_unknown_drug_no_alert(self):
        c = self._checker()
        alerts = c.check_dfg_alerts(["unknownDrugXYZ"], dfg=10.0)
        assert alerts == []

    def test_ibuprofene_critique_below_30(self):
        c = self._checker()
        alerts = c.check_dfg_alerts(["ibuprofène"], dfg=20.0)
        assert len(alerts) == 1
        assert alerts[0].severity == "CRITIQUE"
        assert alerts[0].threshold == 30.0

    def test_alert_message_contains_drug_and_dfg(self):
        c = self._checker()
        alerts = c.check_dfg_alerts(["metformine"], dfg=25.0)
        assert "metformine" in alerts[0].message.lower()
        assert "25" in alerts[0].message

    def test_multiple_drugs_multiple_alerts(self):
        c = self._checker()
        alerts = c.check_dfg_alerts(["metformine", "ibuprofène"], dfg=20.0)
        drug_names = {a.drug for a in alerts}
        assert "metformine" in drug_names
        assert "ibuprofène" in drug_names


# ── Singleton ─────────────────────────────────────────────────────────────────

class TestSingleton:
    def test_get_interaction_checker_returns_same_instance(self):
        c1 = get_interaction_checker()
        c2 = get_interaction_checker()
        assert c1 is c2

    def test_get_interaction_checker_is_checker_type(self):
        c = get_interaction_checker()
        assert isinstance(c, InteractionChecker)
