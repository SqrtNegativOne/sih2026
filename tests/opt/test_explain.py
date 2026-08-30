"""Tests for opt.explain -- the explainability layer.

Test strategy
-------------
Every Explanation must be built purely from the real fields already on the
result object it explains -- no fabrication. Each test constructs a real
typed result (LockWaitResult, VoyageAssignment, etc.) with known values and
asserts the explanation's summary/factors actually reference those exact
values, not just that *something* was returned.
"""
from __future__ import annotations

from opt.explain import (
    _explain_fleet_mix,
    _explain_lock_wait,
    _explain_repositioning,
    _explain_savings,
    _explain_voyage_assignment,
    build_explanations,
)
from opt.monte_carlo import SavingsDistribution
from opt.network import PortEnum, RouteFamily
from opt.types import (
    BasisEntry,
    FleetConfiguration,
    FleetMixFrontier,
    LockWaitResult,
    OptimizerInputs,
    RepositioningAction,
    StoppingResult,
    VesselClass,
    VoyageAssignment,
)


def _empty_inputs(basis: dict[RouteFamily, BasisEntry] | None = None) -> OptimizerInputs:
    return OptimizerInputs(
        parcels=[], vessels=[], tc_quotes={}, planning_horizon_days=30,
        contract_term_days=30, forecasts=[], basis=basis or {},
    )


def _lock_wait_result(**overrides) -> LockWaitResult:
    defaults = {
        "vessel_class": VesselClass.SUPRAMAX, "contract_term_days": 30,
        "ceiling_usd_per_day": 15_000.0, "today_quote_usd_per_day": 14_000.0,
        "action": "LOCK", "expected_spot_cost_usd_per_day": 15_000.0,
        "savings_p50_usd_per_day": 1_000.0, "savings_p10_usd_per_day": -500.0,
        "route_adjusted": False, "origin_port": PortEnum.NEWCASTLE_AU,
        "dest_port": PortEnum.PARADIP, "cargo_volume_dwt": 70_000.0,
    }
    defaults.update(overrides)
    return LockWaitResult(**defaults)


class TestExplainLockWait:
    def test_lock_summary_cites_real_numbers(self):
        result = _lock_wait_result(action="LOCK", today_quote_usd_per_day=14_000.0, ceiling_usd_per_day=15_000.0)
        ex = _explain_lock_wait(result, None, _empty_inputs())
        assert "LOCK" in ex.summary
        assert "14,000" in ex.summary
        assert "15,000" in ex.summary
        assert "at or below" in ex.summary

    def test_wait_summary_says_above(self):
        result = _lock_wait_result(action="WAIT", today_quote_usd_per_day=18_000.0, ceiling_usd_per_day=15_000.0)
        ex = _explain_lock_wait(result, None, _empty_inputs())
        assert "WAIT" in ex.summary
        assert "above" in ex.summary

    def test_no_stopping_result_uses_fallback_method(self):
        result = _lock_wait_result()
        ex = _explain_lock_wait(result, None, _empty_inputs())
        assert "insufficient forecast" in ex.method.lower()

    def test_stopping_result_cites_option_value_and_lsmc_method(self):
        result = _lock_wait_result(ceiling_usd_per_day=13_500.0)
        stopping = StoppingResult(
            vessel_class=VesselClass.SUPRAMAX, today_quote_usd_per_day=14_000.0,
            strike_usd_per_day=15_000.0, planning_horizon_days=30,
            exercise_boundary_usd_per_day=tuple([13_500.0] * 29 + [15_000.0]),
            option_value_usd_per_day=250.0, recommended_action_today="LOCK", n_paths=4000,
        )
        ex = _explain_lock_wait(result, stopping, _empty_inputs())
        assert any("250" in f for f in ex.factors)
        assert any("4,000" in f for f in ex.factors)
        assert "LSMC" in ex.method

    def test_route_adjusted_cites_real_basis(self):
        basis = BasisEntry(route_family=RouteFamily.AUSTRALIA_EC_INDIA, basis_mean=-0.08, basis_std=0.06)
        inputs = _empty_inputs({RouteFamily.AUSTRALIA_EC_INDIA: basis})
        result = _lock_wait_result(route_adjusted=True, origin_port=PortEnum.NEWCASTLE_AU)
        ex = _explain_lock_wait(result, None, inputs)
        assert any("-8.0%" in f or "-8%" in f for f in ex.factors)

    def test_not_route_adjusted_says_no_basis(self):
        result = _lock_wait_result(route_adjusted=False)
        ex = _explain_lock_wait(result, None, _empty_inputs())
        assert any("no route-specific basis" in f.lower() for f in ex.factors)


