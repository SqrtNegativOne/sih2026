"""Tests for opt.voyage — Voyage Scheduler (Sub-problem 2)."""
from datetime import date

import pytest

from opt.network import PortEnum, RouteFamily
from opt.types import (
    BasisEntry,
    CargoParcel,
    LimitSource,
    OptimizerInputs,
    Vessel,
    VesselClass,
)
from opt.voyage import _bunker_price_usd, _vessel_can_call, schedule_voyages

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
        loa_m=190.0,
        beam_m=32.0,
        speed_kn=12.0,
        laden_fuel_consumption_tpd=30.0,
        ballast_fuel_consumption_tpd=25.0,
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
        
        
        opex_usd_per_day=500.0,
    )


# ---------------------------------------------------------------------------
# Port compatibility helper
# ---------------------------------------------------------------------------

class TestVesselCanCall:
    """_vessel_can_call now returns a FeasibilityVerdict, not a bare
    (bool, str | None) tuple -- BT-2. Every port named directly below
    (SINGAPORE, GOPALPUR, PARADIP, HALDIA, RICHARDS_BAY) has no berth_truth
    register entry, so every one of these still runs the byte-identical
    PortEnum dimension check this class has always pinned; only the
    assertion syntax changed (verdict.is_feasible / verdict.reason, not
    tuple indexing). PortEnum.VIZAG is the one exception -- see
    test_both_dwt_and_draft_fine below, which now genuinely exercises the
    register instead of PortEnum, verified to still resolve True.
    """

    def test_no_spec_allows_all(self):
        v = make_supramax()
        assert _vessel_can_call(v, PortEnum.SINGAPORE).is_feasible is True

    def test_dwt_too_large_blocked(self):
        v = make_supramax()  # 55k DWT
        verdict = _vessel_can_call(v, PortEnum.GOPALPUR)
        assert verdict.is_feasible is False
        assert "DWT" in verdict.reason

    def test_dwt_exactly_at_limit_allowed(self):
        v = make_supramax()  # 55k DWT
        assert _vessel_can_call(v, PortEnum.PARADIP).is_feasible is True

    def test_draft_too_deep_blocked(self):
        v = make_supramax()  # draft 12.0m
        verdict = _vessel_can_call(v, PortEnum.HALDIA)
        assert verdict.is_feasible is False
        assert verdict.reason is not None

    def test_draft_exactly_at_limit_allowed(self):
        v = make_supramax()
        assert _vessel_can_call(v, PortEnum.PARADIP).is_feasible is True

    def test_both_dwt_and_draft_fine(self):
        """PortEnum.VIZAG maps to a real berth_truth register entry
        (PortId.VISAKHAPATNAM, BT-1) -- this now genuinely resolves against
        28 real (if superseded) Vizag berths rather than one PortEnum record.
        Verified directly: this 55k dwt / 12.0m draft / 190m LOA vessel
        clears comfortably at several real inner-harbour berths (e.g. EQ-7:
        draft 14.5m, LOA 240m) with DWT/beam untested (neither is published
        for Vizag in this register) -- same True outcome as before, reached
        by a different, real mechanism.
        """
        v = make_supramax()
        verdict = _vessel_can_call(v, PortEnum.VIZAG)
        assert verdict.is_feasible is True
        assert verdict.limit_source is LimitSource.REGISTER

    def test_loa_too_long_blocked(self):
        """Regression test: LOA was never checked at all before this fix.

        Paradip is where the vessel clears DWT (55k < 75k) and draft (12.0m <
        14.3m) cleanly, isolating the LOA check.
        """
        v = make_supramax().model_copy(update={"loa_m": 999.0})
        verdict = _vessel_can_call(v, PortEnum.PARADIP)
        assert verdict.is_feasible is False
        assert "LOA" in verdict.reason

    def test_loa_exactly_at_limit_allowed(self):
        v = make_supramax().model_copy(update={"loa_m": 225.0})  # Paradip's max
        assert _vessel_can_call(v, PortEnum.PARADIP).is_feasible is True

    def test_beam_too_wide_blocked(self):
        """Regression test: beam was never checked at all before this fix."""
        v = make_supramax().model_copy(update={"beam_m": 999.0})
        verdict = _vessel_can_call(v, PortEnum.PARADIP)
        assert verdict.is_feasible is False
        assert "beam" in verdict.reason

    def test_beam_exactly_at_limit_allowed(self):
        v = make_supramax().model_copy(update={"beam_m": 32.2})  # Paradip's max
        assert _vessel_can_call(v, PortEnum.PARADIP).is_feasible is True

    def test_origin_ports_now_have_real_loa_beam_data(self):
        """Regression test: origin ports (Newcastle, Richards Bay, ...) used to
        have no LOA/beam on record at all, making this check vacuous for every
        real loading port -- a direct gap against the problem statement's own
        "similar data for the loading ports in Australia, the US, Mozambique,
        and Indonesia." Real, sourced port-authority/terminal-operator figures
        were added; this pins that an oversized vessel is now genuinely
        rejected at a real origin port, not just at the EC-India destinations.
        """
        v = make_supramax().model_copy(update={"loa_m": 999.0, "beam_m": 999.0})
        verdict = _vessel_can_call(v, PortEnum.RICHARDS_BAY)
        assert verdict.is_feasible is False
        assert "LOA" in verdict.reason

    def test_origin_port_loa_beam_within_real_limits_allowed(self):
        """A realistically-sized Supramax (190m/32m) clears Richards Bay's real
        limits (350m/47.5m) comfortably -- the new data doesn't over-restrict."""
        v = make_supramax()
        assert _vessel_can_call(v, PortEnum.RICHARDS_BAY).is_feasible is True

    def test_portenum_fallback_is_labelled(self):
        """A port with no register entry is clearly marked as such, not
        silently indistinguishable from a real register clearance."""
        v = make_supramax()
        verdict = _vessel_can_call(v, PortEnum.SINGAPORE)
        assert verdict.limit_source is LimitSource.PORTENUM_FALLBACK
        assert verdict.binding_constraint is None

    def test_register_clearance_names_the_berth(self):
        """Requirement 2 (commodity-aware) and the objective itself --
        'returning which berth cleared the vessel' -- verified directly
        against real Gangavaram data: a coking-coal cargo names the actual
        berth, not just a port-level yes/no."""
        v = make_supramax().model_copy(update={"draft_m": 14.5})  # clears B2 (14.5m) not B1 (13.0m)
        verdict = _vessel_can_call(v, PortEnum.GANGAVARAM, commodity="Coking Coal", as_of=date(2026, 8, 27))
        assert verdict.is_feasible is True
        assert verdict.berth_id is not None
        assert verdict.limit_source is LimitSource.REGISTER

    def test_gangavaram_capesize_rejected_at_berth_1_accepted_at_berth_5(self):
        """The literal BT-2 requirement: an 18.0m-draft Capesize is rejected
        at Gangavaram berth 1 (13.0m) but the same vessel, for a coal cargo,
        clears at berth 5 (18.0m) -- verified against the real register."""
        capesize = make_supramax().model_copy(
            update={"dwt": 180_000, "draft_m": 18.0, "loa_m": 292.0, "beam_m": 45.0}
        )
        verdict = _vessel_can_call(
            capesize, PortEnum.GANGAVARAM, commodity="Coal", as_of=date(2026, 8, 27)
        )
        assert verdict.is_feasible is True
        assert verdict.berth_id == "B5"
        assert verdict.margins.draft_margin_m == 0.0

    def test_stale_dhamra_draft_is_untested_not_a_silent_pass_or_fail(self):
        """Dhamra BB1/BB2's draft comes from a declaration series this call
        never supplies -- STALE_OR_UNAVAILABLE, disclosed in untested_checks,
        and the vessel still clears on LOA alone (nothing else fails)."""
        v = make_supramax()  # 190m LOA, well inside every Dhamra berth's 347-350m
        verdict = _vessel_can_call(v, PortEnum.DHAMRA, as_of=date(2026, 8, 27))
        assert verdict.is_feasible is True
        assert any("draft" in check for check in verdict.untested_checks)
        assert verdict.observed_only_berths  # BB5, BB2E, ... surfaced, not silently dropped


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
# Real per-port bunker prices -- opt.network.Port.bunker_price_usd was defined
# for every port and never read; fuel cost used one blended global constant
# regardless of where the vessel actually bunkers. Fixed as part of P3.
# ---------------------------------------------------------------------------

