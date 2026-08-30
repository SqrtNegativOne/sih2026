"""Tests for opt.stopping — LSMC optimal stopping (PS deliverable a, additive).

There is no historical ground truth for an optimal-stopping algorithm the way
there is for a forecast -- correctness here means satisfying real mathematical
properties an optimal stopping value must have (it dominates any fixed,
non-adaptive rule; the boundary and value are finite and well-behaved), plus a
regression test for a real numerical-stability bug found and fixed while
building this: the first version of the closed-form boundary solve divided by
a near-zero denominator whenever the fitted regression slope was close to -1,
producing boundary values like $246,664/day on a $16,000/day process.
"""
from typing import Any, ClassVar

import numpy as np
import pytest

from opt.network import PortEnum, RouteFamily
from opt.stopping import (
    InsufficientForecastError,
    _calibrate_piecewise_lognormal,
    _simulate_paths,
    solve_exercise_boundary,
    solve_lock_or_wait,
)
from opt.types import BasisEntry, ForecastFan, VesselClass

DOWNWARD_FANS = [
    ForecastFan(vessel_class=VesselClass.PANAMAX, horizon_days=7, p10=13_000.0, p50=15_000.0, p90=17_500.0),
    ForecastFan(vessel_class=VesselClass.PANAMAX, horizon_days=30, p10=11_000.0, p50=14_000.0, p90=18_000.0),
    ForecastFan(vessel_class=VesselClass.PANAMAX, horizon_days=90, p10=9_000.0, p50=12_500.0, p90=17_000.0),
]

UPWARD_FANS = [
    ForecastFan(vessel_class=VesselClass.CAPESIZE, horizon_days=7, p10=20_000.0, p50=22_000.0, p90=24_500.0),
    ForecastFan(vessel_class=VesselClass.CAPESIZE, horizon_days=30, p10=22_000.0, p50=26_000.0, p90=31_000.0),
    ForecastFan(vessel_class=VesselClass.CAPESIZE, horizon_days=90, p10=25_000.0, p50=32_000.0, p90=40_000.0),
]


class TestNumericalStabilityRegression:
    def test_boundary_never_blows_up_or_goes_negative(self):
        # Regression test for the exact bug: a downward-drifting fan with a
        # 60-day horizon previously produced boundary values as extreme as
        # $246,664/day and repeated exact $0.0 on a ~$14-17k/day process.
        result = solve_exercise_boundary(
            today_quote_usd_per_day=16_000.0, forecasts=DOWNWARD_FANS, vessel_class=VesselClass.PANAMAX,
            contract_term_days=90, planning_horizon_days=60,
        )
        b = np.array(result.exercise_boundary_usd_per_day)
        assert np.all(np.isfinite(b))
        assert np.all(b >= 0.0)
        # A sane band around the process's own scale (today's quote and the
        # forecast's own p10/p90 range across all horizons), not an unbounded
        # sanity check -- real boundary values for this process should not
        # exceed a small multiple of the widest real quantile seen anywhere in
        # the input fan.
        widest = max(f.p90 for f in DOWNWARD_FANS + [ForecastFan(vessel_class=VesselClass.PANAMAX, horizon_days=7, p10=1, p50=1, p90=16_000.0)])
        assert np.all(b <= widest * 3)

    def test_runs_clean_across_a_grid_of_scenarios_no_warnings(self):
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            for quote in (10_000.0, 16_000.0, 25_000.0):
                for horizon in (14, 30, 60, 90):
                    result = solve_exercise_boundary(
                        today_quote_usd_per_day=quote, forecasts=DOWNWARD_FANS, vessel_class=VesselClass.PANAMAX,
                        contract_term_days=90, planning_horizon_days=horizon, n_paths=1000,
                    )
                    b = np.array(result.exercise_boundary_usd_per_day)
                    assert np.all(np.isfinite(b)) and np.all(b >= 0.0)


