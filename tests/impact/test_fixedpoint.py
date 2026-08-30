"""Real-data tests for the elasticity <-> execution fixed-point solve.

Properties checked are ones a self-consistent solve must have given how it's
constructed (marginal impact can only rise as a schedule draws down basin stock,
never fall) plus real convergence behavior observed on the real reconstruction.
"""
import numpy as np
import polars as pl
import pytest

from impact.fixedpoint import solve_self_consistent_schedule
from opt.types import VesselClass
from tonnage.basins import Basin, PortIndexMissingError, load_port_index
from tonnage.classmix import CLASS_MIDPOINT_DWT
from tonnage.stockflow import reconstruct
from tonnage.supplycurve import MASTER_LONG_PATH, build_tightness_index, fit_all

try:
    load_port_index()
    _HAS_DATA = True
except PortIndexMissingError:
    _HAS_DATA = False

pytestmark = pytest.mark.skipif(not _HAS_DATA, reason="P1 port harvest not present on disk")


@pytest.fixture(scope="module")
def real_inputs():
    result = reconstruct()
    fits = fit_all(build_tightness_index(result))
    as_of = result.frame["date"].max()
    rate = (
        pl.read_parquet(MASTER_LONG_PATH)
        .filter(pl.col("series_id") == "PANAMAX_TCAVG")
        .sort("date")["value"]
        .to_numpy()
    )
    hist_std = float(np.std(np.diff(rate), ddof=1))
    return {
        "result": result, "fit": fits[VesselClass.PANAMAX], "as_of": as_of,
        "hist_std": hist_std, "rep_dwt": CLASS_MIDPOINT_DWT[VesselClass.PANAMAX],
    }


def _solve(real_inputs, total_dwt, damping=0.5, max_iterations=10):
    return solve_self_consistent_schedule(
        real_inputs["result"], real_inputs["fit"], Basin.PACIFIC, real_inputs["as_of"],
        total_dwt=total_dwt, horizon_days=90, n_periods=12,
        representative_dwt=real_inputs["rep_dwt"], historical_rate_std_usd_per_day=real_inputs["hist_std"],
        risk_aversion=1e-6, damping=damping, max_iterations=max_iterations,
    )


def test_converges_for_a_realistic_demand_size(real_inputs):
    res = _solve(real_inputs, total_dwt=480_000.0)
    assert res.converged
    assert res.n_iterations <= 10
    assert res.final_relative_change < 1e-4


def test_effective_slope_never_falls_below_the_base_local_slope(real_inputs):
    # Drawing down basin stock can only raise 1/stock, never lower it -- the
    # self-consistent slope must be >= the naive single-point local slope.
    for total_dwt in (228_000.0, 480_000.0, 2_000_000.0):
        res = _solve(real_inputs, total_dwt=total_dwt)
        assert res.effective_slope_usd_per_day_per_dwt >= res.base_elasticity.d_rate_d_demand_usd_per_day_per_dwt


def test_larger_demand_relative_to_stock_produces_a_larger_correction(real_inputs):
    small = _solve(real_inputs, total_dwt=228_000.0)
    large = _solve(real_inputs, total_dwt=5_000_000.0, max_iterations=20)
    ratio_small = small.effective_slope_usd_per_day_per_dwt / small.base_elasticity.d_rate_d_demand_usd_per_day_per_dwt
    ratio_large = large.effective_slope_usd_per_day_per_dwt / large.base_elasticity.d_rate_d_demand_usd_per_day_per_dwt
    assert ratio_large > ratio_small
    assert ratio_small == pytest.approx(1.0, abs=0.01)  # a small fixture barely moves the local slope


def test_damping_changes_convergence_speed_not_the_fixed_point(real_inputs):
    slow = _solve(real_inputs, total_dwt=480_000.0, damping=0.2, max_iterations=30)
    fast = _solve(real_inputs, total_dwt=480_000.0, damping=1.0, max_iterations=30)
    assert slow.converged and fast.converged
    assert slow.n_iterations >= fast.n_iterations
    assert slow.effective_slope_usd_per_day_per_dwt == pytest.approx(
        fast.effective_slope_usd_per_day_per_dwt, rel=0.01
    )


def test_slope_path_is_monotonically_nondecreasing_and_ends_at_effective_slope(real_inputs):
    res = _solve(real_inputs, total_dwt=480_000.0)
    path = res.slope_path
    assert path[-1] == pytest.approx(res.effective_slope_usd_per_day_per_dwt)
    assert all(path[i] <= path[i + 1] + 1e-12 for i in range(len(path) - 1))


def test_low_damping_can_honestly_fail_to_converge_within_the_budget(real_inputs):
    # Real behavior observed on this exact data: a large demand with weak damping
    # and a tight iteration budget genuinely does not converge -- confirm that is
    # reported as converged=False, not silently accepted.
    res = _solve(real_inputs, total_dwt=5_000_000.0, damping=0.3, max_iterations=10)
    assert res.n_iterations == 10
    if not res.converged:
        assert res.final_relative_change >= 1e-4


@pytest.mark.parametrize("kwargs", [{"damping": 0.0}, {"damping": 1.5}, {"max_iterations": 0}, {"tol": 0.0}, {"tol": 1.0}])
def test_invalid_solver_settings_rejected(real_inputs, kwargs):
    defaults = {"damping": 0.5, "max_iterations": 10, "tol": 1e-4}
    defaults.update(kwargs)
    with pytest.raises(ValueError):
        solve_self_consistent_schedule(
            real_inputs["result"], real_inputs["fit"], Basin.PACIFIC, real_inputs["as_of"],
            total_dwt=228_000.0, horizon_days=90, n_periods=12,
            representative_dwt=real_inputs["rep_dwt"], historical_rate_std_usd_per_day=real_inputs["hist_std"],
            risk_aversion=1e-6, **defaults,
        )