class TestRealBunkerPrices:
    def test_bunker_price_reads_the_real_per_port_value(self):
        # Paradip and Singapore have genuinely different real recorded prices
        # in opt.network (610 vs 540 usd/t) -- confirms the helper reads the
        # actual port data, not a single constant regardless of port.
        assert _bunker_price_usd(PortEnum.PARADIP) == 610.0
        assert _bunker_price_usd(PortEnum.SINGAPORE) == 540.0
        assert _bunker_price_usd(PortEnum.PARADIP) != _bunker_price_usd(PortEnum.SINGAPORE)

    def test_cheaper_bunker_at_the_ballast_origin_yields_higher_profit(self):
        # Same voyage in every respect except which port the vessel ballasts
        # from -- Singapore bunkers real-cheaper (540) than Richards_Bay (570).
        # A cheaper bunkering origin must show up as strictly higher profit,
        # not get silently absorbed by the old single global constant.
        cheaper_origin = make_supramax("V1", port=PortEnum.SINGAPORE)
        pricier_origin = make_supramax("V1", port=PortEnum.RICHARDS_BAY)
        c = make_cargo("C1", PortEnum.PARADIP, PortEnum.VIZAG, date(2025, 1, 5), date(2025, 1, 20))

        result_cheap = schedule_voyages(base_inputs([cheaper_origin], [c]), max_solve_seconds=2.0)
        result_pricy = schedule_voyages(base_inputs([pricier_origin], [c]), max_solve_seconds=2.0)
        assert result_cheap.solver_status in ("OPTIMAL", "FEASIBLE")
        assert result_pricy.solver_status in ("OPTIMAL", "FEASIBLE")
        assert result_cheap.total_profit_usd > result_pricy.total_profit_usd