class TestOptimalityProperty:
    def test_option_value_dominates_always_lock_day_one(self):
        # The defining property of an optimal stopping value: it must be at
        # least as good as any single fixed, non-adaptive rule, including
        # "just lock immediately regardless of what happens later."
        result = solve_exercise_boundary(
            today_quote_usd_per_day=16_000.0, forecasts=DOWNWARD_FANS, vessel_class=VesselClass.PANAMAX,
            contract_term_days=90, planning_horizon_days=60, n_paths=6000, seed=1,
        )
        always_lock_day_one = result.strike_usd_per_day - 16_000.0
        assert result.option_value_usd_per_day >= always_lock_day_one - 1e-6

    def test_option_value_dominates_always_wait_to_maturity(self):
        drift, vol = _calibrate_piecewise_lognormal(16_000.0, DOWNWARD_FANS, VesselClass.PANAMAX, 60)
        paths = _simulate_paths(16_000.0, drift, vol, n_paths=6000, rng=np.random.default_rng(1))
        result = solve_exercise_boundary(
            today_quote_usd_per_day=16_000.0, forecasts=DOWNWARD_FANS, vessel_class=VesselClass.PANAMAX,
            contract_term_days=90, planning_horizon_days=60, n_paths=6000, seed=1,
        )
        always_wait = float(np.mean(result.strike_usd_per_day - paths[:, -1]))
        assert result.option_value_usd_per_day >= always_wait - 1e-6


class TestBoundaryBehavior:
    def test_boundary_at_maturity_equals_the_strike_by_construction(self):
        # Forced terminal exercise: on the last day there is no continuation
        # value left, so the boundary is exactly the strike -- guaranteed by
        # construction, checked here as a real invariant, not an implementation
        # detail.
        result = solve_exercise_boundary(
            today_quote_usd_per_day=16_000.0, forecasts=DOWNWARD_FANS, vessel_class=VesselClass.PANAMAX,
            contract_term_days=90, planning_horizon_days=45,
        )
        assert result.exercise_boundary_usd_per_day[-1] == pytest.approx(result.strike_usd_per_day)

    def test_downward_drifting_market_has_an_early_boundary_below_the_naive_strike(self):
        # Real option-value intuition: when the market is expected to keep
        # falling, waiting is attractive, so the early exercise threshold
        # should require a better-than-naive price, not just match the naive
        # ceiling. Checked on day 2-3 (early, plenty of time value left) rather
        # than day 1 specifically, since day 1 can legitimately fall back to
        # the strike default when too few paths are in the money that early.
        result = solve_exercise_boundary(
            today_quote_usd_per_day=16_000.0, forecasts=DOWNWARD_FANS, vessel_class=VesselClass.PANAMAX,
            contract_term_days=90, planning_horizon_days=30, n_paths=6000, seed=2,
        )
        early = result.exercise_boundary_usd_per_day[1:4]
        assert min(early) < result.strike_usd_per_day

    def test_action_today_is_consistent_with_boundary_day_one(self):
        result = solve_exercise_boundary(
            today_quote_usd_per_day=16_000.0, forecasts=DOWNWARD_FANS, vessel_class=VesselClass.PANAMAX,
            contract_term_days=90, planning_horizon_days=30,
        )
        expected = "LOCK" if result.today_quote_usd_per_day <= result.exercise_boundary_usd_per_day[0] else "WAIT"
        assert result.recommended_action_today == expected


class TestCalibration:
    def test_calibrated_median_path_matches_the_real_forecast_quantiles(self):
        # The whole point of the piecewise calibration: simulate enough paths
        # and the empirical median at each real forecast horizon should land
        # close to that horizon's real p50 -- this is a deterministic
        # calibration from the fan, not a free-parameter fit, so it should
        # reproduce the fan closely given enough paths.
        drift, vol = _calibrate_piecewise_lognormal(16_000.0, DOWNWARD_FANS, VesselClass.PANAMAX, 90)
        paths = _simulate_paths(16_000.0, drift, vol, n_paths=20_000, rng=np.random.default_rng(0))
        for fan in DOWNWARD_FANS:
            empirical_median = float(np.median(paths[:, fan.horizon_days - 1]))
            assert empirical_median == pytest.approx(fan.p50, rel=0.03)

    def test_calibrated_tail_quantiles_are_close_to_the_real_forecast(self):
        drift, vol = _calibrate_piecewise_lognormal(16_000.0, DOWNWARD_FANS, VesselClass.PANAMAX, 90)
        paths = _simulate_paths(16_000.0, drift, vol, n_paths=20_000, rng=np.random.default_rng(0))
        fan90 = next(f for f in DOWNWARD_FANS if f.horizon_days == 90)
        empirical_p10 = float(np.quantile(paths[:, 89], 0.10))
        empirical_p90 = float(np.quantile(paths[:, 89], 0.90))
        assert empirical_p10 == pytest.approx(fan90.p10, rel=0.08)
        assert empirical_p90 == pytest.approx(fan90.p90, rel=0.08)


