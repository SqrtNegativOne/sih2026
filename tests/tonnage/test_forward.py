"""Tests for the forward projection, driven off the real stockflow reconstruction."""
import polars as pl
import pytest

from opt.types import VesselClass
from tonnage.basins import Basin, PortIndexMissingError, load_port_index
from tonnage.forward import Z_80, project_forward, project_tightness_forward
from tonnage.stockflow import reconstruct
from tonnage.supplycurve import build_tightness_index

try:
    load_port_index()
    _HAS_DATA = True
except PortIndexMissingError:
    _HAS_DATA = False

pytestmark = pytest.mark.skipif(not _HAS_DATA, reason="P1 port harvest not present on disk")


@pytest.fixture(scope="module")
def stockflow_result():
    return reconstruct()


@pytest.fixture(scope="module")
def projection(stockflow_result):
    return project_forward(stockflow_result, horizon_days=90)


def test_covers_full_horizon_for_every_basin_class(projection):
    df = projection.frame
    for basin in Basin:
        for cls in VesselClass:
            sub = df.filter((pl.col("basin") == basin.value) & (pl.col("vessel_class") == cls.value))
            assert sub.height == 90
            assert set(sub["horizon_days"].to_list()) == set(range(1, 91))


def test_bands_are_ordered_and_nonnegative(projection):
    df = projection.frame
    assert (df["p10"] >= 0).all()
    assert (df["p10"] <= df["p50"]).all()
    assert (df["p50"] <= df["p90"]).all()


def test_p50_at_horizon_one_is_close_to_last_observed_stock(stockflow_result, projection):
    # A single day out, the point projection should barely move from the last real
    # observation -- it is one day of trailing drift away, not a jump.
    last = (
        stockflow_result.frame.filter(
            (pl.col("basin") == Basin.PACIFIC.value) & (pl.col("vessel_class") == VesselClass.CAPESIZE.value)
        )
        .sort("date")["stock_dwt"][-1]
    )
    day1 = projection.frame.filter(
        (pl.col("basin") == Basin.PACIFIC.value)
        & (pl.col("vessel_class") == VesselClass.CAPESIZE.value)
        & (pl.col("horizon_days") == 1)
    )["p50"][0]
    # within 5% of the fleet-class anchor scale, not an exact match (there is real
    # single-day drift) -- this catches a projection that jumps to something
    # unrelated to the real starting point, not tiny day-1 drift.
    assert abs(day1 - last) < 0.05 * max(last, 1.0)


def test_uncertainty_band_widens_with_horizon(projection):
    # Random-walk-with-drift uncertainty grows as sqrt(horizon); check the
    # unclipped width (p90 - p50, which does not saturate at zero the way p50 -
    # p10 can once the lower band hits the non-negativity floor) is larger at
    # horizon 90 than at horizon 5 for a basin/class with real nonzero spread.
    df = projection.frame.filter(
        (pl.col("basin") == Basin.PACIFIC.value) & (pl.col("vessel_class") == VesselClass.CAPESIZE.value)
    )
    width_5 = df.filter(pl.col("horizon_days") == 5)["p90"][0] - df.filter(pl.col("horizon_days") == 5)["p50"][0]
    width_90 = df.filter(pl.col("horizon_days") == 90)["p90"][0] - df.filter(pl.col("horizon_days") == 90)["p50"][0]
    if width_5 == 0 and width_90 == 0:
        pytest.skip("this basin/class had zero day-to-day spread in the trend window")
    ratio = width_90 / width_5 if width_5 > 0 else float("inf")
    # sqrt(90/5) ~= 4.24 -- allow generous tolerance since drift also shifts p50
    assert ratio == pytest.approx(math_sqrt_18, rel=0.5)


math_sqrt_18 = 18**0.5


def test_empty_stockflow_result_rejected():
    from tonnage.stockflow import StockflowResult

    empty = StockflowResult(frame=pl.DataFrame(), n_ports_used=0, n_ports_skipped=0, clipped_fraction=0.0)
    with pytest.raises(ValueError):
        project_forward(empty)


def test_z80_is_the_standard_80_percent_central_value():
    from scipy import stats

    assert Z_80 == pytest.approx(stats.norm.ppf(0.9), abs=1e-3)


# ---------------------------------------------------------------------------
# P3 requirement 5 -- forward TIGHTNESS index (the relative-index branch).
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def tightness_index(stockflow_result):
    return build_tightness_index(stockflow_result)


@pytest.fixture(scope="module")
def tightness_projection(tightness_index):
    return project_tightness_forward(tightness_index, horizon_days=90)


def test_tightness_projection_is_labelled_relative(tightness_projection):
    assert tightness_projection.index_type == "RELATIVE"


def test_tightness_projection_covers_full_horizon_for_every_class(tightness_projection):
    df = tightness_projection.frame
    for cls in VesselClass:
        sub = df.filter(pl.col("vessel_class") == cls.value)
        assert sub.height == 90
        assert set(sub["horizon_days"].to_list()) == set(range(1, 91))
    # no basin column -- this is a class-level, basin-pooled projection.
    assert "basin" not in df.columns


def test_tightness_bands_are_ordered_and_nonnegative(tightness_projection):
    df = tightness_projection.frame
    assert (df["p10"] >= 0).all()
    assert (df["p10"] <= df["p50"]).all()
    assert (df["p50"] <= df["p90"]).all()


def test_tightness_p50_at_horizon_one_is_close_to_last_observed_value(tightness_index, tightness_projection):
    last = tightness_index.filter(pl.col("vessel_class") == VesselClass.CAPESIZE.value).sort("date")["tightness"][-1]
    day1 = tightness_projection.frame.filter(
        (pl.col("vessel_class") == VesselClass.CAPESIZE.value) & (pl.col("horizon_days") == 1)
    )["p50"][0]
    assert abs(day1 - last) < 0.20 * max(last, 1e-6)


def test_tightness_uncertainty_band_widens_with_horizon(tightness_projection):
    df = tightness_projection.frame.filter(pl.col("vessel_class") == VesselClass.HANDYSIZE.value)
    width_5 = df.filter(pl.col("horizon_days") == 5)["p90"][0] - df.filter(pl.col("horizon_days") == 5)["p50"][0]
    width_90 = df.filter(pl.col("horizon_days") == 90)["p90"][0] - df.filter(pl.col("horizon_days") == 90)["p50"][0]
    if width_5 == 0 and width_90 == 0:
        pytest.skip("this class had zero day-to-day tightness spread in the trend window")
    assert width_90 >= width_5


def test_empty_tightness_index_rejected():
    with pytest.raises(ValueError):
        project_tightness_forward(pl.DataFrame(schema={"date": pl.Date, "vessel_class": pl.Utf8, "tightness": pl.Float64}))
