"""P5 requirement 4: fragile/stable ranking, against synthetic FlipPoints
(the algorithm itself) and one real sweep (that it wires correctly end to
end)."""
from __future__ import annotations

from datetime import timedelta

from fragility.engine import analyze_fragility
from fragility.models import (
    DecisionSignature,
    FlipPoint,
    FragilityReport,
    Provenance,
    Tier,
    VariableId,
)
from fragility.ranking import rank_findings
from ml.live_forecast import latest_available_date
from opt.network import PortEnum

TODAY = latest_available_date()


def _fp(variable: VariableId, **kwargs) -> FlipPoint:
    defaults = {
        "variable": variable, "tier": Tier.TIER1_CLOSED_FORM, "provenance": Provenance.USER_INPUT,
        "base_value": 100.0, "unit": "x", "flip_found": False,
    }
    defaults.update(kwargs)
    return FlipPoint(**defaults)


class TestRankingOrder:
    def test_fragile_findings_are_ordered_by_smallest_percent_delta_first(self) -> None:
        far = _fp(VariableId.CARGO_VOLUME_DWT, flip_found=True, flip_value=200.0, percent_delta=100.0)
        near = _fp(VariableId.VESSEL_DRAFT_M, flip_found=True, flip_value=105.0, percent_delta=5.0)
        report = FragilityReport(
            current_decision=DecisionSignature(), findings=(far, near), evaluations_used=2,
        )
        ranked = rank_findings(report)
        assert [r.finding.variable for r in ranked] == [VariableId.VESSEL_DRAFT_M, VariableId.CARGO_VOLUME_DWT]
        assert ranked[0].category == "FRAGILE"
        assert ranked[0].rank == 1
        assert ranked[0].fragility_score < ranked[1].fragility_score

    def test_stable_findings_sort_after_every_fragile_one(self) -> None:
        fragile = _fp(VariableId.VESSEL_DRAFT_M, flip_found=True, flip_value=105.0, percent_delta=5.0)
        stable = _fp(VariableId.RISK_TOLERANCE, flip_found=False, range_searched_low=0.0, range_searched_high=1.0)
        report = FragilityReport(
            current_decision=DecisionSignature(), findings=(stable, fragile), evaluations_used=2,
        )
        ranked = rank_findings(report)
        assert ranked[0].finding.variable == VariableId.VESSEL_DRAFT_M
        assert ranked[0].category == "FRAGILE"
        assert ranked[1].finding.variable == VariableId.RISK_TOLERANCE
        assert ranked[1].category == "STABLE"

    def test_unavailable_findings_sort_last_and_carry_no_score(self) -> None:
        fragile = _fp(VariableId.VESSEL_DRAFT_M, flip_found=True, flip_value=105.0, percent_delta=5.0)
        stable = _fp(VariableId.RISK_TOLERANCE, flip_found=False, range_searched_low=0.0, range_searched_high=1.0)
        unavailable = _fp(VariableId.ORIGIN_WAIT_DAYS, unavailable_reason="no injection point")
        report = FragilityReport(
            current_decision=DecisionSignature(), findings=(unavailable, stable, fragile), evaluations_used=2,
        )
        ranked = rank_findings(report)
        assert [r.category for r in ranked] == ["FRAGILE", "STABLE", "UNAVAILABLE"]
        assert ranked[-1].fragility_score is None
        assert ranked[-1].finding.variable == VariableId.ORIGIN_WAIT_DAYS

    def test_every_finding_appears_exactly_once(self) -> None:
        findings = (
            _fp(VariableId.VESSEL_DRAFT_M, flip_found=True, flip_value=105.0, percent_delta=5.0),
            _fp(VariableId.PERMISSIBLE_DRAFT_M, flip_found=True, flip_value=90.0, percent_delta=-10.0),
            _fp(VariableId.RISK_TOLERANCE, flip_found=False),
            _fp(VariableId.ORIGIN_WAIT_DAYS, unavailable_reason="x"),
            _fp(VariableId.DEST_WAIT_DAYS, unavailable_reason="x"),
        )
        report = FragilityReport(current_decision=DecisionSignature(), findings=findings, evaluations_used=5)
        ranked = rank_findings(report)
        assert len(ranked) == len(findings)
        assert {r.finding.variable for r in ranked} == {f.variable for f in findings}
        assert [r.rank for r in ranked] == list(range(1, len(findings) + 1))

    def test_zero_base_value_falls_back_to_absolute_delta(self) -> None:
        """percent_delta is None exactly when base_value == 0 -- the ranking
        must not crash or silently treat that as maximally stable."""
        fp = _fp(VariableId.RISK_TOLERANCE, base_value=0.0, flip_found=True, flip_value=0.3, absolute_delta=0.3, percent_delta=None)
        report = FragilityReport(current_decision=DecisionSignature(), findings=(fp,), evaluations_used=1)
        ranked = rank_findings(report)
        assert ranked[0].fragility_score == 0.3


class TestRankingAgainstARealSweep:
    def test_ranking_a_real_report_covers_every_finding(self) -> None:
        laycan_start = TODAY + timedelta(days=21)
        laycan_end = TODAY + timedelta(days=35)
        report = analyze_fragility(
            cargo_volume_dwt=75_000, origin_port=PortEnum.NEWCASTLE_AU, dest_port=PortEnum.GANGAVARAM,
            laycan_start=laycan_start, laycan_end=laycan_end, as_of=TODAY,
        )
        ranked = rank_findings(report)
        assert len(ranked) == len(report.findings)
        # Categories partition the real findings with no overlap/omission.
        by_category: dict[str, int] = {}
        for r in ranked:
            by_category[r.category] = by_category.get(r.category, 0) + 1
        assert sum(by_category.values()) == len(report.findings)