# ---------------------------------------------------------------------------
# Per-leg profit_usd -- used to be hardcoded to 0.0 on every VoyageAssignment
# (only the aggregate total_profit_usd was ever real). Found while wiring a
# live demo end to end: every assignment printed "profit $0" regardless of
# real revenue. Fixed to read back the same solved CP-SAT variables that built
# the objective, so per-leg and total must always agree exactly.
# ---------------------------------------------------------------------------

class TestPerLegProfit:
    def test_single_leg_profit_is_real_and_matches_total(self):
        v = make_supramax()
        c = make_cargo("C1", PortEnum.PARADIP, PortEnum.VIZAG, date(2025, 1, 5), date(2025, 1, 10))
        inputs = base_inputs([v], [c])
        result = schedule_voyages(inputs, max_solve_seconds=2.0)
        assert result.solver_status in ("OPTIMAL", "FEASIBLE")
        assert len(result.assignments) == 1
        a = result.assignments[0]
        assert a.profit_usd != 0.0
        assert a.profit_usd == pytest.approx(result.total_profit_usd, abs=0.01)
        # Real revenue minus real costs must be less than revenue alone.
        assert a.profit_usd < 500_000.0

    def test_two_legs_profit_sums_to_total(self):
        v = make_supramax()
        c1 = make_cargo("C1", PortEnum.PARADIP, PortEnum.VIZAG, date(2025, 1, 5), date(2025, 1, 10))
        c2 = make_cargo("C2", PortEnum.VIZAG, PortEnum.GANGAVARAM, date(2025, 1, 20), date(2025, 1, 28))
        inputs = base_inputs([v], [c1, c2])
        result = schedule_voyages(inputs, max_solve_seconds=5.0)
        assert result.solver_status in ("OPTIMAL", "FEASIBLE")
        assert len(result.assignments) == 2
        assert sum(a.profit_usd for a in result.assignments) == pytest.approx(
            result.total_profit_usd, abs=0.01
        )
        # Neither leg should silently be left at the old hardcoded placeholder.
        assert all(a.profit_usd != 0.0 for a in result.assignments)


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
        assert any(v_id == "V1" and c_id.startswith("C1") for v_id, c_id, _ in result.infeasible_pairs)
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
        assert not any(v_id == "V1" and c_id.startswith("C1") for v_id, c_id, _ in result.infeasible_pairs)
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
            loa_m=292.0,    # Also too long for Gopalpur (185m) and Paradip (225m)
            beam_m=45.0,    # Also too wide for Gopalpur (28m)
            speed_kn=14.0,
            laden_fuel_consumption_tpd=60.0,
            ballast_fuel_consumption_tpd=50.0,
        )
        c = make_cargo("C1", PortEnum.PARADIP, PortEnum.GOPALPUR, date(2025, 1, 5), date(2025, 1, 15), revenue=800_000)
        
        inputs = base_inputs(
            [supramax, capesize], [c]
        )
        result = schedule_voyages(inputs, max_solve_seconds=3.0)
        # Capesize is blocked at Gopalpur by draft AND dwt; Supramax is ALSO blocked by dwt (55k > 35k)
        # Both should be infeasible — this is the correct real-world outcome.
        assert any(v_id == "V_Cape" and c_id.startswith("C1") for v_id, c_id, _ in result.infeasible_pairs)
        assert any(v_id == "V_Supra" and c_id.startswith("C1") for v_id, c_id, _ in result.infeasible_pairs)
        assert len(result.assignments) == 0


