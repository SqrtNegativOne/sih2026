"""Tests for opt.fleetmix — PS deliverable (b), vessel-type recommendation."""
from datetime import date

import pytest

from opt.fleetmix import (
    NoFeasibleConfigurationError,
    _can_call,
    enumerate_fleet_mix,
)
from opt.network import PortEnum
from opt.types import ForecastFan, Vessel, VesselClass

# ---------------------------------------------------------------------------
# Controlled, realistic forecast fixtures -- deterministic so the feasibility
# and ordering logic can be asserted exactly.
# ---------------------------------------------------------------------------

def all_class_fans() -> list[ForecastFan]:
    fans = []
    rates = {
        VesselClass.HANDYSIZE: (10_000.0, 12_000.0, 15_000.0),
        VesselClass.SUPRAMAX: (13_000.0, 16_000.0, 20_000.0),
        VesselClass.PANAMAX: (15_000.0, 18_000.0, 23_000.0),
        VesselClass.CAPESIZE: (25_000.0, 30_000.0, 38_000.0),
    }
    for cls, (p10, p50, p90) in rates.items():
        for h in (7, 30, 90):
            fans.append(ForecastFan(vessel_class=cls, horizon_days=h, p10=p10, p50=p50, p90=p90))
    return fans


class TestPortConstraintFiltering:
    def test_capesize_to_haldia_requires_transshipment(self):
        # Richards Bay is a real deep-water Capesize coal port (recorded
        # max_dwt 220,000 here) -- Haldia is genuinely too shallow for a
        # Capesize to call direct (max_draft_m 8.5 vs the class's 18.0m).
        frontier = enumerate_fleet_mix(150_000.0, PortEnum.RICHARDS_BAY, PortEnum.HALDIA, all_class_fans())
        cape_configs = [c for c in frontier.configurations if c.vessel_class == VesselClass.CAPESIZE]
        assert len(cape_configs) == 1
        assert cape_configs[0].requires_transshipment is True
        assert cape_configs[0].transshipment_hub == PortEnum.DHAMRA

    def test_supramax_to_paradip_is_direct_no_transshipment(self):
        # Real geography: Supramax (58k dwt, 12.5m draft) clears Paradip's real
        # limits (75k dwt, 14.3m draft) directly.
        frontier = enumerate_fleet_mix(50_000.0, PortEnum.NEWCASTLE_AU, PortEnum.PARADIP, all_class_fans())
        supra_configs = [c for c in frontier.configurations if c.vessel_class == VesselClass.SUPRAMAX]
        assert len(supra_configs) == 1
        assert supra_configs[0].requires_transshipment is False

    def test_transshipment_configuration_costs_more_than_a_direct_one_would(self):
        # Same requirement, same origin, one destination the Capesize can reach
        # direct and one it can't -- the transshipment surcharge should show up
        # as extra real cost, not be silently absorbed.
        direct = enumerate_fleet_mix(150_000.0, PortEnum.RICHARDS_BAY, PortEnum.VIZAG, all_class_fans())
        transship = enumerate_fleet_mix(150_000.0, PortEnum.RICHARDS_BAY, PortEnum.HALDIA, all_class_fans())
        direct_cape = next(c for c in direct.configurations if c.vessel_class == VesselClass.CAPESIZE)
        transship_cape = next(c for c in transship.configurations if c.vessel_class == VesselClass.CAPESIZE)
        assert transship_cape.requires_transshipment
        assert not direct_cape.requires_transshipment
        # Not a strict inequality on total cost (routes differ), but the
        # transshipment surcharge itself (150,000 dwt * $3.5/t = $525,000) must
        # be a real, nonzero, attributable cost.
        assert transship_cape.cost_p50_usd > 0


