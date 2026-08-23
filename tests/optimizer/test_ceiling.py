"""Tests for opt.ceiling — ceiling rate calculator and lock/wait decision.

Test strategy
-------------
Each internal helper is tested in isolation first (_blend_quantile, _apply_basis,
_horizon_weight), then compute_ceiling and lock_or_wait are tested end-to-end
with hand-verified expected values.

Hand-verification notes
------------------------
Example used in many tests:
    Supramax, 30-day contract, risk_tolerance=0 (risk-neutral, uses P50).
    Fans:
        h=7:  p10=12000, p50=15000, p90=19000
        h=30: p10=13000, p50=16000, p90=20000
        h=90: p10=14000, p50=17000, p90=22000

    Horizon weights for 30-day contract:
        h=7:  covers days 1..18  → 18 days  →  18/30 = 0.600
        h=30: covers days 19..30 → 12 days  →  12/30 = 0.400
        h=90: covers days 61..30 → 0 days   →  0.000
    total_raw_weight = 0.600 + 0.400 = 1.000

    normalised weights: h7=0.6, h30=0.4

    P50 ceiling (risk_tolerance=0):
        0.6 × 15000 + 0.4 × 16000 = 9000 + 6400 = 15400  ✓
"""
from __future__ import annotations

import math
import pytest