class TestExplainVoyageAssignment:
    def test_cites_real_profit_and_hours(self):
        a = VoyageAssignment(
            vessel_id="V1", parcel_id="P1", dest_port=PortEnum.VIZAG,
            arrival_hours=50, wait_hours=12, start_operation_hours=62,
            finish_hours=90, ballast_hours=40, inter_cargo_gap_hours=5,
            profit_usd=123_456.0,
        )
        ex = _explain_voyage_assignment(a)
        assert "V1" in ex.summary and "P1" in ex.summary
        assert "123,456" in ex.summary
        assert any("40h" in f for f in ex.factors)  # ballast
        assert any("12h" in f for f in ex.factors)  # wait
        assert any("5h" in f for f in ex.factors)  # inter-cargo gap
        assert "CP-SAT" in ex.method


class TestExplainRepositioning:
    def test_staying_summary(self):
        a = RepositioningAction(
            vessel_id="V1", current_port=PortEnum.PARADIP, recommended_port=PortEnum.PARADIP,
            is_staying=True, cargo_probability_within_window=0.7, probability_is_real_data=True,
            recommended_score_usd=5000.0, current_port_score_usd=5000.0,
        )
        ex = _explain_repositioning(a)
        assert "stay" in ex.summary.lower()
        assert any("70%" in f for f in ex.factors)
        assert any("real" in f.lower() for f in ex.factors)

    def test_moving_summary_cites_improvement(self):
        a = RepositioningAction(
            vessel_id="V1", current_port=PortEnum.HALDIA, recommended_port=PortEnum.NEWCASTLE_AU,
            is_staying=False, cargo_probability_within_window=0.79, probability_is_real_data=True,
            recommended_score_usd=8000.0, current_port_score_usd=2000.0,
        )
        ex = _explain_repositioning(a)
        assert "reposition" in ex.summary.lower()
        assert "6,000" in ex.summary  # 8000 - 2000

    def test_non_real_data_flagged(self):
        a = RepositioningAction(
            vessel_id="V1", current_port=PortEnum.SINGAPORE, recommended_port=PortEnum.SINGAPORE,
            is_staying=True, cargo_probability_within_window=0.5, probability_is_real_data=False,
            recommended_score_usd=1000.0, current_port_score_usd=1000.0,
        )
        ex = _explain_repositioning(a)
        assert any("no portwatch coverage" in f.lower() for f in ex.factors)


class TestExplainSavings:
    def test_cites_real_probability_and_quote(self):
        dist = SavingsDistribution(
            vessel_class=VesselClass.SUPRAMAX, contract_term_days=30, quote_usd_per_day=14_000.0,
            expected_p50_savings=500.0, worst_case_p10_savings=-200.0,
            best_case_p90_savings=1500.0, prob_positive_savings=0.63,
        )
        ex = _explain_savings(dist)
        assert "500" in ex.summary and "1,500" in ex.summary and "-200" in ex.summary
        assert any("63%" in f for f in ex.factors)
        assert any("14,000" in f for f in ex.factors)
        assert "Monte Carlo" in ex.method