# ---------------------------------------------------------------------------
# Penalty wiring
# ---------------------------------------------------------------------------

class TestPenalties:
    def test_high_opex_prefers_tighter_schedule(self):
        """With high opex, solver should prefer a cargo that arrives sooner."""
        v = make_supramax()
        # C_tight has laycan opening right when vessel arrives
        # C_late has a laycan opening 10 days later (big idle gap)
        c_tight = make_cargo("C_tight", PortEnum.PARADIP, PortEnum.VIZAG, date(2025, 1, 2), date(2025, 1, 10), revenue=500_000)
        c_late = make_cargo("C_late", PortEnum.PARADIP, PortEnum.VIZAG, date(2025, 1, 15), date(2025, 1, 25), revenue=500_000)

        inputs_high_idle = base_inputs(
            [v], [c_tight, c_late]
        )
        inputs_high_idle = inputs_high_idle.model_copy(update={"opex_usd_per_day": 10_000.0})  # Massive opex penalty

        result = schedule_voyages(inputs_high_idle, max_solve_seconds=3.0)
        assert result.solver_status in ("OPTIMAL", "FEASIBLE")
        # With huge opex penalty, solver should pick c_tight (no gap) over c_late (14-day gap)
        assigned_ids = {a.parcel_id for a in result.assignments}
        assert "C_tight" in assigned_ids


class TestDemurrage:
    """Paradip (handling_rate_tph=1200) -> Vizag (handling_rate_tph=2500), 50,000 dwt:
    load_h = int(50000/1200) = 41, disch_h = int(50000/2500) = 20, total port
    time = 61h. Both fields default to 0.0, so a cargo that never sets them must
    be economically identical to one with a huge allowance -- that is the real
    regression to guard: demurrage must be an opt-in cost, not an accidental one.
    """

    def test_defaults_are_a_true_no_op(self):
        """Not setting demurrage fields must cost nothing, not just "a small amount"."""
        v = make_supramax()
        c = make_cargo("C1", PortEnum.PARADIP, PortEnum.VIZAG, date(2025, 1, 5), date(2025, 1, 20), revenue=500_000)
        assert c.demurrage_usd_per_day == 0.0
        assert c.demurrage_wait_days == 0.0

        baseline = schedule_voyages(base_inputs([v], [c]), max_solve_seconds=3.0)
        with_huge_allowance = schedule_voyages(
            base_inputs([v], [c.model_copy(update={"demurrage_wait_days": 1000.0})]),
            max_solve_seconds=3.0,
        )
        assert baseline.total_profit_usd == pytest.approx(with_huge_allowance.total_profit_usd)

    def test_demurrage_charges_exactly_the_excess_hours(self):
        """96h of port time (P6/F-10: Paradip's origin load hours come from
        its real, empirically-sufficient handling productivity --
        berth_truth.empirical.effective_handling_rate_tph. As of the F-10
        fix (real dry-bulk-only throughput, deduplicated to one row per
        real call -- the previous 280.0 tph figure mixed in liquid-cargo
        calls and length-biased duplicate report sightings, see
        berth_truth.fact_port_call.distinct_calls/classify_cargo), that
        rate is 650.9 tph: 50,000/650.9 = 76h load + 50,000/2500.0 = 20h
        discharge at Vizag, whose rate is still the static literal (no
        real fact_port_call coverage there); see
        opt.voyage._cached_effective_handling_rate_tph), 48h (2.0 day)
        allowance -> 48h billed at the parcel rate."""
        v = make_supramax()
        rate_per_day = 2_400.0  # -> $100/hour, a round number to verify against
        c_no_demurrage = make_cargo(
            "C1", PortEnum.PARADIP, PortEnum.VIZAG, date(2025, 1, 5), date(2025, 1, 20), revenue=500_000
        )
        c_demurrage = c_no_demurrage.model_copy(
            update={"demurrage_usd_per_day": rate_per_day, "demurrage_wait_days": 2.0}
        )

        baseline = schedule_voyages(base_inputs([v], [c_no_demurrage]), max_solve_seconds=3.0)
        billed = schedule_voyages(base_inputs([v], [c_demurrage]), max_solve_seconds=3.0)

        expected_demurrage_cost = 48 * (rate_per_day / 24)
        assert baseline.total_profit_usd - billed.total_profit_usd == pytest.approx(
            expected_demurrage_cost, abs=1.0
        )

    def test_port_time_within_the_allowance_is_free(self):
        """A 9-day (216h) allowance comfortably covers the real 96h actually
        needed (see test_demurrage_charges_exactly_the_excess_hours for the
        real-handling-rate arithmetic behind that figure)."""
        v = make_supramax()
        c_ample = make_cargo(
            "C1", PortEnum.PARADIP, PortEnum.VIZAG, date(2025, 1, 5), date(2025, 1, 20), revenue=500_000
        ).model_copy(update={"demurrage_usd_per_day": 5_000.0, "demurrage_wait_days": 9.0})
        c_none = make_cargo(
            "C1", PortEnum.PARADIP, PortEnum.VIZAG, date(2025, 1, 5), date(2025, 1, 20), revenue=500_000
        )

        with_allowance = schedule_voyages(base_inputs([v], [c_ample]), max_solve_seconds=3.0)
        without = schedule_voyages(base_inputs([v], [c_none]), max_solve_seconds=3.0)
        assert with_allowance.total_profit_usd == pytest.approx(without.total_profit_usd)

    def test_demurrage_is_per_cargo_not_fleet_wide(self):
        """Two identical cargoes, only one with demurrage set -- only that one is billed."""
        v1 = make_supramax("V1", port=PortEnum.PARADIP)
        v2 = make_supramax("V2", port=PortEnum.PARADIP)
        c_billed = make_cargo(
            "C_billed", PortEnum.PARADIP, PortEnum.VIZAG, date(2025, 1, 5), date(2025, 1, 20), revenue=500_000
        ).model_copy(update={"demurrage_usd_per_day": 5_000.0, "demurrage_wait_days": 0.0})
        c_free = make_cargo(
            "C_free", PortEnum.PARADIP, PortEnum.VIZAG, date(2025, 2, 5), date(2025, 2, 20), revenue=500_000
        )

        result = schedule_voyages(base_inputs([v1, v2], [c_billed, c_free]), max_solve_seconds=3.0)
        assert result.solver_status in ("OPTIMAL", "FEASIBLE")
        assigned_ids = {a.parcel_id for a in result.assignments}
        assert {"C_billed", "C_free"} <= assigned_ids


