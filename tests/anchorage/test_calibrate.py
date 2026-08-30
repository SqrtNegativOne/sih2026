"""Tests for anchorage.calibrate -- real join/statistics logic, exercised
against synthetic-but-real-shaped PortWatch CSVs and synthetic
AnchorageCensus records (no real imagery, no real satellite data -- none
exists in this environment yet, see anchorage.detect's own module
docstring)."""
from __future__ import annotations

import csv
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from anchorage.calibrate import (
    MIN_N_FOR_CORRELATION,
    PortCalibrationResult,
    calibrate_port,
    write_calibration_csv,
    write_calibration_doc,
)
from anchorage.detect import AnchorageCensus


def _census(port: str, d: date, vessel_count: int) -> AnchorageCensus:
    return AnchorageCensus(
        port=port,
        scene_id=f"{port}-{d.isoformat()}",
        acquired_at=datetime(d.year, d.month, d.day, 12, 0, tzinfo=UTC),
        vessel_count=vessel_count,
        detections=(),
        mean_sea_state_proxy=0.1,
        confidence="high",
        provenance="MODEL_DERIVED",
    )


def _write_portwatch_csv(path: Path, counts_by_date: dict[date, float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "portcalls_dry_bulk"])
        for d, count in sorted(counts_by_date.items()):
            writer.writerow([d.isoformat(), count])


class TestCalibratePortZeroObservations:
    def test_no_censuses_at_all_reports_n_zero_with_a_plain_reason(self, tmp_path: Path):
        pw_path = tmp_path / "Paradip_daily_portcalls.csv"
        _write_portwatch_csv(pw_path, {date(2026, 1, 1): 5.0})
        result = calibrate_port("PARADIP", [], portwatch_csv_path=pw_path)
        assert result.n == 0
        assert result.spearman_r is None
        assert result.pearson_r is None
        assert result.mean_abs_diff is None
        assert "zero real AnchorageCensus records" in result.finding

    def test_no_portwatch_file_reports_n_zero_with_a_different_plain_reason(self, tmp_path: Path):
        result = calibrate_port(
            "PARADIP", [_census("PARADIP", date(2026, 1, 1), 3)],
            portwatch_csv_path=tmp_path / "does_not_exist.csv",
        )
        assert result.n == 0
        assert "no real PortWatch" in result.finding

    def test_censuses_exist_but_no_dates_overlap_reports_n_zero(self, tmp_path: Path):
        pw_path = tmp_path / "pw.csv"
        _write_portwatch_csv(pw_path, {date(2026, 1, 1): 5.0})
        result = calibrate_port(
            "PARADIP", [_census("PARADIP", date(2027, 1, 1), 3)], portwatch_csv_path=pw_path
        )
        assert result.n == 0
        assert "matched" in result.finding

    def test_an_unknown_port_reports_n_zero_without_touching_any_file(self, tmp_path: Path):
        result = calibrate_port("NOT_A_REAL_PORT", [], portwatch_csv_path=tmp_path / "irrelevant.csv")
        assert result.n == 0
        assert "No real PortWatch tonnage label" in result.finding


class TestCalibratePortBelowMinN:
    def test_never_reports_a_correlation_below_min_n(self, tmp_path: Path):
        """The named acceptance case: do not report r on four points."""
        pw_path = tmp_path / "pw.csv"
        dates = [date(2026, 1, d) for d in range(1, 5)]  # 4 real paired points
        _write_portwatch_csv(pw_path, {d: 10.0 for d in dates})
        censuses = [_census("PARADIP", d, 3) for d in dates]

        assert len(dates) < MIN_N_FOR_CORRELATION
        result = calibrate_port("PARADIP", censuses, portwatch_csv_path=pw_path)

        assert result.n == 4
        assert result.spearman_r is None
        assert result.pearson_r is None
        assert result.mean_abs_diff is not None  # a plain descriptive figure, not gated by MIN_N
        assert "below MIN_N_FOR_CORRELATION" in result.finding

    def test_mean_abs_diff_is_computed_correctly_below_min_n(self, tmp_path: Path):
        pw_path = tmp_path / "pw.csv"
        dates = [date(2026, 1, 1), date(2026, 1, 2)]
        _write_portwatch_csv(pw_path, {dates[0]: 10.0, dates[1]: 20.0})
        censuses = [_census("PARADIP", dates[0], 8), _census("PARADIP", dates[1], 15)]
        result = calibrate_port("PARADIP", censuses, portwatch_csv_path=pw_path)
        # |8-10| = 2, |15-20| = 5 -> mean 3.5
        assert result.mean_abs_diff == pytest.approx(3.5)