class TestErrorHandling:
    def test_nonpositive_quote_rejected(self):
        with pytest.raises(ValueError):
            solve_exercise_boundary(0.0, DOWNWARD_FANS, VesselClass.PANAMAX, 90, 30)
        with pytest.raises(ValueError):
            solve_exercise_boundary(-100.0, DOWNWARD_FANS, VesselClass.PANAMAX, 90, 30)

    def test_nonpositive_horizon_rejected(self):
        with pytest.raises(ValueError):
            solve_exercise_boundary(16_000.0, DOWNWARD_FANS, VesselClass.PANAMAX, 90, 0)

    def test_nonpositive_n_paths_rejected(self):
        with pytest.raises(ValueError):
            solve_exercise_boundary(16_000.0, DOWNWARD_FANS, VesselClass.PANAMAX, 90, 30, n_paths=0)

    def test_no_matching_forecast_class_raises_insufficient_forecast(self):
        with pytest.raises(InsufficientForecastError):
            solve_exercise_boundary(16_000.0, DOWNWARD_FANS, VesselClass.HANDYSIZE, 90, 30)


class TestUpwardDriftingMarketSanity:
    def test_runs_clean_and_produces_a_sane_frontier_on_a_rising_market_too(self):
        result = solve_exercise_boundary(
            today_quote_usd_per_day=21_000.0, forecasts=UPWARD_FANS, vessel_class=VesselClass.CAPESIZE,
            contract_term_days=90, planning_horizon_days=60, n_paths=4000, seed=3,
        )
        b = np.array(result.exercise_boundary_usd_per_day)
        assert np.all(np.isfinite(b)) and np.all(b >= 0.0)
        assert result.recommended_action_today in ("LOCK", "WAIT")


class TestRiskToleranceThreading:
    """Regression coverage for a real gap found while readying this module for
    production use: risk_tolerance used to be silently dropped (compute_ceiling
    was always called with risk_tolerance=0.0 inside solve_exercise_boundary),
    so a risk-averse caller got a risk-neutral strike without any indication.
    """

    def test_higher_risk_tolerance_raises_the_strike(self):
        neutral = solve_exercise_boundary(
            today_quote_usd_per_day=16_000.0, forecasts=DOWNWARD_FANS, vessel_class=VesselClass.PANAMAX,
            contract_term_days=90, planning_horizon_days=30, risk_tolerance=0.0,
        )
        averse = solve_exercise_boundary(
            today_quote_usd_per_day=16_000.0, forecasts=DOWNWARD_FANS, vessel_class=VesselClass.PANAMAX,
            contract_term_days=90, planning_horizon_days=30, risk_tolerance=1.0,
        )
        # risk_tolerance blends the strike toward P90 -- a worse (higher) rate
        # for a charterer -- so a fully risk-averse strike must be >= neutral.
        assert averse.strike_usd_per_day >= neutral.strike_usd_per_day
        assert averse.strike_usd_per_day > neutral.strike_usd_per_day  # DOWNWARD_FANS p90 > p50, not a tie

    def test_default_risk_tolerance_is_risk_neutral(self):
        default = solve_exercise_boundary(
            today_quote_usd_per_day=16_000.0, forecasts=DOWNWARD_FANS, vessel_class=VesselClass.PANAMAX,
            contract_term_days=90, planning_horizon_days=30,
        )
        explicit_neutral = solve_exercise_boundary(
            today_quote_usd_per_day=16_000.0, forecasts=DOWNWARD_FANS, vessel_class=VesselClass.PANAMAX,
            contract_term_days=90, planning_horizon_days=30, risk_tolerance=0.0,
        )
        assert default.strike_usd_per_day == pytest.approx(explicit_neutral.strike_usd_per_day)