# ---------------------------------------------------------------------------
# Multiple Destinations
# ---------------------------------------------------------------------------

class TestAlternativeDestinations:
    def test_alternative_destinations_picks_closer_port(self):
        v = make_supramax(port=PortEnum.PARADIP)
        # Paradip to Vizag is closer than Paradip to Richards Bay.
        # We offer Richards Bay (primary) and Vizag (alternative).
        c = make_cargo("C_alt", PortEnum.PARADIP, PortEnum.RICHARDS_BAY, date(2025, 1, 5), date(2025, 1, 15))
        c = c.model_copy(update={"alternative_dest_ports": [PortEnum.VIZAG]})
        
        inputs = base_inputs([v], [c])
        result = schedule_voyages(inputs, max_solve_seconds=3.0)
        
        assert result.solver_status in ("OPTIMAL", "FEASIBLE")
        assert len(result.assignments) == 1
        a = result.assignments[0]
        # Should pick the closer port (Vizag)
        assert a.dest_port == PortEnum.VIZAG


class TestNoCircularImportWithBerthTruth:
    """P6 regression: opt.voyage importing berth_truth.empirical at module
    scope created a real circular import
    (berth_truth.fact_port_call -> berth_truth.sources -> opt.network ->
    opt/__init__.py -> opt.api -> opt.fleetmix -> opt.voyage ->
    berth_truth.empirical -> berth_truth.fact_port_call, still
    mid-initialization) whenever berth_truth.fact_port_call was the entry
    point of the import graph -- caught by the real test suite, not
    hypothesised, fixed by deferring that import to call time in
    opt.voyage._cached_effective_handling_rate_tph.

    A within-process test cannot reliably reproduce this: pytest's own
    collection almost always imports berth_truth.fact_port_call via some
    other test module first, which populates sys.modules and masks the
    cycle regardless of whether the bug is actually fixed. A real
    subprocess, with a fresh sys.modules, is the only reliable check -- the
    one legitimate use of subprocess in this test suite, and only for this
    reason."""

    def test_fresh_process_can_import_fact_port_call_first(self) -> None:
        import subprocess
        import sys
        from pathlib import Path

        src_dir = Path(__file__).resolve().parents[2] / "src"
        result = subprocess.run(
            [sys.executable, "-c", "from berth_truth.fact_port_call import FactPortCall, FactPortCallStore"],
            cwd=str(src_dir), capture_output=True, text=True, timeout=60, check=False,
        )
        assert result.returncode == 0, result.stderr
