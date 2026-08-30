"""P6 port operational penalty: opt.voyage's CP-SAT port-time precompute
uses berth_truth.empirical's real handling productivity only where its
sufficiency gate passes, else the same static opt.network.Port.handling_rate_tph
literal this always used -- see opt.voyage._cached_effective_handling_rate_tph.
"""
from __future__ import annotations

from datetime import date

import pytest

from berth_truth.empirical import effective_handling_rate_tph
from opt.network import PortEnum, RouteFamily
from opt.types import BasisEntry, CargoParcel, OptimizerInputs, Vessel, VesselClass
from opt.voyage import (
    _cached_effective_handling_rate_tph,
    clear_effective_handling_rate_cache,
    schedule_voyages,
)


def _vessel(port: PortEnum) -> Vessel:
    return Vessel(
        vessel_id="V1", vessel_class=VesselClass.SUPRAMAX, current_port=port, status="idle",
        available_from=date(2025, 1, 1), available_until=None, dwt=55_000,
        draft_m=12.0, loa_m=190.0, beam_m=32.0, speed_kn=12.0,
        laden_fuel_consumption_tpd=30.0, ballast_fuel_consumption_tpd=25.0,
    )


def _cargo(origin: PortEnum, dest: PortEnum, volume_dwt: float = 50_000) -> CargoParcel:
    return CargoParcel(
        parcel_id="C1", origin_port=origin, dest_port=dest, commodity="Coal",
        volume_dwt=volume_dwt, laycan_start=date(2025, 1, 5), laycan_end=date(2025, 1, 20),
        route_family=RouteFamily.INDONESIA_EC_INDIA, revenue_usd=500_000.0,
    )


def _inputs(vessels, parcels) -> OptimizerInputs:
    """Matches tests/opt/test_voyage.py's own base_inputs() exactly (same
    real fixture shape, not a new one) -- RouteFamily.INDONESIA_EC_INDIA
    basis entry, since _cargo() below always uses that route family."""
    return OptimizerInputs(
        vessels=vessels, parcels=parcels, tc_quotes={}, planning_horizon_days=60,
        contract_term_days=30, forecasts=[],
        basis={RouteFamily.INDONESIA_EC_INDIA: BasisEntry(
            route_family=RouteFamily.INDONESIA_EC_INDIA, basis_mean=-0.2, basis_std=0.1,
        )},
        opex_usd_per_day=6_500.0,
    )


class TestEffectiveRateGateAtEachPort:
    def test_paradip_uses_the_real_empirical_rate(self) -> None:
        """Paradip has real, sufficient fact_port_call coverage (n=1211,
        confirmed live) -- the cached wrapper must return exactly what the
        underlying gate computes, not the static literal."""
        clear_effective_handling_rate_cache()
        cached = _cached_effective_handling_rate_tph(PortEnum.PARADIP)
        real = effective_handling_rate_tph(PortEnum.PARADIP)
        assert real.is_empirical is True
        assert cached == pytest.approx(real.rate_tph)
        assert cached != PortEnum.PARADIP.value.handling_rate_tph

    def test_a_port_with_no_coverage_uses_the_static_literal_unchanged(self) -> None:
        clear_effective_handling_rate_cache()
        cached = _cached_effective_handling_rate_tph(PortEnum.SINGAPORE)
        assert cached == PortEnum.SINGAPORE.value.handling_rate_tph

    def test_clear_cache_forces_a_recompute_not_a_stale_answer(self) -> None:
        first = _cached_effective_handling_rate_tph(PortEnum.DHAMRA)
        clear_effective_handling_rate_cache()
        second = _cached_effective_handling_rate_tph(PortEnum.DHAMRA)
        # Same real answer either way (nothing changed on disk between
        # calls) -- this proves cache_clear() doesn't raise or corrupt
        # state, not that the value itself changed.
        assert first == second


class TestScheduleVoyagesUsesTheEffectiveRate:
    """End-to-end: the real CP-SAT precompute, not a unit test of the gate
    function in isolation.

    ``finish_hours`` bundles port operation time together with laden transit
    time (confirmed directly: for Paradip->Vizag, empirical run finish_hours
    - static-forced run finish_hours == exactly the change in load_h at
    Paradip, 137h, with everything else -- arrival, transit, discharge --
    identical) -- so these tests compare a real run against the same
    schedule with the rate forced to the static literal, and check the
    *difference*, rather than assuming finish_hours - start_operation_hours
    isolates port time on its own."""

    def test_paradip_leg_finish_hours_shifts_by_exactly_the_rate_change(self, monkeypatch) -> None:
        v = _vessel(PortEnum.PARADIP)
        c = _cargo(PortEnum.PARADIP, PortEnum.VIZAG, volume_dwt=50_000)
        inputs = _inputs([v], [c])

        clear_effective_handling_rate_cache()
        empirical = schedule_voyages(inputs, max_solve_seconds=3.0)
        assert empirical.solver_status in ("OPTIMAL", "FEASIBLE")
        assert len(empirical.assignments) == 1

        import opt.voyage as voyage_mod

        monkeypatch.setattr(
            voyage_mod, "_cached_effective_handling_rate_tph",
            lambda port: port.value.handling_rate_tph,
        )
        static_only = schedule_voyages(inputs, max_solve_seconds=3.0)
        assert len(static_only.assignments) == 1

        origin_empirical_rate = effective_handling_rate_tph(PortEnum.PARADIP).rate_tph
        origin_static_rate = PortEnum.PARADIP.value.handling_rate_tph
        expected_shift = int(50_000 / origin_empirical_rate) - int(50_000 / origin_static_rate)
        assert expected_shift > 0  # the real, disclosed finding: empirical Paradip handling is materially slower

        actual_shift = empirical.assignments[0].finish_hours - static_only.assignments[0].finish_hours
        assert actual_shift == expected_shift
        # Nothing else about the schedule moved -- only the origin handling
        # rate changed between the two runs.
        assert empirical.assignments[0].arrival_hours == static_only.assignments[0].arrival_hours
        assert empirical.assignments[0].start_operation_hours == static_only.assignments[0].start_operation_hours

    def test_a_route_with_no_empirical_coverage_at_either_end_is_unaffected(self, monkeypatch) -> None:
        """Regression: forcing the static-literal path on a route with zero
        real fact_port_call coverage at either end must produce a
        byte-identical schedule to the real (already-static, since there is
        no real coverage to use) path -- this feature must not perturb
        routes it has no real evidence for."""
        v = _vessel(PortEnum.RICHARDS_BAY)
        c = _cargo(PortEnum.RICHARDS_BAY, PortEnum.SINGAPORE, volume_dwt=50_000)
        inputs = _inputs([v], [c])

        clear_effective_handling_rate_cache()
        real = schedule_voyages(inputs, max_solve_seconds=3.0)
        assert len(real.assignments) == 1

        import opt.voyage as voyage_mod

        monkeypatch.setattr(
            voyage_mod, "_cached_effective_handling_rate_tph",
            lambda port: port.value.handling_rate_tph,
        )
        forced_static = schedule_voyages(inputs, max_solve_seconds=3.0)
        assert len(forced_static.assignments) == 1

        assert real.assignments[0].finish_hours == forced_static.assignments[0].finish_hours
        assert real.total_profit_usd == pytest.approx(forced_static.total_profit_usd)