class TestBasisAffectsSimulatedProcess:
    """Regression coverage for the real defect found while readying this module
    for production: the strike was route-adjusted (via compute_ceiling's own
    basis handling) but the SIMULATED PRICE PROCESS was calibrated against the
    raw class-level fan -- the paths drifted toward a level the route was never
    actually expected to reach. Fixed by calibrating against
    opt.ceiling.route_adjusted_fans(...) instead of the raw fan.

    With basis_std=0, a pure basis_mean shift `m` scales every forecast
    quantile by (1+m) uniformly. Because the strike (via compute_ceiling) and
    now the simulated fan both scale by the same (1+m), the whole optimal-
    stopping problem is a scaled copy of itself from the first real forecast
    horizon onward (volatility, calibrated from a *ratio* of quantiles, is
    unaffected by a uniform scale) -- so with the fix wired correctly, the
    entire boundary curve at and beyond the first horizon must equal exactly
    (1+m) times the unadjusted boundary, given the same seed. Before the fix,
    only the terminal strike would have moved and this exact relationship
    would not hold.
    """

    def test_boundary_scales_exactly_with_basis_mean_beyond_first_horizon(self):
        basis = BasisEntry(route_family=RouteFamily.INDONESIA_EC_INDIA, basis_mean=-0.2, basis_std=0.0)
        m = basis.basis_mean

        unadjusted = solve_exercise_boundary(
            today_quote_usd_per_day=16_000.0, forecasts=DOWNWARD_FANS, vessel_class=VesselClass.PANAMAX,
            contract_term_days=90, planning_horizon_days=60, n_paths=6000, seed=7,
        )
        adjusted = solve_exercise_boundary(
            today_quote_usd_per_day=16_000.0, forecasts=DOWNWARD_FANS, vessel_class=VesselClass.PANAMAX,
            contract_term_days=90, planning_horizon_days=60, n_paths=6000, seed=7, basis=basis,
        )
        # DOWNWARD_FANS' first real horizon is day 7 (index 6); check there and
        # at day 30 (index 29), both >= the first horizon.
        for day_index in (6, 29):
            expected = unadjusted.exercise_boundary_usd_per_day[day_index] * (1.0 + m)
            actual = adjusted.exercise_boundary_usd_per_day[day_index]
            assert actual == pytest.approx(expected, rel=1e-6)

        assert adjusted.strike_usd_per_day == pytest.approx(unadjusted.strike_usd_per_day * (1.0 + m), rel=1e-6)

    def test_nonzero_basis_std_runs_clean(self):
        """basis_std widens the simulated fan too (not just the strike) --
        smoke test that this doesn't destabilise the solve."""
        basis = BasisEntry(route_family=RouteFamily.INDONESIA_EC_INDIA, basis_mean=-0.08, basis_std=0.10)
        result = solve_exercise_boundary(
            today_quote_usd_per_day=16_000.0, forecasts=DOWNWARD_FANS, vessel_class=VesselClass.PANAMAX,
            contract_term_days=90, planning_horizon_days=60, n_paths=4000, seed=8, basis=basis,
        )
        b = np.array(result.exercise_boundary_usd_per_day)
        assert np.all(np.isfinite(b)) and np.all(b >= 0.0)


