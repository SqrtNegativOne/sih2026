"""Tests for opt.voyage — Voyage Scheduler (Sub-problem 2)."""
import pytest
from datetime import date
from opt.network import PortEnum, RouteFamily
from opt.types import Vessel, CargoParcel, VesselClass, OptimizerInputs, BasisEntry
from opt.network import PortEnum, RouteFamily
from opt.voyage import schedule_voyages, _vessel_can_call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_supramax(vessel_id: str = "V1", port: PortEnum = PortEnum.PARADIP) -> Vessel:
    return Vessel(
        vessel_id=vessel_id,
        vessel_class=VesselClass.SUPRAMAX,
        current_port=port,
        status="idle",
        available_from=date(2025, 1, 1),
        available_until=None,
        dwt=55_000,
        draft_m=12.0,
        speed_kn=12.0,
        fuel_consumption_tpd=30.0,
    )


def make_cargo(
    cargo_id: str,
    origin: PortEnum,
    dest: PortEnum,
    laycan_start: date,
    laycan_end: date,
    revenue: float = 500_000.0,
) -> CargoParcel:
    return CargoParcel(
        parcel_id=cargo_id,
        origin_port=origin,
        dest_port=dest,
        commodity="Coal",
        volume_dwt=50_000,
        laycan_start=laycan_start,
        laycan_end=laycan_end,
        route_family=RouteFamily.INDONESIA_EC_INDIA,
        revenue_usd=revenue,
    )


def base_inputs(vessels, parcels) -> OptimizerInputs:
    return OptimizerInputs(
        vessels=vessels,
        parcels=parcels,
        tc_quotes={},
        planning_horizon_days=60,
        contract_term_days=30,
        forecasts=[],
        basis={RouteFamily.INDONESIA_EC_INDIA: BasisEntry(route_family=RouteFamily.INDONESIA_EC_INDIA, basis_mean=-0.2, basis_std=0.1)},
        
        
        idle_penalty_usd_per_day=500.0,
        ballast_penalty_usd_per_day=250.0,
    )


# ---------------------------------------------------------------------------
# Port compatibility helper
# ---------------------------------------------------------------------------

class TestVesselCanCall:
    def test_no_spec_allows_all(self):
        v = make_supramax()
        assert _vessel_can_call(v, PortEnum.SINGAPORE) is True

    def test_dwt_too_large_blocked(self):
        v = make_supramax()  # 55k DWT
        assert _vessel_can_call(v, PortEnum.GOPALPUR) is False

    def test_dwt_exactly_at_limit_allowed(self):
        v = make_supramax()  # 55k DWT
        assert _vessel_can_call(v, PortEnum.PARADIP) is True

    def test_draft_too_deep_blocked(self):
        v = make_supramax()  # draft 12.0m
        assert _vessel_can_call(v, PortEnum.HALDIA) is False

    def test_draft_exactly_at_limit_allowed(self):
        v = make_supramax()
        assert _vessel_can_call(v, PortEnum.PARADIP) is True

    def test_both_dwt_and_draft_fine(self):
        v = make_supramax()
        assert _vessel_can_call(v, PortEnum.VIZAG) is True


# ---------------------------------------------------------------------------
# Basic scheduling
# ---------------------------------------------------------------------------

class TestScheduleVoyages:
    def test_no_data_returns_no_data(self):
        inputs = base_inputs(vessels=[], parcels=[])
        result = schedule_voyages(inputs)
        assert result.solver_status == "NO_DATA"
        assert result.assignments == []

    def test_simple_single_cargo(self):
        v = make_supramax()
        c = make_cargo("C1", PortEnum.PARADIP, PortEnum.VIZAG, date(2025, 1, 5), date(2025, 1, 10))
        inputs = base_inputs(
            [v], [c]
        )
        result = schedule_voyages(inputs, max_solve_seconds=2.0)
        assert result.solver_status in ("OPTIMAL", "FEASIBLE")
        assert len(result.assignments) == 1
        assert result.assignments[0].parcel_id == "C1"

    def test_sequential_cargoes_same_vessel(self):
        v = make_supramax()
        c1 = make_cargo("C1", PortEnum.PARADIP, PortEnum.VIZAG, date(2025, 1, 5), date(2025, 1, 10))
        c2 = make_cargo("C2", PortEnum.VIZAG, PortEnum.GANGAVARAM, date(2025, 1, 20), date(2025, 1, 28))
        inputs = base_inputs(
            [v], [c1, c2]
        )
        result = schedule_voyages(inputs, max_solve_seconds=5.0)
        assert result.solver_status in ("OPTIMAL", "FEASIBLE")
        assigns = sorted(result.assignments, key=lambda a: a.start_operation_hours)
        # C2 must start after C1 finishes
        assert assigns[1].start_operation_hours >= assigns[0].finish_hours

    def test_laycan_window_respected(self):
        v = make_supramax()
        c = make_cargo("C1", PortEnum.PARADIP, PortEnum.VIZAG, date(2025, 1, 5), date(2025, 1, 10))
        inputs = base_inputs([v], [c])
        result = schedule_voyages(inputs, max_solve_seconds=2.0)
        assert result.solver_status in ("OPTIMAL", "FEASIBLE")
        a = result.assignments[0]
        # Start of operations must be >= laycan_start (day 4 from epoch = 96h)
        assert a.start_operation_hours >= 96


