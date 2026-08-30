"""Tests for opt.risk — PS deliverable (d), replacing the hardcoded ReviewTrigger."""
from datetime import date
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from opt.network import PortEnum
from opt.risk import (
    CYCLONE_THRESHOLD_MULTIPLE,
    RiskAlert,
    _zscore_of_last,
    assess_risk,
    chokepoint_disruption_alert,
    cyclone_season_alert,
    port_congestion_alert,
    rate_regime_alert,
)
from opt.types import VesselClass

# ---------------------------------------------------------------------------
# Pure math: deterministic, synthetic
# ---------------------------------------------------------------------------

class TestZScoreOfLast:
    def test_exact_zscore_on_a_known_series(self):
        # baseline [1,2,3,4,5], mean=3, std=sqrt(2.5)~=1.5811, last=10
        values = np.array([1.0, 2.0, 3.0, 4.0, 5.0, 10.0])
        result = _zscore_of_last(values, window=5)
        assert result is not None
        last, z = result
        assert last == 10.0
        assert z == pytest.approx((10.0 - 3.0) / np.std([1, 2, 3, 4, 5], ddof=1))

    def test_insufficient_history_returns_none(self):
        assert _zscore_of_last(np.array([1.0, 2.0, 3.0]), window=10) is None

    def test_zero_variance_baseline_returns_none(self):
        # A constant baseline has no meaningful z-score -- division by zero
        # avoided, not silently producing inf.
        values = np.array([5.0] * 10 + [5.0])
        assert _zscore_of_last(values, window=10) is None


# ---------------------------------------------------------------------------
# Real data: src/data/master_long.parquet, real PortWatch CSVs
# ---------------------------------------------------------------------------

class TestRateRegimeAlertRealData:
    def test_runs_clean_for_every_class_no_warnings_no_crash(self):
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            for cls in VesselClass:
                result = rate_regime_alert(cls)
                assert result is None or isinstance(result, RiskAlert)

    def test_survives_being_asked_about_the_known_bad_data_window(self):
        # BC_INDEX carries 44 real rows with impossible negative values around
        # 2020-01-31 to 2020-02-06 (found while building this module -- see
        # opt.risk's docstring). Asking for a risk assessment as-of a date
        # inside that window must not crash or warn, since a real caller could
        # legitimately re-run a historical scenario from around then.
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            result = rate_regime_alert(VesselClass.CAPESIZE, as_of=date(2020, 2, 3))
        assert result is None or isinstance(result, RiskAlert)

    def test_alert_when_present_has_a_valid_severity_and_matching_direction_wording(self):
        result = rate_regime_alert(VesselClass.PANAMAX)
        if result is None:
            pytest.skip("no real rate anomaly present as of the latest data -- nothing to check")
        assert result.severity in ("warning", "critical")
        assert ("jumped" in result.message) or ("dropped" in result.message)
        assert abs(result.metric_value) >= result.threshold


class TestPortCongestionAlertRealData:
    def test_runs_clean_on_a_real_busy_port(self):
        result = port_congestion_alert("Port_Hedland_AU")
        assert result is None or isinstance(result, RiskAlert)

    def test_unknown_port_label_returns_none_not_an_error(self):
        assert port_congestion_alert("Not_A_Real_Port_XYZ") is None


class TestChokepointDisruptionAlertRealData:
    def test_malacca_strait_real_transit_drop_as_of_a_pinned_date(self):
        # Real finding from real data: as of 2026-08-16 (the latest date this
        # chokepoint's real data covers when this test was written), Malacca
        # Strait dry-bulk transits show a real z~-2.2 drop against their own
        # 60-day baseline. Pinned to a fixed as_of so this doesn't silently
        # start failing as more data is appended later -- it is a regression
        # check on a specific, already-observed real anomaly, not a claim that
        # Malacca is always anomalous.
        result = chokepoint_disruption_alert("chokepoint5", as_of=date(2026, 8, 16))
        assert result is not None
        assert result.category == "chokepoint_disruption"
        assert result.subject == "Malacca Strait"
        assert result.metric_value < 0  # a drop, not a rise

    def test_unknown_chokepoint_id_returns_none_not_an_error(self):
        assert chokepoint_disruption_alert("chokepoint_does_not_exist") is None

    def test_a_calm_chokepoint_can_return_none(self):
        # Not every chokepoint is disrupted at once -- Suez, checked the same
        # day the Malacca anomaly above was found, showed no alert. Confirms
        # the detector isn't just always firing.
        result = chokepoint_disruption_alert("chokepoint1", as_of=date(2026, 8, 16))
        assert result is None