class TestExplainFleetMix:
    def _config(self, cls: VesselClass, cost_p50: float, feasible: bool = True, reason: str | None = None) -> FleetConfiguration:
        return FleetConfiguration(
            vessel_class=cls, n_vessels=1, dwt_per_vessel=58_000.0, total_capacity_dwt=58_000.0,
            requires_transshipment=False, transshipment_hub=None, voyage_days_per_vessel=10.0,
            cost_p10_usd=cost_p50 * 0.9 if feasible else 0.0,
            cost_p50_usd=cost_p50 if feasible else 0.0,
            cost_p90_usd=cost_p50 * 1.1 if feasible else 0.0,
            reliability_score=1.0 if feasible else 0.0,
            infeasible_reason=reason,
        )

    def test_cheapest_recommended_cites_cost_and_reliability(self):
        cheap = self._config(VesselClass.SUPRAMAX, 300_000.0)
        pricier = self._config(VesselClass.PANAMAX, 350_000.0)
        frontier = FleetMixFrontier(
            origin=PortEnum.BALIKPAPAN, dest=PortEnum.VIZAG, requirement_dwt=50_000.0,
            configurations=(cheap, pricier), rejected_configurations=(),
        )
        ex = _explain_fleet_mix(frontier)
        assert "Supramax" in ex.summary
        assert "300,000" in ex.summary
        assert any("350,000" in f for f in ex.factors)

    def test_no_feasible_configurations_cites_rejection_reasons(self):
        rejected = (
            self._config(VesselClass.CAPESIZE, 0.0, feasible=False, reason="cannot call origin X"),
            self._config(VesselClass.PANAMAX, 0.0, feasible=False, reason="no forecast available"),
        )
        frontier = FleetMixFrontier(
            origin=PortEnum.HALDIA, dest=PortEnum.PARADIP, requirement_dwt=200_000.0,
            configurations=(), rejected_configurations=rejected,
        )
        ex = _explain_fleet_mix(frontier)
        assert "no vessel class can move" in ex.summary.lower()
        assert any("cannot call origin X" in f for f in ex.factors)
        assert any("no forecast available" in f for f in ex.factors)


class TestBuildExplanations:
    def test_wires_all_pieces_in_order(self):
        lw = _lock_wait_result()
        assignments = [
            VoyageAssignment(vessel_id="V1", parcel_id="P1", dest_port=PortEnum.VIZAG,
                              arrival_hours=1, wait_hours=0, start_operation_hours=1,
                              finish_hours=10, ballast_hours=1, inter_cargo_gap_hours=0, profit_usd=100.0),
            VoyageAssignment(vessel_id="V2", parcel_id="P2", dest_port=PortEnum.HALDIA,
                              arrival_hours=1, wait_hours=0, start_operation_hours=1,
                              finish_hours=10, ballast_hours=2, inter_cargo_gap_hours=0, profit_usd=200.0),
        ]
        repos = [
            RepositioningAction(vessel_id="V3", current_port=PortEnum.PARADIP, recommended_port=PortEnum.PARADIP,
                                 is_staying=True, cargo_probability_within_window=0.5,
                                 probability_is_real_data=True, recommended_score_usd=1.0, current_port_score_usd=1.0),
        ]
        dist = SavingsDistribution(
            vessel_class=VesselClass.SUPRAMAX, contract_term_days=30, quote_usd_per_day=14_000.0,
            expected_p50_savings=1.0, worst_case_p10_savings=-1.0, best_case_p90_savings=2.0,
            prob_positive_savings=0.5,
        )
        result = build_explanations(
            lw_result=lw, stopping_result=None, voyage_assignments=assignments,
            repositioning_actions=repos, mc_dist=dist, fleet_mix=None, inputs=_empty_inputs(),
        )
        assert len(result.voyage_assignments) == 2
        assert "V1" in result.voyage_assignments[0].summary
        assert "V2" in result.voyage_assignments[1].summary
        assert len(result.repositioning) == 1
        assert result.fleet_mix is None

    def test_fleet_mix_none_when_not_provided(self):
        lw = _lock_wait_result()
        dist = SavingsDistribution(
            vessel_class=VesselClass.SUPRAMAX, contract_term_days=30, quote_usd_per_day=14_000.0,
            expected_p50_savings=1.0, worst_case_p10_savings=-1.0, best_case_p90_savings=2.0,
            prob_positive_savings=0.5,
        )
        result = build_explanations(
            lw_result=lw, stopping_result=None, voyage_assignments=[], repositioning_actions=[],
            mc_dist=dist, fleet_mix=None, inputs=_empty_inputs(),
        )
        assert result.fleet_mix is None
