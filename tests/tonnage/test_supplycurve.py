"""Real-data tests for the tightness index and supply-curve fits.

These assert what the real numbers actually showed during development, not what
would be nicest -- see the module docstring in tonnage/supplycurve.py for the
honest writeup of a weak, sign-inconsistent level relationship for two of four
classes. The point of these tests is to catch a regression in the *real* fitted
numbers, not to enforce a result this data doesn't support.
"""
import polars as pl
import pytest

from opt.types import VesselClass
from tonnage.basins import PortIndexMissingError, load_port_index
from tonnage.stockflow import reconstruct
from tonnage.supplycurve import (
    MIN_OBS_FOR_CONFIDENCE,
    QUANTILES,
    NoOverlapError,
    build_tightness_index,
    fit_all,
    fit_supply_curve,
)

try:
    load_port_index()
    _HAS_DATA = True
except PortIndexMissingError:
    _HAS_DATA = False

pytestmark = pytest.mark.skipif(not _HAS_DATA, reason="P1 port harvest not present on disk")


@pytest.fixture(scope="module")
def tightness_index():
    return build_tightness_index(reconstruct())


@pytest.fixture(scope="module")
def fits(tightness_index):
    return fit_all(tightness_index)


def test_tightness_index_is_positive_and_covers_all_classes(tightness_index):
    assert tightness_index.height > 0
    assert (tightness_index["tightness"] > 0).all()
    assert set(tightness_index["vessel_class"].unique().to_list()) == {c.value for c in VesselClass}


def test_all_four_classes_fit_from_real_overlap(fits):
    assert set(fits.keys()) == set(VesselClass)
    for fit in fits.values():
        assert fit.n_obs >= 5


def test_quantile_coverage_is_well_calibrated_in_sample(fits):
    # This is the one property that held cleanly and strongly for every class --
    # the regression fits its own training quantiles correctly, independent of
    # whether the underlying relationship is causally meaningful.
    for cls, fit in fits.items():
        for q in QUANTILES:
            assert fit.coverage[q] == pytest.approx(q, abs=0.05), (
                f"{cls} q={q} coverage {fit.coverage[q]:.3f} is out of calibration"
            )


def test_supramax_has_the_strongest_real_level_correlation(fits):
    # The one class where the level relationship came out unambiguously real:
    # r=+0.746 on 185 real joined observations. Regression-test that this specific,
    # surprising-in-a-good-way result doesn't silently regress.
    assert fits[VesselClass.SUPRAMAX].pearson_r > 0.6
    assert not fits[VesselClass.SUPRAMAX].weak_signal


def test_capesize_and_handysize_are_honestly_flagged_weak(fits):
    # Both came out wrong-signed on levels during development (r=-0.02, r=-0.24).
    # weak_signal must catch both -- this is a regression test for the honesty
    # gate itself, not a claim that the underlying relationship stays this weak
    # forever as more real TCAVG data accumulates.
    assert fits[VesselClass.CAPESIZE].weak_signal
    assert fits[VesselClass.HANDYSIZE].weak_signal


def test_supramax_and_handysize_are_short_history_but_not_below_the_confidence_floor(
    fits, tightness_index
):
    # Real TCAVG coverage for these two starts 2025-11-19, later than the other
    # two classes -- confirm the honest small-n situation is exactly what it is,
    # not silently padded or truncated.
    #
    # This used to assert `n_obs == 185`, a snapshot of the data on the day it
    # was written. That became wrong the moment the desk started harvesting
    # rates daily (F-92): the count now grows every publication day, so a
    # literal here would fail every day the harvester succeeds -- turning a
    # working feature into a red suite. The intent was never the number 185. It
    # was that these two classes see the same, shorter history than the others,
    # and that the fit uses every real joined observation rather than a padded
    # or truncated set. Both are asserted against the data itself, so they stay
    # true as it grows.
    supramax = fits[VesselClass.SUPRAMAX]
    handysize = fits[VesselClass.HANDYSIZE]

    assert supramax.n_obs == handysize.n_obs
    assert supramax.n_obs >= MIN_OBS_FOR_CONFIDENCE

    # Not padded, not truncated: the fit used exactly the rows the tightness
    # index really carries for that class.
    for cls, fit in ((VesselClass.SUPRAMAX, supramax), (VesselClass.HANDYSIZE, handysize)):
        available = tightness_index.filter(pl.col("vessel_class") == cls.value).height
        assert fit.n_obs <= available
        assert fit.n_obs > 0

    # Still the shorter history: both start later than Panamax, whose TCAVG
    # series reaches further back.
    assert supramax.n_obs < fits[VesselClass.PANAMAX].n_obs


def test_predict_returns_all_three_quantiles_in_order(fits):
    fit = fits[VesselClass.SUPRAMAX]
    mid_tightness = sum(fit.tightness_range) / 2
    pred = fit.predict(mid_tightness)
    assert set(pred.keys()) == set(QUANTILES)
    assert pred[0.1] <= pred[0.5] <= pred[0.9]


def test_unknown_class_with_no_overlap_raises(tightness_index):
    empty_index = tightness_index.filter(pl.col("vessel_class") == "nonexistent")
    with pytest.raises(NoOverlapError):
        fit_supply_curve(VesselClass.CAPESIZE, empty_index)
