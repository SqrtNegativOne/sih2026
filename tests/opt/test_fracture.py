"""The Fracture Index -- composition, renormalisation, and the band cap.

Every fixture here is synthetic and written into ``tmp_path``; nothing in
this file reads the real PortWatch/GDELT/Panama artifacts, so these tests
stay deterministic as the real harvests move on.
"""
from __future__ import annotations

import csv
import statistics
from datetime import date
from pathlib import Path

import pytest

from opt.fracture import (
    BAND_CAP_WHEN_INCOMPLETE,
    BAND_THRESHOLDS,
    FRACTURE_WEIGHTS,
    Z_SATURATION,
    chokepoint_fracture,
    jwc_listed_chokepoints,
    route_fracture,
)
from opt.network import PortEnum
from opt.risk import CHOKEPOINT_WINDOW_DAYS

# chokepoint5 (Malacca) is deliberately NOT inside any JWC listed area, so it
# isolates the two measured signals. chokepoint6 (Hormuz) IS listed.
UNLISTED = "chokepoint5"
LISTED = "chokepoint6"
PANAMA = "chokepoint2"

AS_OF = date(2026, 8, 20)


def _write_transit(root: Path, chokepoint_id: str, target_z: float) -> Path:
    """A real-shaped transit CSV whose last row lands at exactly ``target_z``
    against its own baseline. The last value is computed from the baseline's
    real mean/stdev rather than hardcoded, so the fixture cannot drift out of
    step with ``opt.risk._zscore_of_last``'s own arithmetic."""
    root.mkdir(parents=True, exist_ok=True)
    baseline = [100.0 + (2.0 if i % 2 else 0.0) for i in range(CHOKEPOINT_WINDOW_DAYS)]
    last = statistics.mean(baseline) + target_z * statistics.stdev(baseline)
    path = root / f"{chokepoint_id}_daily_transits.csv"
    start = date(2026, 5, 1).toordinal()
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "n_dry_bulk"])
        writer.writerows(
            (date.fromordinal(start + i).isoformat(), value)
            for i, value in enumerate([*baseline, last])
        )
    return path