from opt.types import BasisEntry, ForecastFan, VesselClass
from opt.ceiling import (
    _apply_basis,
    _blend_quantile,
    _horizon_weight,
    compute_ceiling,
    lock_or_wait,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def supramax_fans() -> list[ForecastFan]:
    """Three-horizon fan for Supramax, matching the hand-verification note above."""
    return [
        ForecastFan(VesselClass.SUPRAMAX, 7,  p10=12_000.0, p50=15_000.0, p90=19_000.0),
        ForecastFan(VesselClass.SUPRAMAX, 30, p10=13_000.0, p50=16_000.0, p90=20_000.0),
        ForecastFan(VesselClass.SUPRAMAX, 90, p10=14_000.0, p50=17_000.0, p90=22_000.0),
    ]


@pytest.fixture()
def capesize_fans() -> list[ForecastFan]:
    return [
        ForecastFan(VesselClass.CAPESIZE, 7,  p10=18_000.0, p50=22_000.0, p90=28_000.0),
        ForecastFan(VesselClass.CAPESIZE, 30, p10=19_000.0, p50=23_000.0, p90=29_000.0),
        ForecastFan(VesselClass.CAPESIZE, 90, p10=20_000.0, p50=24_000.0, p90=30_000.0),
    ]


@pytest.fixture()
def indo_basis() -> BasisEntry:
    """Indonesia EC-India route: -8% basis mean, 6% std (from 02_overview.md example)."""
    return BasisEntry("indo_ec_india", basis_mean=-0.08, basis_std=0.06)


# ---------------------------------------------------------------------------
# _blend_quantile
# ---------------------------------------------------------------------------

class TestBlendQuantile:
    def test_risk_neutral_returns_p50(self):
        result = _blend_quantile(p10=10_000.0, p50=15_000.0, risk_tolerance=0.0)
        assert result == pytest.approx(15_000.0)

    def test_fully_risk_averse_returns_p10(self):
        result = _blend_quantile(p10=10_000.0, p50=15_000.0, risk_tolerance=1.0)
        assert result == pytest.approx(10_000.0)

    def test_midpoint_blends_linearly(self):
        result = _blend_quantile(p10=10_000.0, p50=15_000.0, risk_tolerance=0.5)
        assert result == pytest.approx(12_500.0)

    def test_small_risk_tolerance(self):
        result = _blend_quantile(p10=10_000.0, p50=20_000.0, risk_tolerance=0.25)
        # 0.75 * 20000 + 0.25 * 10000 = 15000 + 2500 = 17500
        assert result == pytest.approx(17_500.0)

    def test_invalid_risk_tolerance_above_1_raises(self):
        with pytest.raises(ValueError, match="risk_tolerance"):
            _blend_quantile(10_000.0, 15_000.0, risk_tolerance=1.5)

    def test_invalid_risk_tolerance_below_0_raises(self):
        with pytest.raises(ValueError, match="risk_tolerance"):
            _blend_quantile(10_000.0, 15_000.0, risk_tolerance=-0.1)

    def test_equal_p10_p50_returns_same_value(self):
        result = _blend_quantile(p10=15_000.0, p50=15_000.0, risk_tolerance=0.7)
        assert result == pytest.approx(15_000.0)


# ---------------------------------------------------------------------------
# _apply_basis
# ---------------------------------------------------------------------------

class TestApplyBasis:
    def test_none_basis_passthrough(self):
        p10, p50, p90 = _apply_basis(10_000.0, 15_000.0, 20_000.0, basis=None)
        assert p10 == pytest.approx(10_000.0)
        assert p50 == pytest.approx(15_000.0)
        assert p90 == pytest.approx(20_000.0)

    def test_negative_basis_lowers_p50(self):
        basis = BasisEntry("indo", basis_mean=-0.08, basis_std=0.0)
        _, p50, _ = _apply_basis(12_000.0, 15_000.0, 18_000.0, basis=basis)
        # 15000 * (1 - 0.08) = 13800
        assert p50 == pytest.approx(13_800.0)

    def test_positive_basis_raises_p50(self):
        basis = BasisEntry("pacific_rv", basis_mean=0.05, basis_std=0.0)
        _, p50, _ = _apply_basis(12_000.0, 15_000.0, 18_000.0, basis=basis)
        # 15000 * 1.05 = 15750
        assert p50 == pytest.approx(15_750.0)

    def test_nonzero_std_widens_fan(self):
        """P10 should be lower and P90 higher when basis_std > 0."""
        basis_no_std  = BasisEntry("r", basis_mean=0.0, basis_std=0.0)
        basis_with_std = BasisEntry("r", basis_mean=0.0, basis_std=0.10)
        p10_no, p50_no, p90_no   = _apply_basis(12_000.0, 15_000.0, 18_000.0, basis=basis_no_std)
        p10_wd, p50_wd, p90_wd   = _apply_basis(12_000.0, 15_000.0, 18_000.0, basis=basis_with_std)
        assert p10_wd < p10_no
        assert p90_wd > p90_no
        assert p50_wd == pytest.approx(p50_no)  # std doesn't move the median

    def test_p10_never_negative(self):
        """Even with aggressive basis, p10 is clamped to at least 1."""
        basis = BasisEntry("extreme", basis_mean=-0.99, basis_std=0.50)
        p10, _, _ = _apply_basis(100.0, 200.0, 300.0, basis=basis)
        assert p10 >= 1.0

    def test_basis_widening_magnitude(self):
        """Widening = base_p50 * basis_std, applied symmetrically around basis-adjusted fan."""
        basis = BasisEntry("r", basis_mean=0.0, basis_std=0.10)
        p10, p50, p90 = _apply_basis(12_000.0, 15_000.0, 18_000.0, basis=basis)
        widening = 15_000.0 * 0.10  # = 1500
        assert p10 == pytest.approx(12_000.0 - widening)
        assert p90 == pytest.approx(18_000.0 + widening)


# ---------------------------------------------------------------------------
# _horizon_weight
# ---------------------------------------------------------------------------

class TestHorizonWeight:
    # Segments [lo, hi): h7=[0,19), h30=[19,61), h90=[61,∞)
    # --- 30-day contract ---
    # h=7:  [0, 19) ∩ [0, 30) → 19 days → 19/30
    # h=30: [19, 61) ∩ [0, 30) → [19, 30) → 11 days → 11/30
    # h=90: [61, ∞) → 0
    # total raw = 30/30 = 1.0 ✓
    def test_h7_weight_30day_contract(self):
        w = _horizon_weight(7, 30)
        assert w == pytest.approx(19 / 30)

    def test_h30_weight_30day_contract(self):
        w = _horizon_weight(30, 30)
        assert w == pytest.approx(11 / 30)

    def test_h90_weight_30day_contract_is_zero(self):
        w = _horizon_weight(90, 30)
        assert w == pytest.approx(0.0)

    def test_weights_sum_to_1_for_30_day(self):
        total = sum(_horizon_weight(h, 30) for h in (7, 30, 90))
        assert total == pytest.approx(1.0)

    # --- 90-day contract ---
    # h=7:  [0, 19) → 19/90
    # h=30: [19, 61) → 42/90
    # h=90: [61, 90) → 29/90   total = 90/90 = 1.0 ✓
    def test_h7_weight_90day_contract(self):
        w = _horizon_weight(7, 90)
        assert w == pytest.approx(19 / 90)

    def test_h30_weight_90day_contract(self):
        w = _horizon_weight(30, 90)
        assert w == pytest.approx(42 / 90)

    def test_h90_weight_90day_contract(self):
        w = _horizon_weight(90, 90)
        assert w == pytest.approx(29 / 90)

    def test_weights_sum_to_1_for_90_day(self):
        total = sum(_horizon_weight(h, 90) for h in (7, 30, 90))
        assert total == pytest.approx(1.0)

    # --- Very short contract (≤7 days) ---
    # h=7: [0, 19) ∩ [0, 7) → [0, 7) → 7 days → 7/7 = 1.0
    # h=30, h=90: start ≥ 7 → 0
    def test_7day_contract_h7_full(self):
        assert _horizon_weight(7, 7) == pytest.approx(1.0)

    def test_7day_contract_h30_zero(self):
        assert _horizon_weight(30, 7) == pytest.approx(0.0)

    def test_7day_contract_h90_zero(self):
        assert _horizon_weight(90, 7) == pytest.approx(0.0)

    # --- 180-day contract (6-month TC) ---
    # h=90: [61, 10001) ∩ [0, 180) → [61, 180) → 119/180
    # h=7: [0,19)→19/180, h=30: [19,61)→42/180, h=90: [61,180)→119/180 → total=180 ✓
    def test_h90_weight_180day_contract(self):
        w = _horizon_weight(90, 180)
        assert w == pytest.approx(119 / 180)

    def test_weights_sum_to_1_for_180_day(self):
        total = sum(_horizon_weight(h, 180) for h in (7, 30, 90))
        assert total == pytest.approx(1.0)

    def test_invalid_horizon_raises(self):
        with pytest.raises(ValueError, match="Unsupported horizon_days"):
            _horizon_weight(14, 30)



# ---------------------------------------------------------------------------
# compute_ceiling
# ---------------------------------------------------------------------------

class TestComputeCeiling:
    def test_risk_neutral_30day_contract(self, supramax_fans):
        """Hand-verified: P50 ceiling for 30-day Supramax.
        Segments h7=[0,19), h30=[19,61). For a 30-day contract:
          h7: 19/30, h30: 11/30 — sum = 1.0 (no normalisation needed).
        P50 ceiling = (19/30)*15000 + (11/30)*16000 = (285000+176000)/30 = 461000/30.
        """
        expected = 461_000.0 / 30
        result = compute_ceiling(supramax_fans, VesselClass.SUPRAMAX, 30, risk_tolerance=0.0)
        assert result["ceiling_usd_per_day"] == pytest.approx(expected, rel=1e-6)
        assert result["expected_spot_p50"] == pytest.approx(expected, rel=1e-6)

    def test_fully_risk_averse_is_lower_than_risk_neutral(self, supramax_fans):
        neutral = compute_ceiling(supramax_fans, VesselClass.SUPRAMAX, 30, risk_tolerance=0.0)
        averse  = compute_ceiling(supramax_fans, VesselClass.SUPRAMAX, 30, risk_tolerance=1.0)
        assert averse["ceiling_usd_per_day"] < neutral["ceiling_usd_per_day"]

    def test_risk_averse_30day_ceiling(self, supramax_fans):
        """Hand-verified: P10 ceiling for 30-day Supramax.
        Weights: h7=19/30, h30=11/30.
        P10 ceiling = (19/30)*12000 + (11/30)*13000 = (228000+143000)/30 = 371000/30.
        """
        expected = 371_000.0 / 30
        result = compute_ceiling(supramax_fans, VesselClass.SUPRAMAX, 30, risk_tolerance=1.0)
        assert result["ceiling_usd_per_day"] == pytest.approx(expected, rel=1e-6)

    def test_no_forecasts_for_class_raises(self, supramax_fans):
        with pytest.raises(ValueError, match="No forecast entries found"):
            compute_ceiling(supramax_fans, VesselClass.CAPESIZE, 30)

    def test_zero_contract_term_raises(self, supramax_fans):
        with pytest.raises(ValueError, match="positive"):
            compute_ceiling(supramax_fans, VesselClass.SUPRAMAX, 0)

    def test_negative_contract_term_raises(self, supramax_fans):
        with pytest.raises(ValueError, match="positive"):
            compute_ceiling(supramax_fans, VesselClass.SUPRAMAX, -10)

    def test_route_adjusted_false_without_basis(self, supramax_fans):
        result = compute_ceiling(supramax_fans, VesselClass.SUPRAMAX, 30)
        assert result["route_adjusted"] is False

    def test_route_adjusted_true_with_basis(self, supramax_fans, indo_basis):
        result = compute_ceiling(supramax_fans, VesselClass.SUPRAMAX, 30, basis=indo_basis)
        assert result["route_adjusted"] is True

    def test_negative_basis_lowers_ceiling(self, supramax_fans, indo_basis):
        """Indo EC-India basis (-8%) should lower the ceiling vs no basis."""
        no_basis  = compute_ceiling(supramax_fans, VesselClass.SUPRAMAX, 30)
        with_basis = compute_ceiling(supramax_fans, VesselClass.SUPRAMAX, 30, basis=indo_basis)
        assert with_basis["ceiling_usd_per_day"] < no_basis["ceiling_usd_per_day"]

    def test_single_horizon_fan_works(self):
        """If only the h=7 fan is provided, all weight goes to h=7."""
        fans = [ForecastFan(VesselClass.PANAMAX, 7, p10=10_000.0, p50=14_000.0, p90=18_000.0)]
        result = compute_ceiling(fans, VesselClass.PANAMAX, 7)
        assert result["ceiling_usd_per_day"] == pytest.approx(14_000.0)
        assert result["total_weight"] == pytest.approx(1.0)

    def test_multiple_classes_in_fans_only_uses_requested(self, supramax_fans, capesize_fans):
        """Mixed-class fan list — ceiling only uses the requested class."""
        all_fans = supramax_fans + capesize_fans
        supra = compute_ceiling(all_fans, VesselClass.SUPRAMAX, 30)
        cape  = compute_ceiling(all_fans, VesselClass.CAPESIZE, 30)
        assert supra["ceiling_usd_per_day"] != cape["ceiling_usd_per_day"]

    def test_longer_contract_gives_more_weight_to_h90(self, supramax_fans):
        """A 180-day contract puts more weight on h=90 (higher P50=17000 for Supramax).
        This should pull the ceiling closer to 17000 vs the 30-day ceiling of 15400."""
        short = compute_ceiling(supramax_fans, VesselClass.SUPRAMAX, 30)
        long_  = compute_ceiling(supramax_fans, VesselClass.SUPRAMAX, 180)
        assert long_["ceiling_usd_per_day"] > short["ceiling_usd_per_day"]

    def test_p10_always_leq_ceiling_leq_p90(self, supramax_fans, capesize_fans):
        """Ceiling (blended) must lie between P10 and P50 of the fan."""
        for fans, cls in [(supramax_fans, VesselClass.SUPRAMAX), (capesize_fans, VesselClass.CAPESIZE)]:
            for term in (30, 90, 180):
                for rt in (0.0, 0.5, 1.0):
                    res = compute_ceiling(fans, cls, term, risk_tolerance=rt)
                    assert res["expected_spot_p10"] <= res["ceiling_usd_per_day"] <= res["expected_spot_p50"] + 1e-6


# ---------------------------------------------------------------------------
# lock_or_wait
# ---------------------------------------------------------------------------

class TestLockOrWait:
    def test_lock_when_quote_below_ceiling(self, supramax_fans):
        """Quote of 14000 < ceiling (~15400) → LOCK."""
        result = lock_or_wait(
            supramax_fans, VesselClass.SUPRAMAX,
            contract_term_days=30,
            today_quote_usd_per_day=14_000.0,
        )
        assert result.action == "LOCK"

    def test_wait_when_quote_above_ceiling(self, supramax_fans):
        """Quote of 18000 > ceiling (~15400) → WAIT."""
        result = lock_or_wait(
            supramax_fans, VesselClass.SUPRAMAX,
            contract_term_days=30,
            today_quote_usd_per_day=18_000.0,
        )
        assert result.action == "WAIT"

    def test_lock_at_exactly_ceiling(self, supramax_fans):
        """Quote exactly equal to ceiling → LOCK (≤ not <)."""
        ceiling_res = compute_ceiling(supramax_fans, VesselClass.SUPRAMAX, 30)
        ceiling = ceiling_res["ceiling_usd_per_day"]
        result = lock_or_wait(
            supramax_fans, VesselClass.SUPRAMAX,
            contract_term_days=30,
            today_quote_usd_per_day=ceiling,
        )
        assert result.action == "LOCK"

    def test_savings_positive_when_locking(self, supramax_fans):
        quote = 14_000.0
        result = lock_or_wait(
            supramax_fans, VesselClass.SUPRAMAX,
            contract_term_days=30,
            today_quote_usd_per_day=quote,
        )
        # Savings = P50 spot ceiling - TC quote; must be positive when we LOCK
        expected_p50 = compute_ceiling(supramax_fans, VesselClass.SUPRAMAX, 30)["expected_spot_p50"]
        assert result.savings_p50_usd_per_day == pytest.approx(expected_p50 - quote, rel=1e-6)
        assert result.savings_p50_usd_per_day > 0.0

    def test_savings_negative_when_waiting_makes_sense(self, supramax_fans):
        result = lock_or_wait(
            supramax_fans, VesselClass.SUPRAMAX,
            contract_term_days=30,
            today_quote_usd_per_day=18_000.0,
        )
        # TC quote (18000) > P50 spot (15400) → locking would cost more → negative savings
        assert result.savings_p50_usd_per_day < 0.0

    def test_vessel_class_propagated(self, supramax_fans):
        result = lock_or_wait(
            supramax_fans, VesselClass.SUPRAMAX, 30, 14_000.0
        )
        assert result.vessel_class == VesselClass.SUPRAMAX

    def test_contract_term_propagated(self, supramax_fans):
        result = lock_or_wait(
            supramax_fans, VesselClass.SUPRAMAX, 90, 14_000.0
        )
        assert result.contract_term_days == 90

    def test_zero_quote_raises(self, supramax_fans):
        with pytest.raises(ValueError, match="positive"):
            lock_or_wait(supramax_fans, VesselClass.SUPRAMAX, 30, today_quote_usd_per_day=0.0)

    def test_negative_quote_raises(self, supramax_fans):
        with pytest.raises(ValueError, match="positive"):
            lock_or_wait(supramax_fans, VesselClass.SUPRAMAX, 30, today_quote_usd_per_day=-500.0)

    def test_with_basis_route_adjusted_flag(self, supramax_fans, indo_basis):
        result = lock_or_wait(
            supramax_fans, VesselClass.SUPRAMAX, 30, 14_000.0, basis=indo_basis
        )
        assert result.route_adjusted is True

    def test_without_basis_route_adjusted_false(self, supramax_fans):
        result = lock_or_wait(
            supramax_fans, VesselClass.SUPRAMAX, 30, 14_000.0
        )
        assert result.route_adjusted is False

    def test_risk_averse_lowers_ceiling_making_lock_harder(self, supramax_fans):
        """Higher risk_tolerance → lower ceiling → harder to trigger LOCK."""
        # At quote=14000, risk-neutral locks. At risk_tolerance=1, ceiling=12400 < 14000 → WAIT.
        neutral = lock_or_wait(
            supramax_fans, VesselClass.SUPRAMAX, 30, 14_000.0, risk_tolerance=0.0
        )
        averse  = lock_or_wait(
            supramax_fans, VesselClass.SUPRAMAX, 30, 14_000.0, risk_tolerance=1.0
        )
        assert neutral.action == "LOCK"
        assert averse.action == "WAIT"

    def test_p10_savings_always_leq_p50_savings(self, supramax_fans):
        """Worst-case (P10) savings can never exceed expected (P50) savings."""
        for quote in (13_000.0, 15_400.0, 17_000.0):
            result = lock_or_wait(supramax_fans, VesselClass.SUPRAMAX, 30, quote)
            assert result.savings_p10_usd_per_day <= result.savings_p50_usd_per_day

    def test_6month_contract_with_basis(self, supramax_fans, indo_basis):
        """Smoke test for a realistic 6-month Supramax TC on Indo→EC India route."""
        result = lock_or_wait(
            supramax_fans, VesselClass.SUPRAMAX,
            contract_term_days=180,
            today_quote_usd_per_day=13_500.0,
            risk_tolerance=0.3,
            basis=indo_basis,
        )
        # Just check it runs and produces valid output
        assert result.action in ("LOCK", "WAIT")
        assert result.ceiling_usd_per_day > 0.0
        assert result.vessel_class == VesselClass.SUPRAMAX