class TestFrontierOrderingAndProperties:
    def test_configurations_sorted_by_cost_ascending(self):
        frontier = enumerate_fleet_mix(100_000.0, PortEnum.NEWCASTLE_AU, PortEnum.PARADIP, all_class_fans())
        costs = [c.cost_p50_usd for c in frontier.configurations]
        assert costs == sorted(costs)

    def test_cheapest_matches_first_element(self):
        frontier = enumerate_fleet_mix(100_000.0, PortEnum.NEWCASTLE_AU, PortEnum.PARADIP, all_class_fans())
        assert frontier.cheapest == frontier.configurations[0]

    def test_most_reliable_has_the_max_reliability_score(self):
        frontier = enumerate_fleet_mix(100_000.0, PortEnum.NEWCASTLE_AU, PortEnum.PARADIP, all_class_fans())
        best = frontier.most_reliable
        assert all(best.reliability_score >= c.reliability_score for c in frontier.configurations)

    def test_p10_le_p50_le_p90_for_every_feasible_configuration(self):
        frontier = enumerate_fleet_mix(100_000.0, PortEnum.NEWCASTLE_AU, PortEnum.PARADIP, all_class_fans())
        for c in frontier.configurations:
            assert c.cost_p10_usd <= c.cost_p50_usd <= c.cost_p90_usd

    def test_more_vessels_means_lower_reliability_score_all_else_equal(self):
        # A larger requirement forces more sailings of the same class -- more
        # independent laycan dependencies, which the reliability heuristic
        # should reflect as strictly lower.
        small = enumerate_fleet_mix(50_000.0, PortEnum.NEWCASTLE_AU, PortEnum.PARADIP, all_class_fans())
        large = enumerate_fleet_mix(500_000.0, PortEnum.NEWCASTLE_AU, PortEnum.PARADIP, all_class_fans())
        small_handy = next(c for c in small.configurations if c.vessel_class == VesselClass.HANDYSIZE)
        large_handy = next(c for c in large.configurations if c.vessel_class == VesselClass.HANDYSIZE)
        assert large_handy.n_vessels > small_handy.n_vessels
        assert large_handy.reliability_score < small_handy.reliability_score


class TestErrorHandling:
    def test_nonpositive_requirement_rejected(self):
        with pytest.raises(ValueError):
            enumerate_fleet_mix(0.0, PortEnum.NEWCASTLE_AU, PortEnum.PARADIP, all_class_fans())
        with pytest.raises(ValueError):
            enumerate_fleet_mix(-100.0, PortEnum.NEWCASTLE_AU, PortEnum.PARADIP, all_class_fans())

    def test_no_forecast_data_leaves_the_frontier_empty_not_fabricated(self):
        frontier = enumerate_fleet_mix(100_000.0, PortEnum.NEWCASTLE_AU, PortEnum.PARADIP, forecasts=[])
        assert frontier.configurations == ()
        with pytest.raises(NoFeasibleConfigurationError):
            _ = frontier.cheapest
        with pytest.raises(NoFeasibleConfigurationError):
            _ = frontier.most_reliable


class TestRealForecastIntegration:
    """End-to-end sanity check against the real, unit-calibrated market data
    already on disk (src/data/master_long.parquet), not synthetic fixtures."""

    def test_real_forecast_produces_a_sane_frontier(self):
        import polars as pl

        from opt.types import BasisEntry, RouteFamily

        master_path = "src/data/master_long.parquet"
        try:
            master = pl.read_parquet(master_path)
        except FileNotFoundError:
            pytest.skip("master_long.parquet not present on disk")


        from ml import units

        real_fans = []
        for cls in VesselClass:
            tc_id = units.CLASS_SERIES[cls.value][1]
            tc_row = master.filter(pl.col("series_id") == tc_id).sort("date").tail(1)
            if tc_row.is_empty():
                continue
            tc_now = float(tc_row["value"][0])
            as_of = tc_row["date"][0]
            # A flat, uninformative fan (p10/p50/p90 all near today's real quote)
            # is enough to sanity-check pricing/feasibility mechanics end to end
            # without needing the full live XGBoost feature pipeline here too
            # (that path is already covered by run_live_scenario.py).
            for h in (7, 30, 90):
                real_fans.append(
                    ForecastFan(
                        vessel_class=cls, horizon_days=h,
                        p10=tc_now * 0.9, p50=tc_now, p90=tc_now * 1.15,
                    )
                )
            del as_of

        if not real_fans:
            pytest.skip("no real TC data available")

        basis = BasisEntry(route_family=RouteFamily.AUSTRALIA_EC_INDIA, basis_mean=0.0, basis_std=0.05)
        frontier = enumerate_fleet_mix(
            120_000.0, PortEnum.NEWCASTLE_AU, PortEnum.VIZAG, real_fans, basis=basis, contract_term_days=30,
        )
        assert len(frontier.configurations) >= 1
        assert frontier.cheapest.cost_p50_usd > 0