def _write_gdelt(path: Path, chokepoint_id: str, target_z: float) -> Path:
    """A real-shaped weekly GDELT CSV whose last week lands at ``target_z``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    baseline = [10.0 + (2.0 if i % 2 else 0.0) for i in range(12)]
    last = statistics.mean(baseline) + target_z * statistics.stdev(baseline)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["chokepoint_id", "iso_year", "iso_week", "event_count", "avg_tone",
             "n_source_articles", "retrieved_at"]
        )
        for i, value in enumerate([*baseline, last]):
            writer.writerow([chokepoint_id, 2026, 20 + i, value, -3.5, 40, "2026-08-20T00:00:00+00:00"])
    return path


def _write_advisory(path: Path, *, restricted: bool, last_reviewed: str = "2026-12-31") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["chokepoint_id", "effective_from", "effective_to", "max_draft_ft",
             "normal_max_draft_ft", "advisory_ref", "source_url", "last_reviewed", "note"]
        )
        writer.writerow(
            [PANAMA, "2026-01-01", "", 44.0 if restricted else 50.0, 50.0,
             "test", "https://example.invalid/", last_reviewed, "synthetic fixture"]
        )
    return path


def _fracture(tmp_path: Path, chokepoint_id: str, **kwargs):
    """Run a fracture against tmp_path-only fixtures. Any path not written by
    a helper points at a non-existent directory, so that input is genuinely
    missing rather than falling through to the real artifact on disk."""
    return chokepoint_fracture(
        chokepoint_id,
        AS_OF,
        chokepoint_dir=kwargs.get("chokepoint_dir", tmp_path / "no_transit"),
        gdelt_path=kwargs.get("gdelt_path", tmp_path / "no_gdelt.csv"),
        advisories_path=kwargs.get("advisories_path", tmp_path / "no_advisories.csv"),
    )


class TestWeightsAndThresholds:
    def test_weights_sum_to_one(self):
        assert sum(FRACTURE_WEIGHTS.values()) == pytest.approx(1.0)

    def test_band_thresholds_are_descending_and_documented(self):
        values = [t for t, _ in BAND_THRESHOLDS]
        assert values == sorted(values, reverse=True)


class TestComposition:
    def test_all_three_available_signals_renormalise_over_their_own_weights(self, tmp_path: Path):
        """transit at full risk, conflict at half, not JWC-listed. The index
        must be the weighted mean over the AVAILABLE weights only (0.9), not
        over all four (1.0) -- zero-filling the absent draft input would
        wrongly dilute this to 63.0."""
        transit_dir = tmp_path / "pw"
        _write_transit(transit_dir, UNLISTED, target_z=-Z_SATURATION)
        gdelt = _write_gdelt(tmp_path / "g.csv", UNLISTED, target_z=Z_SATURATION / 2)

        result = _fracture(tmp_path, UNLISTED, chokepoint_dir=transit_dir, gdelt_path=gdelt)

        assert result.inputs_available == ("transit_z", "conflict_z", "jwc_listed")
        expected = (0.40 * 100.0 + 0.30 * 50.0 + 0.20 * 0.0) / 0.90
        assert result.index == pytest.approx(expected, abs=1e-6)
        assert result.index == pytest.approx(61.11, abs=0.01)

    def test_one_signal_only_renormalises_and_is_capped_at_watch(self, tmp_path: Path):
        """With only transit available (plus the always-available listing
        flag), the renormalised index is high -- but the band must be capped,
        because one measured signal cannot justify 'elevated'."""
        transit_dir = tmp_path / "pw"
        _write_transit(transit_dir, UNLISTED, target_z=-Z_SATURATION)

        result = _fracture(tmp_path, UNLISTED, chokepoint_dir=transit_dir)

        assert result.inputs_available == ("transit_z", "jwc_listed")
        expected = (0.40 * 100.0 + 0.20 * 0.0) / 0.60
        assert result.index == pytest.approx(expected, abs=1e-6)
        assert result.index > 50.0, "uncapped this index would be 'elevated'"
        assert result.band == BAND_CAP_WHEN_INCOMPLETE == "watch"
        assert "capped" in result.explanation

    def test_a_traffic_spike_is_not_a_risk_signal(self, tmp_path: Path):
        """A rise in transits scores zero, never a negative contribution --
        the same convention opt.risk.chokepoint_disruption_alert uses."""
        transit_dir = tmp_path / "pw"
        _write_transit(transit_dir, UNLISTED, target_z=+Z_SATURATION)
        gdelt = _write_gdelt(tmp_path / "g.csv", UNLISTED, target_z=-Z_SATURATION)

        result = _fracture(tmp_path, UNLISTED, chokepoint_dir=transit_dir, gdelt_path=gdelt)

        assert result.index == pytest.approx(0.0)
        assert result.band == "calm"

    def test_a_jwc_listing_alone_cannot_exceed_the_cap(self, tmp_path: Path):
        """Hormuz is inside a listed area. With no measured signal at all its
        index is 100 on that single input -- and must still band as 'watch'."""
        result = _fracture(tmp_path, LISTED)

        assert result.jwc_listed is True
        assert result.inputs_available == ("jwc_listed",)
        assert result.index == pytest.approx(100.0)
        assert result.band == "watch"

    def test_the_draft_input_joins_the_weighting_when_an_advisory_covers_the_date(self, tmp_path: Path):
        transit_dir = tmp_path / "pw"
        _write_transit(transit_dir, PANAMA, target_z=-Z_SATURATION)
        gdelt = _write_gdelt(tmp_path / "g.csv", PANAMA, target_z=Z_SATURATION)
        advisories = _write_advisory(tmp_path / "adv.csv", restricted=True)

        result = _fracture(
            tmp_path, PANAMA, chokepoint_dir=transit_dir, gdelt_path=gdelt, advisories_path=advisories
        )

        assert result.draft_restricted is True
        assert set(result.inputs_available) == {"transit_z", "conflict_z", "jwc_listed", "draft_restricted"}
        expected = (0.40 * 100.0 + 0.30 * 100.0 + 0.20 * 0.0 + 0.10 * 100.0) / 1.0
        assert result.index == pytest.approx(expected, abs=1e-6)


class TestMissingData:
    def test_missing_gdelt_file_entirely_still_computes_from_transit_alone(self, tmp_path: Path):
        """The named acceptance case: no GDELT harvest on disk at all."""
        transit_dir = tmp_path / "pw"
        _write_transit(transit_dir, UNLISTED, target_z=-1.5)

        result = _fracture(tmp_path, UNLISTED, chokepoint_dir=transit_dir)

        assert result.conflict_z is None
        assert result.transit_z is not None
        assert "conflict_z" not in result.inputs_available
        assert "transit_z" in result.inputs_available
        assert result.band == "calm" or result.band == BAND_CAP_WHEN_INCOMPLETE
        assert "no conflict signal on disk" in result.explanation

    def test_nothing_on_disk_is_not_reported_as_confirmed_calm(self, tmp_path: Path):
        result = _fracture(tmp_path, UNLISTED)
        assert result.transit_z is None
        assert result.conflict_z is None
        assert result.inputs_available == ("jwc_listed",)
        assert "no transit signal on disk" in result.explanation
        assert "no conflict signal on disk" in result.explanation

    def test_a_stale_advisory_table_drops_the_input_rather_than_asserting_false(self, tmp_path: Path):
        """as_of after last_reviewed must mean 'unknown', not a confident
        'no restriction in force'."""
        advisories = _write_advisory(tmp_path / "adv.csv", restricted=True, last_reviewed="2024-12-31")
        result = _fracture(tmp_path, PANAMA, advisories_path=advisories)
        assert "draft_restricted" not in result.inputs_available
        assert result.draft_restricted is False  # the bool field, but unused in the index

    def test_a_current_advisory_with_no_restriction_is_a_real_false_not_a_gap(self, tmp_path: Path):
        advisories = _write_advisory(tmp_path / "adv.csv", restricted=False)
        result = _fracture(tmp_path, PANAMA, advisories_path=advisories)
        assert "draft_restricted" in result.inputs_available
        assert result.draft_restricted is False

    def test_an_unknown_chokepoint_id_raises_rather_than_scoring_nothing(self, tmp_path: Path):
        with pytest.raises(KeyError, match="Unknown chokepoint id"):
            _fracture(tmp_path, "chokepoint999")


class TestBandsAreAllReachable:
    @pytest.mark.parametrize(
        ("transit_z", "conflict_z", "expected_band"),
        [
            (+1.0, -1.0, "calm"),
            (-Z_SATURATION, 0.0, "watch"),
            (-Z_SATURATION, Z_SATURATION / 2, "elevated"),
            (-Z_SATURATION, Z_SATURATION, "critical"),
        ],
    )
    def test_each_band_is_reachable_with_both_measured_signals_present(
        self, tmp_path: Path, transit_z: float, conflict_z: float, expected_band: str
    ):
        transit_dir = tmp_path / "pw"
        _write_transit(transit_dir, UNLISTED, target_z=transit_z)
        gdelt = _write_gdelt(tmp_path / "g.csv", UNLISTED, target_z=conflict_z)

        result = _fracture(tmp_path, UNLISTED, chokepoint_dir=transit_dir, gdelt_path=gdelt)

        assert result.inputs_available == ("transit_z", "conflict_z", "jwc_listed")
        assert result.band == expected_band


class TestRouteFracture:
    def test_a_suez_route_returns_a_fracture_per_chokepoint_it_crosses(self, tmp_path: Path):
        results = route_fracture(
            PortEnum.HAMPTON_ROADS, PortEnum.VIZAG, AS_OF,
            chokepoint_dir=tmp_path / "none", gdelt_path=tmp_path / "none.csv",
            advisories_path=tmp_path / "none.csv",
        )
        ids = [r.chokepoint_id for r in results]
        assert "chokepoint1" in ids  # Suez
        assert "chokepoint4" in ids  # Bab el-Mandeb
        assert all(0.0 <= r.index <= 100.0 for r in results)

    def test_a_route_crossing_no_monitored_chokepoint_returns_empty(self, tmp_path: Path):
        assert route_fracture(
            PortEnum.VIZAG, PortEnum.PARADIP, AS_OF,
            chokepoint_dir=tmp_path / "none", gdelt_path=tmp_path / "none.csv",
            advisories_path=tmp_path / "none.csv",
        ) == ()


class TestJwcListingDerivation:
    def test_the_listing_set_is_derived_from_the_two_real_tables(self):
        """Bab el-Mandeb, Hormuz and Kerch fall inside real listed-area
        envelopes; the Bosporus deliberately does not (the circular's Black
        Sea polygon stops short of Turkish waters)."""
        listed = jwc_listed_chokepoints()
        assert "chokepoint4" in listed   # Bab el-Mandeb
        assert "chokepoint6" in listed   # Hormuz
        assert "chokepoint28" in listed  # Kerch
        assert "chokepoint3" not in listed  # Bosporus
        assert "chokepoint5" not in listed  # Malacca