class TestBoundarySmoothnessRegression:
    """Regression coverage for a third real defect found by testing this
    module properly with risk_tolerance/basis actually varied (never
    exercised at the risk_tolerance=0/basis=None-only defaults every
    pre-existing test used): the per-day linear continuation-value fit is a
    genuinely weak regression far from maturity, so its coefficients carry
    real sampling variance. Left unmitigated, the REPORTED boundary --
    including boundary[0], which drives ceiling_usd_per_day/action in the
    fused decision -- swung by thousands of dollars between adjacent days
    and between RNG seeds for the *identical* real inputs. Fixed with a
    ridge-regularized fit used only to derive the reported boundary; the
    unbiased OLS fit that drives the actual exercise policy (cash_flow,
    option_value, dominance) is untouched -- see solve_exercise_boundary's
    own inline comments for why a single ridge-shrunk fit for both was
    tried first and rejected (it broke a dominance property).
    """

    def test_boundary_is_smooth_day_to_day_in_the_regime_that_broke(self):
        basis = BasisEntry(route_family=RouteFamily.INDONESIA_EC_INDIA, basis_mean=-0.08, basis_std=0.06)
        result = solve_exercise_boundary(
            today_quote_usd_per_day=16_000.0, forecasts=DOWNWARD_FANS, vessel_class=VesselClass.PANAMAX,
            contract_term_days=90, planning_horizon_days=30, basis=basis, risk_tolerance=1.0,
            n_paths=4000, seed=0,
        )
        b = np.array(result.exercise_boundary_usd_per_day)
        max_jump = np.max(np.abs(np.diff(b)))
        # Before the fix this ranged wildly and unpredictably per seed
        # (>$12,000, up to the full strike). After the fix, this specific
        # regime (risk_tolerance=1.0 stacked with a real basis) still has one
        # real, consistent transition in the curve shape rather than noise --
        # measured at ~$4,550-$4,670 across ten seeds, i.e. a tight,
        # reproducible band, not scattered instability. The threshold here is
        # set with real headroom above that measured band, so it still fails
        # if this regresses toward the old wildly-scattered behaviour.
        assert max_jump < 6_000.0

    def test_day_one_boundary_stable_across_seeds(self):
        """The number that actually drives the lock/wait decision (boundary[0])
        must not be an artifact of which arbitrary RNG seed happened to run --
        before this fix it ranged over roughly $4,000-$18,000 across seeds for
        the same real inputs."""
        boundaries_day_one = []
        for seed in range(6):
            result = solve_exercise_boundary(
                today_quote_usd_per_day=16_000.0, forecasts=DOWNWARD_FANS, vessel_class=VesselClass.PANAMAX,
                contract_term_days=90, planning_horizon_days=30, risk_tolerance=0.0,
                n_paths=4000, seed=seed,
            )
            boundaries_day_one.append(result.exercise_boundary_usd_per_day[0])
        spread = max(boundaries_day_one) - min(boundaries_day_one)
        # Before the fix this spread exceeded $7,000 on a ~$14,000/day strike.
        assert spread < 2_000.0

    def test_ridge_report_fit_does_not_change_which_paths_exercise(self):
        """The policy (cash_flow / option_value) must be identical to a plain-
        OLS-only run -- the ridge fit is report-only by construction, checked
        here directly rather than just inferred from the dominance tests
        still passing."""
        result = solve_exercise_boundary(
            today_quote_usd_per_day=16_000.0, forecasts=DOWNWARD_FANS, vessel_class=VesselClass.PANAMAX,
            contract_term_days=90, planning_horizon_days=30, n_paths=4000, seed=0,
        )
        # option_value is a pure function of cash_flow, which is a pure
        # function of should_exercise, which only ever reads the unbiased
        # OLS c0/c1 -- reproduce that computation independently and compare.
        from opt.ceiling import compute_ceiling

        ceiling_info = compute_ceiling(DOWNWARD_FANS, VesselClass.PANAMAX, 90, risk_tolerance=0.0, basis=None)
        strike = ceiling_info["expected_spot_blended"]
        drift, vol = _calibrate_piecewise_lognormal(16_000.0, DOWNWARD_FANS, VesselClass.PANAMAX, 30)
        paths = _simulate_paths(16_000.0, drift, vol, 4000, np.random.default_rng(0))
        exercise_value = strike - paths
        cash_flow = exercise_value[:, -1].copy()
        for day in range(paths.shape[1] - 1, 0, -1):
            s_t = paths[:, day - 1]
            exercise_now = exercise_value[:, day - 1]
            itm = exercise_now > 0
            if itm.sum() >= 30:
                x, y = s_t[itm], cash_flow[itm]
                design = np.column_stack([np.ones_like(x), x])
                c0, c1 = np.linalg.lstsq(design, y, rcond=None)[0]
                continuation = c0 + c1 * s_t
                should_exercise = itm & (exercise_now > continuation)
                cash_flow = np.where(should_exercise, exercise_now, cash_flow)
        expected_option_value = float(np.mean(cash_flow))
        assert result.option_value_usd_per_day == pytest.approx(expected_option_value, rel=1e-9)