# ---------------------------------------------------------------------------
# Cyclone climatology -- a tiny synthetic parquet fixture, never the real
# 150-year build. Nine rows: five 0.05 "filler" weeks in basins/weeks the
# tests below never query, plus four BAY_OF_BENGAL weeks at distinct rates
# chosen to land cleanly (with margin, away from any boundary) in each band
# of the severity ladder once CYCLONE_THRESHOLD_MULTIPLE (1.5) is applied to
# this fixture's own median (0.05):
#   threshold = 1.5 * 0.05 = 0.075 (2x = 0.15, 3x = 0.225)
#   week 30 -> 0.06  (< 0.075)            -> no alert
#   week 15 -> 0.10  ([0.075, 0.15))      -> info
#   week 20 -> 0.18  ([0.15, 0.225))      -> warning
#   week 41 -> 0.30  (>= 0.225)           -> critical
_CLIMATOLOGY_FIXTURE_ROWS: list[tuple[str, int, float]] = [
    ("ARABIAN_SEA", 5, 0.05),
    ("ARABIAN_SEA", 6, 0.05),
    ("MOZAMBIQUE_CHANNEL", 6, 0.05),
    ("MOZAMBIQUE_CHANNEL", 7, 0.05),
    ("NE_AUSTRALIA", 7, 0.05),
    ("BAY_OF_BENGAL", 30, 0.06),
    ("BAY_OF_BENGAL", 15, 0.10),
    ("BAY_OF_BENGAL", 20, 0.18),
    ("BAY_OF_BENGAL", 41, 0.30),
]


def _write_climatology_fixture(path: Path) -> None:
    df = pl.DataFrame(_CLIMATOLOGY_FIXTURE_ROWS, schema=["basin", "iso_week", "strike_rate"], orient="row")
    df.write_parquet(path)


def _date_for_iso_week(week: int, year: int = 2026) -> date:
    return date.fromisocalendar(year, week, 3)


@pytest.fixture
def climatology_fixture(tmp_path: Path) -> Path:
    path = tmp_path / "cyclone_climatology.parquet"
    _write_climatology_fixture(path)
    return path


