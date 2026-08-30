"""Real-data tests for Signal/UNCTAD validation.

These regression-test the actual honest findings from development (see
tonnage/validate.py's module docstring) -- they do not assert a clean validation
result, because the real data does not support one, and asserting otherwise would
just be a test that lies alongside the code.
"""
import pytest

from opt.types import VesselClass
from tonnage.basins import PortIndexMissingError, load_port_index
from tonnage.stockflow import reconstruct
from tonnage.validate import (
    KNOWN_SIGNAL_BALLASTER_SNAPSHOTS,
    compare_to_signal,
    implied_ballaster_count,
    summarize_signal_validation,
    unctad_cross_check,
)

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
def comparison(stockflow_result):
    return compare_to_signal(stockflow_result)


def test_known_snapshots_are_the_real_fixed_set_of_12():
    # 3 of the 15 Signal weekly issues on disk report ballaster counts; this is
    # the complete real set, not a sample -- see module docstring for why more
    # can't be scraped (robots.txt Content-Signal: ai-train=no on the archive).
    assert len(KNOWN_SIGNAL_BALLASTER_SNAPSHOTS) == 12
    assert len({s.source_file for s in KNOWN_SIGNAL_BALLASTER_SNAPSHOTS}) == 3


def test_every_grouped_comparison_point_resolves_within_harvest_range(comparison):
    # All three Signal dates fall inside the 2019-2026 PortWatch harvest window, so
    # every point should resolve to a real reconstructed value, not None.
    assert len(comparison) == 10  # 12 snapshots group into 10 (basin, class, date) points
    assert all(p.reconstructed_count is not None for p in comparison)
    assert all(p.ratio is not None for p in comparison)


def test_absolute_scale_is_honestly_not_validated(stockflow_result, comparison):
    # Regression test for the real, load-bearing finding: ratios span orders of
    # magnitude (0.35x to ~26x in development), so no fixed correction factor
    # makes the reconstruction's absolute headcount match Signal's. If a future
    # change to stockflow narrows this spread dramatically, this test should be
    # revisited deliberately -- it is not a bar to keep failing forever, it is a
    # tripwire against quietly forgetting the finding.
    summary = summarize_signal_validation(comparison)
    assert summary.absolute_scale_validated is False
    assert summary.n_points == 10
    assert summary.min_ratio < 1.0, "the real data had at least one under-count (Capesize/Indian Ocean)"
    assert summary.max_ratio > 10.0, "the real data had at least one large over-count (Handysize/Pacific)"


def test_reconstructed_ratio_is_never_absurdly_scaled(comparison):
    # A weak sanity bound, not a validation claim: even acknowledging the scale
    # issue, the reconstruction should land within a few orders of magnitude of
    # the real Signal figures, not off by (say) 10,000x -- that would indicate an
    # actual unit bug rather than the documented total-fleet-vs-free-fleet gap.
    for p in comparison:
        assert 0.01 < p.ratio < 1000, f"{p} ratio is out of even a generous sanity band"


def test_unctad_cross_check_matches_by_construction(stockflow_result):
    # Documented as non-independent -- the anchor mechanism is built to hit this
    # exactly. This test exists to catch a mechanism bug (a basin dropped, a class
    # double-counted), not to claim independent validation.
    checks = unctad_cross_check(stockflow_result)
    assert len(checks) == 4
    for c in checks:
        assert c.reconstructed_total_dwt == pytest.approx(c.unctad_anchor_dwt, rel=1e-6)


def test_implied_ballaster_count_outside_harvest_range_returns_none(stockflow_result):
    from datetime import date

    from tonnage.basins import Basin

    far_future = date(2099, 1, 1)
    assert implied_ballaster_count(stockflow_result, Basin.PACIFIC, VesselClass.CAPESIZE, far_future) is None