class TestSolveLockOrWait:
    """opt.ceiling + opt.stopping fused into the one production decision."""

    def test_fusion_active_ceiling_matches_boundary_day_one(self):
        lw, sr = solve_lock_or_wait(
            forecasts=DOWNWARD_FANS, cargo_volume_dwt=70_000.0,
            origin_port=PortEnum.NEWCASTLE_AU, dest_port=PortEnum.PARADIP,
            contract_term_days=90, planning_horizon_days=30,
            today_quote_usd_per_day=16_000.0,
        )
        assert sr is not None
        assert lw.ceiling_usd_per_day == pytest.approx(sr.exercise_boundary_usd_per_day[0])

    def test_action_and_ceiling_are_self_consistent(self):
        lw, sr = solve_lock_or_wait(
            forecasts=DOWNWARD_FANS, cargo_volume_dwt=70_000.0,
            origin_port=PortEnum.NEWCASTLE_AU, dest_port=PortEnum.PARADIP,
            contract_term_days=90, planning_horizon_days=30,
            today_quote_usd_per_day=16_000.0,
        )
        expected_action = "LOCK" if lw.today_quote_usd_per_day <= lw.ceiling_usd_per_day else "WAIT"
        assert lw.action == expected_action
        assert sr is not None and lw.action == sr.recommended_action_today

    def test_non_decision_fields_match_the_plain_rule_exactly(self):
        """Fusion must refine only action/ceiling -- everything else (cargo
        identity, route_adjusted, the savings-estimate ingredients) must come
        through from opt.ceiling.lock_or_wait_for_cargo unchanged."""
        from opt.ceiling import lock_or_wait_for_cargo

        kwargs = {
            "forecasts": DOWNWARD_FANS, "cargo_volume_dwt": 70_000.0,
            "origin_port": PortEnum.NEWCASTLE_AU, "dest_port": PortEnum.PARADIP,
            "contract_term_days": 90, "today_quote_usd_per_day": 16_000.0,
        }
        plain = lock_or_wait_for_cargo(**kwargs)
        lw, sr = solve_lock_or_wait(planning_horizon_days=30, **kwargs)

        assert sr is not None  # fusion must actually be active for this to be a real check
        assert lw.vessel_class == plain.vessel_class
        assert lw.origin_port == plain.origin_port
        assert lw.dest_port == plain.dest_port
        assert lw.cargo_volume_dwt == plain.cargo_volume_dwt
        assert lw.route_adjusted == plain.route_adjusted
        assert lw.today_quote_usd_per_day == plain.today_quote_usd_per_day
        assert lw.contract_term_days == plain.contract_term_days
        assert lw.expected_spot_cost_usd_per_day == pytest.approx(plain.expected_spot_cost_usd_per_day)
        assert lw.savings_p50_usd_per_day == pytest.approx(plain.savings_p50_usd_per_day)
        assert lw.savings_p10_usd_per_day == pytest.approx(plain.savings_p10_usd_per_day)

    def test_falls_back_to_plain_rule_when_planning_horizon_too_short_for_any_forecast(self):
        """compute_ceiling can succeed off a single h=90 fan for a 90-day
        contract (nonzero horizon weight) while the LSMC's own calibration,
        filtered to planning_horizon_days, finds no usable anchor at all --
        must degrade to the plain rule, not raise."""
        h90_only = [ForecastFan(vessel_class=VesselClass.SUPRAMAX, horizon_days=90, p10=6_000.0, p50=8_000.0, p90=11_000.0)]
        lw, sr = solve_lock_or_wait(
            forecasts=h90_only, cargo_volume_dwt=50_000.0,  # -> Supramax
            origin_port=PortEnum.BALIKPAPAN, dest_port=PortEnum.VIZAG,
            contract_term_days=90, planning_horizon_days=5,
            today_quote_usd_per_day=9_000.0,
        )
        assert sr is None
        assert lw.action in ("LOCK", "WAIT")
        assert lw.vessel_class == VesselClass.SUPRAMAX

    def test_fused_result_still_route_adjusts_via_origin(self):
        basis_table = {RouteFamily.INDONESIA_EC_INDIA: BasisEntry(
            route_family=RouteFamily.INDONESIA_EC_INDIA, basis_mean=-0.08, basis_std=0.06
        )}
        lw, sr = solve_lock_or_wait(
            forecasts=DOWNWARD_FANS, cargo_volume_dwt=70_000.0,
            origin_port=PortEnum.BALIKPAPAN,  # real Indonesia origin -> has a basis entry
            dest_port=PortEnum.VIZAG,
            contract_term_days=90, planning_horizon_days=30,
            today_quote_usd_per_day=16_000.0,
            basis_table=basis_table,
        )
        assert lw.route_adjusted is True
        assert sr is not None

    def test_runs_clean_across_a_grid_of_real_scenarios(self):
        """Smoke test for the fully fused, production call shape across
        varied risk_tolerance/basis combinations -- no warnings, no crashes."""
        import warnings

        basis_table = {RouteFamily.INDONESIA_EC_INDIA: BasisEntry(
            route_family=RouteFamily.INDONESIA_EC_INDIA, basis_mean=-0.08, basis_std=0.06
        )}
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            for origin, table in ((PortEnum.NEWCASTLE_AU, None), (PortEnum.BALIKPAPAN, basis_table)):
                for risk_tolerance in (0.0, 0.5, 1.0):
                    for quote in (10_000.0, 16_000.0, 25_000.0):
                        lw, _sr = solve_lock_or_wait(
                            forecasts=DOWNWARD_FANS, cargo_volume_dwt=70_000.0,
                            origin_port=origin, dest_port=PortEnum.VIZAG,
                            contract_term_days=90, planning_horizon_days=30,
                            today_quote_usd_per_day=quote,
                            basis_table=table, risk_tolerance=risk_tolerance,
                            n_paths=1000,
                        )
                        assert lw.action in ("LOCK", "WAIT")
                        assert lw.ceiling_usd_per_day > 0.0


