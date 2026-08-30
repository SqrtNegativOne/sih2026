"""End-to-end: real elasticity + real historical rate volatility -> a real
execution frontier, for the plan's own demo scenario (480kt over a quarter).

This is the test that caught a real, serious unit bug during development: the
first version of impact_parameters_from_elasticity priced this exact scenario's
permanent impact at $52 billion (see execution.py's docstring for the fix). These
tests pin the corrected, plausible numbers down as a regression guard.
"""
import numpy as np
import polars as pl
import pytest

from impact.elasticity import local_elasticity
from impact.execution import (
    efficient_frontier,
    impact_parameters_from_elasticity,
    solve_execution_schedule,
)
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
def real_params():
    result = reconstruct()
    fits = fit_all(build_tightness_index(result))
    as_of = result.frame["date"].max()
    est = local_elasticity(result, fits[VesselClass.PANAMAX], Basin.PACIFIC, as_of, quantile=0.5)

    rate = (
        pl.read_parquet(MASTER_LONG_PATH)
        .filter(pl.col("series_id") == "PANAMAX_TCAVG")
        .sort("date")["value"]
        .to_numpy()
    )
    hist_std = float(np.std(np.diff(rate), ddof=1))

    return impact_parameters_from_elasticity(
        est.d_rate_d_demand_usd_per_day_per_dwt,
        hist_std,
        representative_dwt=CLASS_MIDPOINT_DWT[VesselClass.PANAMAX],
        assumed_charter_days=30.0,
    )


def test_real_impact_parameters_are_positive_and_finite(real_params):
    assert real_params.permanent_impact_usd_per_dwt2 > 0
    assert real_params.temporary_impact_usd_per_dwt2 > 0
    assert real_params.daily_volatility_usd_per_dwt > 0
    for v in real_params.__dict__.values():
        assert np.isfinite(v)


def test_480kt_quarter_scenario_costs_a_plausible_amount_not_billions(real_params):
    # The plan's own example: SAIL fixes 480kt over Q3. A single-clip execution's
    # permanent-impact cost should land in a plausible range for real freight
    # economics (order of $100k-$10M for this volume) -- not the $52 billion the
    # unnormalized bridge produced before the representative_dwt fix.
    sched = solve_execution_schedule(
        total_dwt=480_000.0, horizon_days=90, n_periods=1,
        permanent_impact_usd_per_dwt2=real_params.permanent_impact_usd_per_dwt2,
        temporary_impact_usd_per_dwt2=real_params.temporary_impact_usd_per_dwt2,
        daily_volatility_usd_per_dwt=real_params.daily_volatility_usd_per_dwt,
        risk_aversion=0.0,
    )
    assert 1e4 < sched.expected_cost_usd < 1e8


def test_spreading_the_requirement_reduces_variance_on_real_parameters(real_params):
    frontier = efficient_frontier(
        total_dwt=480_000.0, horizon_days=90, n_periods=12,
        permanent_impact_usd_per_dwt2=real_params.permanent_impact_usd_per_dwt2,
        temporary_impact_usd_per_dwt2=real_params.temporary_impact_usd_per_dwt2,
        daily_volatility_usd_per_dwt=real_params.daily_volatility_usd_per_dwt,
        risk_aversion_grid=(0.0, 1e-6, 1e-4, 1e-2),
    )
    variances = [s.cost_variance_usd2 for s in frontier]
    assert variances == sorted(variances, reverse=True)
    # Buying that variance reduction costs something in expected terms -- the
    # frontier is a real trade-off, not a free lunch, even on real parameters.
    assert frontier[-1].expected_cost_usd > frontier[0].expected_cost_usd


def test_representative_dwt_must_be_positive():
    from impact.execution import impact_parameters_from_elasticity as f

    with pytest.raises(ValueError):
        f(0.01, 100.0, representative_dwt=0.0)