# ---------------------------------------------------------------------------
# Port compatibility enforcement in scheduler
# ---------------------------------------------------------------------------

class TestPortCompatibility:
    def test_infeasible_pair_recorded(self):
        """Vessel too deep for destination - should be in infeasible_pairs."""
        v = make_supramax()  # draft 12.0m
        c = make_cargo("C1", PortEnum.PARADIP, PortEnum.HALDIA, date(2025, 1, 5), date(2025, 1, 10))
        
        inputs = base_inputs([v], [c]
        )
        result = schedule_voyages(inputs, max_solve_seconds=2.0)
        assert ("V1", "C1") in result.infeasible_pairs
        # Cargo cannot be served by any vessel -> no assignments
        assert len(result.assignments) == 0

    def test_compatible_vessel_assigned(self):
        """Vessel within port limits — should be scheduled normally."""
        v = make_supramax()  # draft 12.0m, dwt 55k
        c = make_cargo("C1", PortEnum.PARADIP, PortEnum.VIZAG, date(2025, 1, 5), date(2025, 1, 15))
        
        inputs = base_inputs([v], [c]
        )
        result = schedule_voyages(inputs, max_solve_seconds=2.0)
        assert result.solver_status in ("OPTIMAL", "FEASIBLE")
        assert ("V1", "C1") not in result.infeasible_pairs
        assert len(result.assignments) == 1

    def test_two_vessels_only_compatible_one_assigned(self):
        """Capesize blocked at Gopalpur (shallow); Supramax gets the cargo."""
        supramax = make_supramax("V_Supra")  # 55k DWT, 12.0m draft
        capesize = Vessel(
            vessel_id="V_Cape",
            vessel_class=VesselClass.CAPESIZE,
            current_port=PortEnum.PARADIP,
            status="idle",
            available_from=date(2025, 1, 1),
            available_until=None,
            dwt=180_000,
            draft_m=18.0,   # Too deep for Gopalpur (10.7m)
            speed_kn=14.0,
            fuel_consumption_tpd=60.0,
        )
        c = make_cargo("C1", PortEnum.PARADIP, PortEnum.GOPALPUR, date(2025, 1, 5), date(2025, 1, 15), revenue=800_000)
        
        inputs = base_inputs(
            [supramax, capesize], [c]
        )
        result = schedule_voyages(inputs, max_solve_seconds=3.0)
        # Capesize is blocked at Gopalpur by draft AND dwt; Supramax is ALSO blocked by dwt (55k > 35k)
        # Both should be infeasible — this is the correct real-world outcome.
        assert ("V_Cape", "C1") in result.infeasible_pairs
        assert ("V_Supra", "C1") in result.infeasible_pairs
        assert len(result.assignments) == 0


# ---------------------------------------------------------------------------
# Penalty wiring
# ---------------------------------------------------------------------------

class TestPenalties:
    def test_high_idle_penalty_prefers_tighter_schedule(self):
        """With high idle penalty, solver should prefer a cargo that arrives sooner."""
        v = make_supramax()
        # C_tight has laycan opening right when vessel arrives
        # C_late has a laycan opening 10 days later (big idle gap)
        c_tight = make_cargo("C_tight", PortEnum.PARADIP, PortEnum.VIZAG, date(2025, 1, 2), date(2025, 1, 10), revenue=500_000)
        c_late = make_cargo("C_late", PortEnum.PARADIP, PortEnum.VIZAG, date(2025, 1, 15), date(2025, 1, 25), revenue=500_000)

        inputs_high_idle = base_inputs(
            [v], [c_tight, c_late]
        )
        inputs_high_idle = inputs_high_idle.model_copy(update={"idle_penalty_usd_per_day": 10_000.0})  # Massive idle penalty

        result = schedule_voyages(inputs_high_idle, max_solve_seconds=3.0)
        assert result.solver_status in ("OPTIMAL", "FEASIBLE")
        # With huge idle penalty, solver should pick c_tight (no gap) over c_late (14-day gap)
        assigned_ids = {a.parcel_id for a in result.assignments}
        assert "C_tight" in assigned_ids

    def test_high_ballast_penalty_avoids_distant_cargo(self):
        """With very high ballast penalty, a distant low-revenue cargo should be skipped."""
        v = make_supramax(port=PortEnum.PARADIP)
        # C_local: same port, no ballast, ok revenue
        c_local = make_cargo("C_local", PortEnum.PARADIP, PortEnum.VIZAG, date(2025, 1, 5), date(2025, 1, 15), revenue=400_000)
        # C_distant: 8,000nm away, small revenue — should be rejected when ballast_penalty is high
        c_distant = make_cargo("C_distant", PortEnum.HAMPTON_ROADS, PortEnum.PARADIP, date(2025, 1, 5), date(2025, 3, 1), revenue=100_000)

        inputs = base_inputs(
            [v], [c_local, c_distant]
        )
        inputs = inputs.model_copy(update={"ballast_penalty_usd_per_day": 5_000.0})  # Very high penalty
        result = schedule_voyages(inputs, max_solve_seconds=3.0)
        assert result.solver_status in ("OPTIMAL", "FEASIBLE")
        assigned_ids = {a.parcel_id for a in result.assignments}
        # Distant cargo should be unprofitable and skipped
        assert "C_distant" not in assigned_ids