class TestWeatherDelayTax:
    """2.4: opt.weather_window.TransitBuffer.expected_delay_days taxes the
    WAIT branch of the fused decision -- see solve_lock_or_wait's own
    arithmetic comment. This is the production decision path, so tested
    hardest of anything in this module, per this chunk's own brief:
    regression first (every pre-existing test above still passes untouched,
    confirmed both before and after this change was made), then the new
    property tests below.
    """

    _KWARGS: ClassVar[dict[str, Any]] = {
        "forecasts": DOWNWARD_FANS, "cargo_volume_dwt": 70_000.0,
        "origin_port": PortEnum.NEWCASTLE_AU, "dest_port": PortEnum.PARADIP,
        "contract_term_days": 90, "planning_horizon_days": 30,
        "today_quote_usd_per_day": 16_000.0,
    }

    def test_weather_delay_days_zero_is_byte_identical_to_the_implicit_default(self):
        """The single most important property in this whole chunk: a caller
        that never passes weather_delay_days at all (every pre-2.4 caller)
        must see no change whatsoever -- checked here as full-object
        equality between "not passed" and "explicitly 0.0", and pinned
        against the real, known-good pre-2.4 numbers so a regression in the
        default path can't hide behind two calls agreeing with each other
        while both silently drifting."""
        implicit_default = solve_lock_or_wait(**self._KWARGS)
        explicit_zero = solve_lock_or_wait(**self._KWARGS, weather_delay_days=0.0)
        assert implicit_default[0] == explicit_zero[0]
        assert implicit_default[1] == explicit_zero[1]

        lw, sr = implicit_default
        assert sr is not None
        assert lw.action == "WAIT"
        assert lw.ceiling_usd_per_day == pytest.approx(11_851.31, abs=0.5)
        assert lw.ceiling_usd_per_day == pytest.approx(sr.exercise_boundary_usd_per_day[0])

    def test_a_large_weather_delay_flips_a_marginal_wait_into_a_lock(self):
        baseline, _ = solve_lock_or_wait(**self._KWARGS)
        assert baseline.action == "WAIT", "fixture must be a real WAIT baseline for this to test anything"
        # The gap between today's $16,000/day quote and the untaxed
        # ~$11,851/day boundary is ~$4,149/day. Because the delay cost is
        # amortised over the contract term (see solve_lock_or_wait's own
        # arithmetic comment -- the tax is a USD/day rate, not a USD total),
        # closing that gap needs roughly
        #   4,149 * 90 / 16,000 ~= 23.3 delay-days.
        # 30 days is comfortably past that; 20 is comfortably short of it,
        # so this brackets the real flip point rather than just asserting
        # one lucky value.
        not_enough, _ = solve_lock_or_wait(**self._KWARGS, weather_delay_days=20.0)
        assert not_enough.action == "WAIT"

        taxed, _ = solve_lock_or_wait(**self._KWARGS, weather_delay_days=30.0)
        assert taxed.action == "LOCK"
        assert taxed.ceiling_usd_per_day > baseline.ceiling_usd_per_day

    def test_weather_delay_never_flips_a_lock_into_a_wait(self):
        """The tax is one-directional by construction -- it can only ever
        raise the boundary, never lower it. Checked directly against a real
        baseline-LOCK scenario across a range of delay magnitudes, including
        an extreme one, so this is a genuine property check, not a single
        lucky data point."""
        upward_kwargs = {
            "forecasts": UPWARD_FANS, "cargo_volume_dwt": 180_000.0,
            "origin_port": PortEnum.NEWCASTLE_AU, "dest_port": PortEnum.PARADIP,
            "contract_term_days": 90, "planning_horizon_days": 60,
            "today_quote_usd_per_day": 21_000.0, "n_paths": 4000, "seed": 3,
        }
        baseline, _ = solve_lock_or_wait(**upward_kwargs)
        assert baseline.action == "LOCK", "fixture must be a real LOCK baseline for this to test anything"
        for delay_days in (0.1, 1.0, 5.0, 50.0):
            taxed, _ = solve_lock_or_wait(**upward_kwargs, weather_delay_days=delay_days)
            assert taxed.action == "LOCK"
            assert taxed.ceiling_usd_per_day >= baseline.ceiling_usd_per_day

    def test_the_arithmetic_amortises_the_delay_cost_over_the_contract_term(self):
        """solve_lock_or_wait's own comment documents an exact formula --
        checked directly here, not just its qualitative (LOCK-favoring)
        effect above.

        **This test previously asserted the un-amortised total** (delay_days
        x today's quote, a USD figure) added directly to the boundary (a
        USD/day figure) -- a real units bug that this test actively pinned
        in place as correct, and that shipped until a user reported seeing
        LOCK on essentially every quote. The delay cost is a one-off total;
        the boundary it adjusts is a daily rate; converting between them is
        the division by contract_term_days below."""
        baseline, _ = solve_lock_or_wait(**self._KWARGS)
        delay_days = 0.75
        taxed, _ = solve_lock_or_wait(**self._KWARGS, weather_delay_days=delay_days)

        expected_tax_usd = delay_days * self._KWARGS["today_quote_usd_per_day"]
        expected_tax_per_day = expected_tax_usd / self._KWARGS["contract_term_days"]
        assert taxed.ceiling_usd_per_day == pytest.approx(baseline.ceiling_usd_per_day + expected_tax_per_day)

        # And the units are genuinely sane: a sub-one-day delay must move a
        # ~$11.8K/day boundary by a small number of dollars per day, not by
        # a five-figure amount (which is exactly what the old formula did).
        assert 0 < expected_tax_per_day < 500

    def test_stopping_result_itself_is_never_taxed(self):
        """stopping_result -- the raw LSMC detail -- must report the UNTAXED
        boundary/action throughout regardless of weather_delay_days; only the
        fused LockWaitResult this function returns reflects the tax. See
        solve_lock_or_wait's own docstring for why."""
        _lw0, sr0 = solve_lock_or_wait(**self._KWARGS)
        # 30 delay-days is past this fixture's own real flip point (~23.3 --
        # see test_a_large_weather_delay_flips_a_marginal_wait_into_a_lock),
        # so the fused result genuinely flips while the raw LSMC detail must
        # not move at all.
        lw_taxed, sr_taxed = solve_lock_or_wait(**self._KWARGS, weather_delay_days=30.0)
        assert sr_taxed == sr0
        assert sr_taxed.recommended_action_today == "WAIT"  # untaxed -- unaffected by the tax
        assert lw_taxed.action == "LOCK"  # the fused result IS taxed
        assert lw_taxed.ceiling_usd_per_day != pytest.approx(sr_taxed.exercise_boundary_usd_per_day[0])