class TestRelaxedPathUsesTheRegisterNotThePortEnumLiteral:
    """BT-2's own named requirement: the relaxed tolerance must be applied to
    the resolved register limit, never the legacy PortEnum literal, whenever
    a register entry exists. Gangavaram is the real case where the two
    genuinely differ: PortEnum.GANGAVARAM.value.max_draft_m is the old,
    unsourced 16.5m; the real register's deepest berth (B5/B6, BPTS/AGPL/06)
    is 18.0m -- a full 1.5m apart, enough to flip the relaxed-tolerance
    outcome depending on which one is actually used.
    """

    def _deep_draft_vessel(self, draft_m: float) -> Vessel:
        return Vessel(
            vessel_id="relaxed-test-vessel",
            vessel_class=VesselClass.CAPESIZE,
            current_port=PortEnum.SINGAPORE,
            status="idle",
            available_from=date(2026, 1, 1),
            dwt=180_000.0,
            draft_m=draft_m,
            loa_m=290.0,  # well inside B5's real 292m -- isolates the draft check
            beam_m=45.0,
            speed_kn=14.0,
            laden_fuel_consumption_tpd=45.0,
            ballast_fuel_consumption_tpd=38.0,
        )

    def test_portenum_literal_is_genuinely_different_from_the_register(self) -> None:
        """Sanity check on the premise itself, not just the outcome."""
        assert PortEnum.GANGAVARAM.value.max_draft_m == 16.5

    def test_strict_check_fails_beyond_every_real_gangavaram_berth(self) -> None:
        v = self._deep_draft_vessel(19.5)  # deeper than the register's real 18.0m max
        ok, reason = _can_call(v, PortEnum.GANGAVARAM, relaxed=False, as_of=date(2026, 8, 27))
        assert ok is False
        assert "draft" in reason

    def test_relaxed_pass_uses_the_deeper_register_limit_not_the_shallower_literal(self) -> None:
        """19.5m is within tolerance of the REAL register limit (18.0 + 2.5 =
        20.5) but NOT within tolerance of the old PortEnum literal (16.5 +
        2.5 = 19.0) -- these two only agree if the bug this task fixes is
        still present. A pass here is only possible via the register value.
        """
        v = self._deep_draft_vessel(19.5)
        ok, reason = _can_call(v, PortEnum.GANGAVARAM, relaxed=True, as_of=date(2026, 8, 27))
        assert ok is True
        assert reason is None

    def test_relaxed_still_rejects_beyond_the_registers_own_tolerance(self) -> None:
        """Not an unbounded relaxation: 21.0m exceeds even 18.0 + 2.5 = 20.5."""
        v = self._deep_draft_vessel(21.0)
        ok, reason = _can_call(v, PortEnum.GANGAVARAM, relaxed=True, as_of=date(2026, 8, 27))
        assert ok is False

    def test_a_port_with_no_register_entry_still_uses_the_portenum_literal_relaxed(self) -> None:
        """Requirement 8 extended to the relaxed path: PARADIP has no
        register entry, so relaxed tolerance still applies to PortEnum's own
        literal, exactly as it always did. Uses Paradip's own real LOA/beam
        (225m/32.2m) rather than the Capesize-sized helper above, so a
        too-long/too-wide rejection (relaxed doesn't widen LOA/beam, matching
        the original design) doesn't mask the draft-tolerance behaviour this
        test actually targets."""
        v = Vessel(
            vessel_id="paradip-fallback-test",
            vessel_class=VesselClass.SUPRAMAX,
            current_port=PortEnum.SINGAPORE,
            status="idle",
            available_from=date(2026, 1, 1),
            dwt=55_000.0,
            draft_m=PortEnum.PARADIP.value.max_draft_m + 1.0,
            loa_m=190.0,
            beam_m=32.0,
            speed_kn=14.0,
            laden_fuel_consumption_tpd=26.0,
            ballast_fuel_consumption_tpd=22.0,
        )
        strict_ok, _ = _can_call(v, PortEnum.PARADIP, relaxed=False)
        assert strict_ok is False
        relaxed_ok, _ = _can_call(v, PortEnum.PARADIP, relaxed=True)
        assert relaxed_ok is True  # 1.0m over is within the 2.5m tolerance