class TestCalibratePortAtOrAboveMinN:
    def test_a_real_perfect_correlation_is_reported_correctly(self, tmp_path: Path):
        pw_path = tmp_path / "pw.csv"
        dates = [date(2026, 1, d) for d in range(1, MIN_N_FOR_CORRELATION + 1)]
        counts_by_date = {d: float(10 + i) for i, d in enumerate(dates)}
        _write_portwatch_csv(pw_path, counts_by_date)
        # Satellite count is an exact linear function of the real PortWatch count.
        censuses = [_census("PARADIP", d, int(counts_by_date[d] - 10)) for d in dates]

        result = calibrate_port("PARADIP", censuses, portwatch_csv_path=pw_path)
        assert result.n == MIN_N_FOR_CORRELATION
        assert result.spearman_r == pytest.approx(1.0)
        assert result.pearson_r == pytest.approx(1.0, abs=1e-6)
        assert result.date_range == (dates[0], dates[-1])

    def test_a_real_weak_or_absent_correlation_is_reported_honestly_not_hidden(self, tmp_path: Path):
        """The task's own explicit case: a weak result is a legitimate,
        publishable finding, not something to suppress."""
        pw_path = tmp_path / "pw.csv"
        dates = [date(2026, 1, d) for d in range(1, MIN_N_FOR_CORRELATION + 1)]
        # PortWatch count is monotonically increasing; satellite count
        # alternates high/low with no real relationship to it.
        counts_by_date = {d: float(10 + i) for i, d in enumerate(dates)}
        _write_portwatch_csv(pw_path, counts_by_date)
        alternating = [5, 1, 5, 1, 5, 1, 5, 1, 5, 1]
        censuses = [_census("PARADIP", d, alternating[i]) for i, d in enumerate(dates)]

        result = calibrate_port("PARADIP", censuses, portwatch_csv_path=pw_path)
        assert result.n == MIN_N_FOR_CORRELATION
        assert result.spearman_r is not None
        assert abs(result.spearman_r) < 0.3  # genuinely weak, not fabricated as strong
        assert "Spearman r=" in result.finding

    def test_only_paired_dates_count_toward_n_unpaired_censuses_are_excluded(self, tmp_path: Path):
        pw_path = tmp_path / "pw.csv"
        paired_dates = [date(2026, 1, d) for d in range(1, MIN_N_FOR_CORRELATION + 1)]
        # Non-constant values on both sides -- a real correlation is
        # computable and not a scipy ConstantInputWarning/NaN case, which
        # would obscure what this test is actually checking (n, not r).
        _write_portwatch_csv(pw_path, {d: float(10 + i) for i, d in enumerate(paired_dates)})
        censuses = [_census("PARADIP", d, 3 + i) for i, d in enumerate(paired_dates)]
        censuses.append(_census("PARADIP", date(2030, 1, 1), 99))  # no matching PortWatch date

        result = calibrate_port("PARADIP", censuses, portwatch_csv_path=pw_path)
        assert result.n == MIN_N_FOR_CORRELATION  # the unpaired one does not inflate n

    def test_only_the_requested_ports_own_censuses_are_used(self, tmp_path: Path):
        pw_path = tmp_path / "pw.csv"
        dates = [date(2026, 1, d) for d in range(1, MIN_N_FOR_CORRELATION + 1)]
        _write_portwatch_csv(pw_path, {d: float(10 + i) for i, d in enumerate(dates)})
        censuses = [_census("PARADIP", d, 3 + i) for i, d in enumerate(dates)]
        censuses += [_census("VISAKHAPATNAM", d, 99) for d in dates]  # a different port

        result = calibrate_port("PARADIP", censuses, portwatch_csv_path=pw_path)
        assert result.n == MIN_N_FOR_CORRELATION


class TestWriteCalibrationOutputs:
    def test_csv_has_the_documented_columns_and_one_row_per_port(self, tmp_path: Path):
        results = [
            PortCalibrationResult(
                port="PARADIP", n=0, date_range=None, spearman_r=None, pearson_r=None,
                mean_abs_diff=None, finding="n=0: no data.",
            ),
        ]
        out = tmp_path / "anchorage_calibration.csv"
        write_calibration_csv(results, path=out)
        rows = list(csv.DictReader(out.open()))
        assert len(rows) == 1
        assert set(rows[0]) == {
            "port", "n", "date_from", "date_to", "spearman_r", "pearson_r", "mean_abs_diff", "finding",
        }
        assert rows[0]["port"] == "PARADIP"
        assert rows[0]["n"] == "0"
        assert rows[0]["spearman_r"] == ""  # None renders as blank, never a fabricated 0.0

    def test_doc_summary_states_min_n_and_every_port_result(self, tmp_path: Path):
        results = [
            PortCalibrationResult(
                port="PARADIP", n=0, date_range=None, spearman_r=None, pearson_r=None,
                mean_abs_diff=None, finding="n=0: no data.",
            ),
        ]
        out = tmp_path / "calibration.md"
        write_calibration_doc(results, path=out)
        text = out.read_text()
        assert "PARADIP" in text
        assert str(MIN_N_FOR_CORRELATION) in text
        assert "n=0: no data." in text
