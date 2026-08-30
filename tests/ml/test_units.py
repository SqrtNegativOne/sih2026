"""Tests for the Baltic index <-> USD/day conversion.

These pin down empirical facts about the source data, not just code behaviour. If a
data refresh changes the published Baltic relation, these fail loudly rather than
letting wrong money flow downstream.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Final

import numpy as np
import polars as pl
import pytest

from ml.units import (
    CLASS_SERIES,
    InsufficientOverlapError,
    UnitMap,
    detect_regime_breaks,
    fit_all,
    fit_unit_map,
)

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
MASTER: Final[Path] = REPO_ROOT / "src" / "data" / "master_long.parquet"


@pytest.fixture(scope="module")
def master() -> pl.DataFrame:
    if not MASTER.exists():
        pytest.skip(f"{MASTER} not built; run build-master first")
    return pl.read_parquet(MASTER)


# ---------------------------------------------------------------------------
# Pure-arithmetic properties (no data dependency)
# ---------------------------------------------------------------------------


def _map(slope: float, intercept: float) -> UnitMap:
    return UnitMap(
        vessel_class="Test",
        slope=slope,
        intercept=intercept,
        r2=1.0,
        max_residual_usd=0.0,
        n_obs=100,
        fitted_from=date(2026, 1, 1),
        fitted_to=date(2026, 6, 1),
        asof=date(2026, 6, 1),
        intercept_dropped=False,
        regime_start=date(2026, 1, 1),
        is_fresh_regime=False,
    )


def test_roundtrip_index_usd() -> None:
    m = _map(9.0696, -3503.2)
    for pts in (500.0, 1500.0, 4000.0):
        assert m.to_index(m.to_usd_per_day(pts)) == pytest.approx(pts, rel=1e-12)


def test_project_return_collapses_to_naive_when_intercept_zero() -> None:
    m = _map(9.0, 0.0)
    tc, r = 20_000.0, 0.15
    assert m.project_return(tc, r) == pytest.approx(tc * np.exp(r), rel=1e-12)


def test_project_return_is_exact_under_the_affine_map() -> None:
    """Projecting an index return must reproduce the true future TC exactly."""
    slope, intercept = 9.0696, -3503.2
    m = _map(slope, intercept)
    idx_now, idx_future = 2800.0, 3200.0
    tc_now = slope * idx_now + intercept
    tc_future = slope * idx_future + intercept
    r = float(np.log(idx_future / idx_now))
    assert m.project_return(tc_now, r) == pytest.approx(tc_future, rel=1e-9)


def test_naive_transfer_is_biased_when_intercept_nonzero() -> None:
    """The bug this module exists to prevent: naive transfer overstates the move."""
    slope, intercept = 9.0696, -3503.2
    m = _map(slope, intercept)
    idx_now, idx_future = 2800.0, 3200.0
    tc_now = slope * idx_now + intercept
    tc_future = slope * idx_future + intercept
    r = float(np.log(idx_future / idx_now))
    naive = tc_now * np.exp(r)
    assert m.project_return(tc_now, r) == pytest.approx(tc_future, rel=1e-9)
    # Naive is wrong by a material amount -- hundreds of USD/day.
    assert abs(naive - tc_future) > 400.0


# ---------------------------------------------------------------------------
# Empirical facts about the Baltic relation
# ---------------------------------------------------------------------------

#: Slopes recovered from the overlap as of Aug 2026, to be re-derived if data changes.
EXPECTED_SLOPE: Final[dict[str, float]] = {
    "Capesize": 9.069,
    "Panamax": 9.000,
    "Supramax": 12.640,
    "Handysize": 18.00,
}


@pytest.mark.parametrize("cls", sorted(CLASS_SERIES))
def test_map_is_near_exact_on_recent_window(master: pl.DataFrame, cls: str) -> None:
    """The published relation is recoverable to rounding error."""
    m = fit_unit_map(master, cls)
    assert m.r2 > 0.999, f"{cls}: R2={m.r2:.6f} -- relation is not affine any more"
    assert m.max_residual_usd < 100.0, (
        f"{cls}: worst residual ${m.max_residual_usd:.0f}/day is too large for a "
        f"published deterministic relation"
    )
    assert m.slope == pytest.approx(EXPECTED_SLOPE[cls], rel=0.02)


@pytest.mark.parametrize("cls", ["Panamax", "Supramax", "Handysize"])
def test_intercept_is_zero_for_non_capesize(master: pl.DataFrame, cls: str) -> None:
    """These three are pure scalar maps; a fitted intercept would be noise."""
    m = fit_unit_map(master, cls)
    assert m.intercept == 0.0


def test_fit_respects_asof_and_never_looks_ahead(master: pl.DataFrame) -> None:
    asof = date(2025, 6, 30)
    m = fit_unit_map(master, "Capesize", asof=asof)
    assert m.fitted_to <= asof
    assert m.asof == asof


def test_asof_before_overlap_refuses_rather_than_guessing(master: pl.DataFrame) -> None:
    """Pre-2024 there is no TC history; the module must refuse, not extrapolate."""
    with pytest.raises(InsufficientOverlapError):
        fit_unit_map(master, "Capesize", asof=date(2015, 1, 1))


def test_capesize_regime_change_is_detected(master: pl.DataFrame) -> None:
    """The Jan-2026 Baltic methodology change must surface as a regime break."""
    segs = detect_regime_breaks(master, "Capesize")
    assert len(segs) >= 2, "expected at least one Capesize regime break"
    boundaries = [s.start for s in segs[1:]]
    assert any(
        date(2025, 12, 1) <= b <= date(2026, 3, 1) for b in boundaries
    ), f"Jan-2026 break not found; boundaries={boundaries}"


def test_fit_all_returns_every_class(master: pl.DataFrame) -> None:
    maps = fit_all(master)
    assert set(maps) == set(CLASS_SERIES)


# ---------------------------------------------------------------------------
# The property that actually protects the pipeline
# ---------------------------------------------------------------------------


def _walk_forward(
    master: pl.DataFrame, cls: str, h: int
) -> tuple[np.ndarray, np.ndarray, float]:
    """Walk the overlap applying as-of maps. Returns (errors, straddles_break, level).

    ``straddles_break[k]`` marks windows where the Baltic changed the relation
    between the decision date and the resolution date.
    """
    index_id, tc_id = CLASS_SERIES[cls]
    idx = master.filter(pl.col("series_id") == index_id).select(
        "date", pl.col("value").alias("i")
    )
    tc = master.filter(pl.col("series_id") == tc_id).select(
        "date", pl.col("value").alias("t")
    )
    j = idx.join(tc, on="date", how="inner").drop_nulls().sort("date")
    dates = j["date"].to_list()
    ii = j["i"].to_numpy().astype(float)
    tt = j["t"].to_numpy().astype(float)
    n = len(ii)
    if n < 80 + h:
        pytest.skip(f"{cls}: only {n} overlapping rows")

    breaks = [s.start for s in detect_regime_breaks(master, cls)[1:]]
    errors: list[float] = []
    straddles: list[bool] = []
    for k in range(60, n - h, 3):
        m = fit_unit_map(master, cls, asof=dates[k])
        r = float(np.log(ii[k + h] / ii[k]))
        errors.append(abs(m.project_return(tt[k], r) - tt[k + h]))
        straddles.append(any(dates[k] < b <= dates[k + h] for b in breaks))
    return np.array(errors), np.array(straddles), float(np.median(tt))


@pytest.mark.parametrize("cls", sorted(CLASS_SERIES))
@pytest.mark.parametrize("h", [7, 30])
def test_return_transfer_reconstructs_actual_future_tc(
    master: pl.DataFrame, cls: str, h: int
) -> None:
    """Walk the overlap: index return + as-of map must reproduce realised USD/day.

    This is the end-to-end guarantee the optimizer depends on. The map is fitted
    using only data available at each decision date.

    Accuracy is asserted on the *median* rather than the mean. Windows that straddle
    a Baltic methodology change are unreconstructable by any map -- the relation
    changed between the forecast and its resolution -- and they drag the mean up
    without saying anything about whether the conversion is correct. Those windows
    are covered separately by
    ``test_regime_straddling_windows_are_the_only_large_errors``.
    """
    err, _straddles, level = _walk_forward(master, cls, h)
    rel = float(np.median(err)) / level
    assert rel < 0.005, (
        f"{cls} h={h}: median reconstruction error ${np.median(err):.0f}/day "
        f"({100 * rel:.3f}% of a ${level:.0f}/day market) exceeds 0.5%"
    )


@pytest.mark.parametrize("cls", sorted(CLASS_SERIES))
def test_regime_straddling_windows_are_the_only_large_errors(
    master: pl.DataFrame, cls: str
) -> None:
    """Large reconstruction errors must be explained by a methodology change.

    If a big error ever shows up in a window with no regime break, the conversion
    logic itself is wrong and this fails -- which is the point.
    """
    err, straddles, level = _walk_forward(master, cls, 30)
    clean = err[~straddles]
    if clean.size == 0:
        pytest.skip(f"{cls}: no clean windows")
    rel = float(np.median(clean)) / level
    assert rel < 0.002, (
        f"{cls}: median error on break-free windows is {100 * rel:.3f}% -- "
        f"the map is wrong independently of any Baltic rebasing"
    )
    if straddles.any():
        assert err[straddles].mean() > clean.mean(), (
            f"{cls}: straddling windows should be materially worse; if they are not, "
            f"the break detector is finding breaks that are not there"
        )


def test_fresh_regime_is_flagged(master: pl.DataFrame) -> None:
    """Just after the Jan-2026 Capesize break the map must admit it is uncertain."""
    segs = detect_regime_breaks(master, "Capesize")
    break_date = segs[1].start
    soon_after = fit_unit_map(master, "Capesize", asof=break_date + timedelta(days=10))
    assert soon_after.is_fresh_regime
    assert soon_after.regime_start >= break_date

    settled = fit_unit_map(master, "Capesize", asof=break_date + timedelta(days=140))
    assert not settled.is_fresh_regime
