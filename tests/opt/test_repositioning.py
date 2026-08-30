from datetime import date

import pytest

from opt.congestion import dynamic_wait_days
from opt.network import PortEnum, RouteFamily
from opt.repositioning import (
    cargo_probability_within_window,
    clear_hazard_cache,
    recommend_repositioning,
)
from opt.types import (
    BasisEntry,
    ForecastFan,
    OptimizerInputs,
    Vessel,
    VesselClass,
)


def test_repositioning_logic():
    v = Vessel(
        vessel_id="V1",
        vessel_class=VesselClass.SUPRAMAX,
        current_port=PortEnum.PARADIP,
        status="idle",
        available_from=date(2025, 1, 1),
        available_until=None,
        dwt=55000,
        draft_m=12.0,
        loa_m=190.0,
        beam_m=32.0,
        speed_kn=12.0,  # 288 nm/day
        laden_fuel_consumption_tpd=30.0,
        ballast_fuel_consumption_tpd=25.0
    )
    
    # Base forecast for Supramax = 10,000 USD/day
    fans = [ForecastFan(vessel_class=VesselClass.SUPRAMAX, horizon_days=30, p10=8000, p50=10000, p90=12000)]
    
    inputs = OptimizerInputs(
        parcels=[], vessels=[v], tc_quotes={}, planning_horizon_days=30, contract_term_days=30,
        forecasts=fans,
        basis={RouteFamily.INDONESIA_EC_INDIA: BasisEntry(route_family=RouteFamily.INDONESIA_EC_INDIA, basis_mean=-0.2, basis_std=0.1)}, # 5 days ballast
        
        opex_usd_per_day=500.0,
    )
    
    candidates = [PortEnum.PARADIP, PortEnum.SINGAPORE]
    
    rec = recommend_repositioning(v, candidates, inputs, assumed_voyage_days=30)
    
    assert rec.current_port == PortEnum.PARADIP
    assert len(rec.options) == 2
    
    paradip_opt = next(o for o in rec.options if o.port == PortEnum.PARADIP)

    # Wait cost is real-data-driven (opt.congestion.dynamic_wait_days scales
    # Paradip's static 3.5-day baseline by how busy it actually is right now)
    # -- derive the expected cost from that same real function rather than a
    # hardcoded number that would go stale as real PortWatch data updates.
    # base_tce here is the unadjusted class-level forecast (compute_ceiling is
    # called without a basis in recommend_repositioning), and Paradip's real
    # hazard probability saturates to 1.0 at a 30-day window.
    expected_wait_days, _ = dynamic_wait_days(PortEnum.PARADIP)
    expected_wait_cost = expected_wait_days * inputs.opex_usd_per_day
    assert paradip_opt.wait_cost_usd == pytest.approx(expected_wait_cost)
    assert paradip_opt.ballast_cost_usd == 0.0
    assert paradip_opt.expected_tce_usd_per_day == 10000.0
    assert paradip_opt.cargo_probability_within_window == pytest.approx(1.0)
    expected_score = 1.0 * 10_000.0 * 30 - expected_wait_cost
    assert paradip_opt.score_usd == pytest.approx(expected_score)

    assert rec.options[0].port == PortEnum.PARADIP  # Paradip wins because Singapore ballast is too expensive now


# ---------------------------------------------------------------------------
# Real fix: the original engine hardcoded basis_mean=0.0, so port_tce (and
# hence score) was identical everywhere -- structurally incapable of ever
# preferring one real candidate port over another based on cargo likelihood.
# These test the actual fix, against real PortWatch/tonnage-field data.
# ---------------------------------------------------------------------------

def _capesize_at(port: PortEnum) -> Vessel:
    return Vessel(
        vessel_id="V1", vessel_class=VesselClass.CAPESIZE, current_port=port, status="idle",
        available_from=date(2025, 1, 1), dwt=175_000, draft_m=17.5, loa_m=290.0, beam_m=45.0,
        speed_kn=13.0, laden_fuel_consumption_tpd=45.0, ballast_fuel_consumption_tpd=38.0,
    )


class TestRealHazardDifferentiation:
    """Real data: the whole point of this rewrite is that two real candidate
    ports must no longer score as if cargo were equally likely at both."""

    def test_two_real_ports_get_different_cargo_probabilities_not_identical(self):
        # Real finding: Capesize traffic is real and frequent at Newcastle (a
        # major coal export port) and essentially absent at Haldia (too
        # shallow for Capesize -- max_draft_m 8.5 vs the class's real draft).
        p_newcastle, real_n = cargo_probability_within_window(PortEnum.NEWCASTLE_AU, VesselClass.CAPESIZE, 30)
        p_haldia, real_h = cargo_probability_within_window(PortEnum.HALDIA, VesselClass.CAPESIZE, 30)
        assert real_n and real_h
        assert p_newcastle > p_haldia
        assert p_haldia < 0.05  # near zero, not just "somewhat lower"

    def test_port_with_no_tonnage_coverage_gets_the_honest_neutral_default(self):
        p, is_real = cargo_probability_within_window(PortEnum.SINGAPORE, VesselClass.SUPRAMAX, 30)
        assert is_real is False
        assert p == 0.5

    def test_recommendation_prefers_the_port_with_real_higher_cargo_likelihood(self):
        # A Capesize sitting between two real candidates should be steered
        # toward the one with genuinely more real Capesize-scale cargo
        # activity, not toward whichever happens to be geographically closer
        # regardless of whether there's any cargo to find there.
        v = _capesize_at(PortEnum.RICHARDS_BAY)
        fans = [ForecastFan(vessel_class=VesselClass.CAPESIZE, horizon_days=30, p10=20_000, p50=25_000, p90=32_000)]
        inputs = OptimizerInputs(
            parcels=[], vessels=[v], tc_quotes={}, planning_horizon_days=30, contract_term_days=30,
            forecasts=fans, basis={}, opex_usd_per_day=500.0,
        )
        rec = recommend_repositioning(v, [PortEnum.NEWCASTLE_AU, PortEnum.HALDIA], inputs, assumed_voyage_days=30)
        newcastle_opt = next(o for o in rec.options if o.port == PortEnum.NEWCASTLE_AU)
        haldia_opt = next(o for o in rec.options if o.port == PortEnum.HALDIA)
        assert newcastle_opt.cargo_probability_within_window > haldia_opt.cargo_probability_within_window
        assert newcastle_opt.probability_is_real_data and haldia_opt.probability_is_real_data

    def test_hazard_cache_can_be_cleared(self):
        # Real cache-invalidation path, exercised directly rather than assumed
        # to work because it's a one-line function.
        cargo_probability_within_window(PortEnum.PARADIP, VesselClass.HANDYSIZE, 30)
        clear_hazard_cache()
        # Must still work correctly after clearing (recomputes from scratch).
        p, is_real = cargo_probability_within_window(PortEnum.PARADIP, VesselClass.HANDYSIZE, 30)
        assert is_real is True
        assert 0.0 <= p <= 1.0

    def test_probability_increases_with_window_length(self):
        # A real, checkable property of the underlying Poisson hazard model:
        # P(cargo within N days) must be monotonically nondecreasing in N.
        p_short, _ = cargo_probability_within_window(PortEnum.DHAMRA, VesselClass.PANAMAX, 7)
        p_long, _ = cargo_probability_within_window(PortEnum.DHAMRA, VesselClass.PANAMAX, 60)
        assert p_long >= p_short

