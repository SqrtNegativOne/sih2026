"""Real-data tests for the stock-flow reconstruction.

``reconstruct()`` reads all 128 real PortWatch port CSVs (~14s), so it is computed
once per test module via a fixture rather than once per test function.
"""
import polars as pl
import pytest

from opt.types import VesselClass
from tonnage.basins import Basin, PortIndexMissingError, load_port_index
from tonnage.stockflow import UNCTAD_FLEET_DWT_BY_CLASS, reconstruct

try:
    load_port_index()
    _HAS_DATA = True
except PortIndexMissingError:
    _HAS_DATA = False

pytestmark = pytest.mark.skipif(not _HAS_DATA, reason="P1 port harvest not present on disk")


@pytest.fixture(scope="module")
def result():
    return reconstruct()


def test_nonempty_and_covers_all_basins_and_classes(result):
    df = result.frame
    assert df.height > 0
    assert set(df["basin"].unique().to_list()) == {b.value for b in Basin}
    assert set(df["vessel_class"].unique().to_list()) == {c.value for c in VesselClass}


def test_stock_is_never_negative(result):
    assert result.frame["stock_dwt"].min() >= 0.0


def test_mass_balance_matches_unctad_anchor_exactly_when_unclipped(result):
    # Every basin's correction term is built so the three basins sum to the UNCTAD
    # class total exactly, as long as clipping never bound (clipping would break
    # the exact equality since a clipped basin gives back less than its raw share
    # took out). Confirm both halves of that on the real reconstruction.
    assert result.clipped_fraction == 0.0, (
        "if this ever becomes nonzero on real data, the exact mass-balance assertion "
        "below no longer holds by construction and needs loosening, not deleting"
    )
    df = result.frame
    for cls in VesselClass:
        sub = df.filter(df["vessel_class"] == cls.value)
        totals = sub.group_by("date").agg(pl.col("stock_dwt").sum().alias("total"))
        anchor = UNCTAD_FLEET_DWT_BY_CLASS[cls]
        for v in totals["total"]:
            assert v == pytest.approx(anchor, rel=1e-6), f"{cls} mass balance drifted from its anchor"


def test_pacific_dominates_capesize_stock_matching_australia_china_iron_ore_trade(result):
    # Real-world ground truth: the Australia -> China iron ore corridor is the
    # single largest Capesize trade in existence, and both ends are tagged
    # "pacific" in this basin scheme -- Capesize stock should be heavily
    # concentrated there relative to the other two basins.
    df = result.frame.filter(pl.col("vessel_class") == VesselClass.CAPESIZE.value)
    means = df.group_by("basin").agg(pl.col("stock_dwt").mean().alias("m"))
    by_basin = dict(zip(means["basin"].to_list(), means["m"].to_list()))
    assert by_basin[Basin.PACIFIC.value] > by_basin[Basin.ATLANTIC.value]
    assert by_basin[Basin.PACIFIC.value] > by_basin[Basin.INDIAN_OCEAN.value]


def test_reconstruction_uses_the_real_full_port_set(result):
    assert result.n_ports_used >= 100
    assert result.n_ports_used + result.n_ports_skipped == len(load_port_index())


def test_bounded_window_keeps_raw_trajectory_within_a_realistic_scale(result):
    # This is the regression test for the drift bug found during development: an
    # unbounded cumulative net-flow integral drifted into the billions of tonnes
    # (vs a ~300M dwt real Capesize fleet) because the harvest is export-heavy.
    # The bounded trailing-window raw trajectory must stay within a sane multiple
    # of the largest class anchor, not diverge with series length.
    max_anchor = max(UNCTAD_FLEET_DWT_BY_CLASS.values())
    assert result.frame["raw_stock_dwt"].abs().max() < max_anchor