class TestCycloneSeasonAlert:
    def test_high_strike_rate_week_fires_with_the_real_triggering_basin_and_metric(self, climatology_fixture: Path):
        result = cyclone_season_alert(
            date(2026, 1, 1),
            ports=[PortEnum.PARADIP],
            laycan_start=_date_for_iso_week(41),
            laycan_end=_date_for_iso_week(41),
            climatology_path=climatology_fixture,
        )
        assert result is not None
        assert result.category == "cyclone_season"
        assert result.subject == "BAY_OF_BENGAL"
        assert result.metric_value == pytest.approx(0.30)
        assert result.severity == "critical"

    def test_port_in_no_cyclone_basin_fires_nothing(self, climatology_fixture: Path):
        # Richards Bay, South Africa is a real port that resolves to zero
        # named basins (data_builders.build_cyclone_climatology's own test
        # suite already confirms this mapping) -- same high-strike week as
        # the test above, but nothing to alert on for this port.
        result = cyclone_season_alert(
            date(2026, 1, 1),
            ports=[PortEnum.RICHARDS_BAY],
            laycan_start=_date_for_iso_week(41),
            laycan_end=_date_for_iso_week(41),
            climatology_path=climatology_fixture,
        )
        assert result is None

    def test_low_season_week_fires_nothing(self, climatology_fixture: Path):
        result = cyclone_season_alert(
            date(2026, 1, 1),
            ports=[PortEnum.PARADIP],
            laycan_start=_date_for_iso_week(30),
            laycan_end=_date_for_iso_week(30),
            climatology_path=climatology_fixture,
        )
        assert result is None

    def test_no_ports_supplied_fires_nothing_not_a_fabricated_alert(self, climatology_fixture: Path):
        # The old calendar-only signature fired for every caller regardless
        # of location; the whole point of this change is that a real port is
        # required to name a real basin. No ports -> nothing to check.
        assert cyclone_season_alert(date(2026, 10, 15), climatology_path=climatology_fixture) is None

    def test_missing_climatology_file_returns_none_not_an_error(self, tmp_path: Path):
        result = cyclone_season_alert(
            date(2026, 10, 1),
            ports=[PortEnum.PARADIP],
            laycan_start=date(2026, 10, 1),
            laycan_end=date(2026, 10, 5),
            climatology_path=tmp_path / "does_not_exist.parquet",
        )
        assert result is None

    def test_severity_ladder_info_warning_critical_are_all_reachable(self, climatology_fixture: Path):
        def severity_for(week: int) -> str | None:
            r = cyclone_season_alert(
                date(2026, 1, 1),
                ports=[PortEnum.PARADIP],
                laycan_start=_date_for_iso_week(week),
                laycan_end=_date_for_iso_week(week),
                climatology_path=climatology_fixture,
            )
            return r.severity if r is not None else None

        assert severity_for(30) is None  # below the base threshold entirely
        assert severity_for(15) == "info"
        assert severity_for(20) == "warning"
        assert severity_for(41) == "critical"

    def test_threshold_is_a_real_multiple_of_the_fixtures_own_median(self, climatology_fixture: Path):
        result = cyclone_season_alert(
            date(2026, 1, 1),
            ports=[PortEnum.PARADIP],
            laycan_start=_date_for_iso_week(41),
            laycan_end=_date_for_iso_week(41),
            climatology_path=climatology_fixture,
        )
        assert result is not None
        median = sorted(r[2] for r in _CLIMATOLOGY_FIXTURE_ROWS)[len(_CLIMATOLOGY_FIXTURE_ROWS) // 2]
        assert result.threshold == pytest.approx(median * CYCLONE_THRESHOLD_MULTIPLE)


class TestAssessRiskWithCycloneContext:
    def test_assess_risk_still_returns_a_valid_assessment_called_the_old_way(self):
        # Exactly how every pre-existing caller in this file already calls
        # it -- no ports/laycan kwargs at all.
        result = assess_risk(VesselClass.HANDYSIZE, ["Not_A_Real_Port"], ["chokepoint_none"], date(2026, 8, 1))
        assert result.alerts == ()

    def test_assess_risk_forwards_ports_and_laycan_into_a_real_cyclone_alert(
        self, climatology_fixture: Path, monkeypatch: pytest.MonkeyPatch
    ):
        # assess_risk doesn't expose climatology_path itself (only
        # cyclone_season_alert does, for direct testability) -- point the
        # module-level default at the fixture so this exercises assess_risk's
        # own ports/laycan forwarding, not a second copy of
        # cyclone_season_alert's internal logic.
        import opt.risk as risk_module

        monkeypatch.setattr(risk_module, "CYCLONE_CLIMATOLOGY_PATH", climatology_fixture)
        result = assess_risk(
            VesselClass.HANDYSIZE,
            [],
            [],
            date(2026, 1, 1),
            ports=[PortEnum.PARADIP],
            laycan_start=_date_for_iso_week(41),
            laycan_end=_date_for_iso_week(41),
        )
        cyclone_alerts = [a for a in result.alerts if a.category == "cyclone_season"]
        assert len(cyclone_alerts) == 1
        assert cyclone_alerts[0].subject == "BAY_OF_BENGAL"


class TestAssessRiskAndReviewTrigger:
    def test_no_alerts_gives_monthly_schedule_and_an_honest_default_condition(self):
        # A calm month (August), a nonexistent port/chokepoint (no data -> no
        # alert), pinned so this doesn't depend on ambient market conditions.
        result = assess_risk(VesselClass.HANDYSIZE, ["Not_A_Real_Port"], ["chokepoint_none"], date(2026, 8, 1))
        assert result.alerts == ()
        trigger = result.to_review_trigger()
        assert trigger.schedule == "MONTHLY"
        assert "No active risk signals" in trigger.conditions[0]

    def test_a_real_known_disruption_escalates_the_schedule_and_appears_in_conditions(self):
        result = assess_risk(VesselClass.SUPRAMAX, [], ["chokepoint5"], date(2026, 8, 16))
        assert len(result.alerts) == 1
        trigger = result.to_review_trigger()
        assert trigger.schedule in ("WEEKLY", "DAILY")
        assert any("Malacca" in c for c in trigger.conditions)

    def test_critical_severity_forces_daily_schedule(self):
        # Construct the escalation path directly (a real critical-severity real
        # market event is not something to wait around for in a test) --
        # exercises to_review_trigger's own escalation rule against a real
        # RiskAlert value, not a mock.
        from opt.risk import RiskAssessment

        critical = RiskAlert(
            category="rate_regime", severity="critical", message="test critical event",
            metric_value=4.0, threshold=2.0, subject="Capesize",
        )
        assessment = RiskAssessment(as_of=date(2026, 8, 1), alerts=(critical,))
        assert assessment.to_review_trigger().schedule == "DAILY"
