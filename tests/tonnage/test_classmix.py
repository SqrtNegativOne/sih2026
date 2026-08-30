"""Real-data tests for the class-mix weighting -- no synthetic port fixtures.

Every assertion here is checked against actual PortWatch CSVs on disk. If they
haven't been pulled, the suite skips rather than fails.
"""
import math

import pytest

from opt.types import VesselClass
from tonnage.basins import PortIndexMissingError, load_port_index, port_csv_path
from tonnage.classmix import (
    CLASS_MIDPOINT_DWT,
    NoActivityError,
    build_fleet_class_mix,
    class_weights,
    mean_parcel_size,
    port_class_weights,
)

try:
    load_port_index()
    _HAS_DATA = True
except PortIndexMissingError:
    _HAS_DATA = False

pytestmark = pytest.mark.skipif(not _HAS_DATA, reason="P1 port harvest not present on disk")


def test_port_hedland_mean_parcel_size_matches_hand_computed_value():
    # Hand-computed independently during design: 23,648 calls, ~3.977bn tonnes,
    # mean ~168,171 t. Re-derived here through the real module, not re-typed.
    estimate = mean_parcel_size("Port_Hedland_AU")
    assert estimate.mean_parcel_t == pytest.approx(168_171, rel=0.01)
    assert estimate.n_calls > 20_000


def test_port_hedland_is_overwhelmingly_capesize():
    # Port Hedland is the world's largest iron-ore export port and is structurally
    # Capesize -- real, unambiguous ground truth.
    weights = port_class_weights("Port_Hedland_AU")
    assert weights[VesselClass.CAPESIZE] > 0.85
    assert sum(weights.values()) == pytest.approx(1.0)


def test_kwinana_is_predominantly_handysize():
    # Kwinana/Fremantle is a grain port serving smaller parcels.
    weights = port_class_weights("Kwinana_AU")
    assert weights[VesselClass.HANDYSIZE] == max(weights.values())


def test_newcastle_is_not_forced_into_a_single_class():
    # Newcastle's real mean parcel size (~73,000 t from the aggregate ratio) sits
    # between Panamax and Capesize -- a genuinely mixed coal port (Capesize +
    # Newcastlemax alongside Panamax-range vessels). The weight should reflect
    # that blend rather than collapse onto one class the way Hedland does.
    weights = port_class_weights("Newcastle_AU")
    top_two = sorted(weights.values(), reverse=True)[:2]
    assert top_two[0] < 0.85, "a genuinely mixed port should not saturate like Hedland does"
    assert top_two[1] > 0.05, "the second-largest class should still carry real weight"


def test_weights_are_monotonic_in_log_size():
    # As mean parcel size rises past a class's midpoint, that class's weight should
    # rise then fall -- check the ordering makes sense at the four real midpoints.
    for cls, mid in CLASS_MIDPOINT_DWT.items():
        weights = class_weights(mid)
        assert weights[cls] == max(weights.values()), f"{cls} should peak at its own midpoint"


def test_weights_sum_to_one_across_a_wide_range():
    for t in (5_000, 20_000, 50_000, 90_000, 150_000, 300_000):
        w = class_weights(float(t))
        assert sum(w.values()) == pytest.approx(1.0)
        assert all(v >= 0 for v in w.values())


def test_nonpositive_parcel_size_rejected():
    with pytest.raises(ValueError):
        class_weights(0.0)
    with pytest.raises(ValueError):
        class_weights(-100.0)


def test_zero_activity_port_raises_not_silently_zero():
    # Find a real port file with genuinely zero dry-bulk calls, if one exists in
    # this harvest, to prove the guard fires on real data rather than only in
    # theory. If every harvested port has some dry-bulk activity, that's fine too
    # -- skip rather than fabricate a zero-activity fixture.
    for port in load_port_index():
        path = port_csv_path(port.label)
        if not path.exists():
            continue
        try:
            mean_parcel_size(port.label, path)
        except NoActivityError:
            return
    pytest.skip("every harvested port on disk has nonzero dry-bulk activity")


def test_build_fleet_class_mix_covers_real_ports_and_reports_gaps():
    mix, skipped = build_fleet_class_mix()
    assert mix.height > 50
    assert set(mix.columns) >= {
        "label", "basin", "role", "mean_parcel_t",
        "weight_HANDYSIZE", "weight_SUPRAMAX", "weight_PANAMAX", "weight_CAPESIZE",
    }
    # The 15 P1 candidates resolved-but-never-pulled should show up as skipped with
    # a reason, not vanish silently.
    if skipped.height > 0:
        assert "reason" in skipped.columns

    row_sums = mix.select(
        (
            mix["weight_HANDYSIZE"] + mix["weight_SUPRAMAX"]
            + mix["weight_PANAMAX"] + mix["weight_CAPESIZE"]
        ).alias("total")
    )
    for v in row_sums["total"]:
        assert math.isclose(v, 1.0, abs_tol=1e-9)
