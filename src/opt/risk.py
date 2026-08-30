"""Risk monitoring and early-warning trigger engine (Sub-problem 4): PS
deliverable (d), previously a hardcoded string.

``opt.api`` used to always return ``ReviewTrigger(schedule="WEEKLY",
conditions=["BDI jumps >5%"])`` -- literal, regardless of what was actually
happening in the market. This module computes real signals from real data and
only raises an alert when a threshold is actually crossed:

- **Rate regime/anomaly**: z-score of today's log-return on the real Baltic
  index (``src/data/master_long.parquet``) against its own trailing 90-day
  return distribution. A real statistical anomaly test, not a fixed "5%" rule
  that means something different in a calm market than a volatile one.
- **Port congestion anomaly**: z-score of a port's daily dry-bulk call count
  (real IMF PortWatch data, reused from ``tonnage.basins``) against its own
  trailing baseline.
- **Chokepoint disruption**: z-score of a chokepoint's daily dry-bulk transit
  count (real PortWatch chokepoint data), flagging *drops* specifically -- a
  sudden fall in transits is the disruption signal (a blockage, a closure), a
  spike is not.
- **Cyclone season**: a real per-basin, per-ISO-week strike climatology, built
  from NOAA's IBTrACS best-track archive (``data_builders.build_cyclone_climatology``,
  ``src/data/cyclone_climatology.parquet`` -- see that module's docstring for
  the basin definitions and the 1980-onward satellite-era cutoff). Keyed off
  the actual ports on the quote: each port resolves to zero or more named
  basins (``data_builders.build_cyclone_climatology.basins_for_port``), the
  laycan window resolves to the ISO week(s) it spans, and the alert fires when
  the highest real strike_rate among those (basin, week) pairs clears a
  threshold set from the data itself (see ``CYCLONE_THRESHOLD_MULTIPLE``
  below) -- not a fixed October-December calendar rule that fired the same way
  regardless of which port the cargo was actually moving through. This is
  STILL a climatology lookup, not a live storm-track forecast: it says "this
  basin/week has historically been active," not "a storm is approaching right
  now." A live 7-day-ahead track overlay (Open-Meteo) is separate, later scope
  (chunk 2.3) and is not implemented here.

Every alert carries the metric value and threshold that triggered it, so
"re-solve now" is always explainable, not just asserted.
"""
from __future__ import annotations

import csv
import logging
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Final, Literal

import numpy as np
import polars as pl

from data_builders.build_cyclone_climatology import basins_for_port
from opt.chokepoints import CHOKEPOINT_NAMES
from opt.network import PortEnum
from opt.types import RiskAlert, RiskAssessment, VesselClass
from tonnage.basins import PortIndexMissingError, port_csv_path

LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

__all__ = [
    "RiskAlert",
    "RiskAssessment",
    "assess_risk",
    "chokepoint_disruption_alert",
    "cyclone_season_alert",
    "port_congestion_alert",
    "rate_regime_alert",
]

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
MASTER_LONG_PATH: Final[Path] = REPO_ROOT / "src" / "data" / "master_long.parquet"
CHOKEPOINT_DIR: Final[Path] = REPO_ROOT / "raw_data" / "portwatch"

CLASS_INDEX_SERIES: Final[dict[VesselClass, str]] = {
    VesselClass.CAPESIZE: "BC_INDEX",
    VesselClass.PANAMAX: "BPI_INDEX",
    VesselClass.SUPRAMAX: "BSI_INDEX",
    VesselClass.HANDYSIZE: "BHSI_INDEX",
}

#: |z| beyond this on a rate return, or a port-call count, counts as anomalous.
#: A conventional two-sided 97.5th-percentile-ish threshold, not fitted to make
#: any particular alert fire.
RATE_Z_THRESHOLD: Final[float] = 2.0
CONGESTION_Z_THRESHOLD: Final[float] = 2.0
#: Chokepoint drops use a looser threshold than congestion upswings -- transit
#: counts are lower-volume, noisier series (single digits to tens/day at most
#: chokepoints) where a stricter threshold fires on routine noise.
CHOKEPOINT_Z_THRESHOLD: Final[float] = 1.75

RATE_WINDOW_DAYS: Final[int] = 90
CONGESTION_WINDOW_DAYS: Final[int] = 60
CHOKEPOINT_WINDOW_DAYS: Final[int] = 60

CYCLONE_CLIMATOLOGY_PATH: Final[Path] = REPO_ROOT / "src" / "data" / "cyclone_climatology.parquet"

#: The base "significant risk" threshold is this multiple of the all-basin
#: median strike_rate across the whole climatology table (computed from the
#: data at call time, not a fitted-to-fire guess). 1.5x was chosen by
#: checking it against the real build (data_builders.build_cyclone_climatology,
#: 136 basin/week rows as of the 2026-08-29 harvest): it puts the more active
#: ~35% of basin/weeks above the base threshold, ~12% above the 2x/"warning"
#: line, and the single most extreme real basin/week on record (Mozambique
#: Channel, ISO week 8, strike_rate 0.596) just clears the 3x/"critical" line
#: -- confirming all three severities are genuinely reachable on real data,
#: not just a theoretical band nothing real ever hits.
CYCLONE_THRESHOLD_MULTIPLE: Final[float] = 1.5

#: Severity ladder, all relative to the same base threshold above (same
#: pattern as RATE_Z_THRESHOLD: one threshold value on the alert, severity
#: escalation is a separate comparison): below 1x -> no alert; [1x, 2x) ->
#: info; [2x, 3x) -> warning; >= 3x -> critical.
_CYCLONE_WARNING_MULTIPLE: Final[float] = 2.0
_CYCLONE_CRITICAL_MULTIPLE: Final[float] = 3.0

Severity = Literal["info", "warning", "critical"]


def _zscore_of_last(values: np.ndarray, window: int) -> tuple[float, float] | None:
    """(last value, z-score of the last value against the preceding window).

    None when there isn't enough history to judge -- an anomaly test needs a
    real baseline, not a guess.
    """
    if len(values) < window + 1:
        return None
    baseline = values[-(window + 1):-1]
    std = float(np.std(baseline, ddof=1))
    if std <= 0:
        return None
    last = float(values[-1])
    z = (last - float(np.mean(baseline))) / std
    return last, z


def rate_regime_alert(vessel_class: VesselClass, as_of: date | None = None) -> RiskAlert | None:
    """Real anomaly test on the Baltic index's own daily log-return series.

    BC_INDEX carries 44 real rows with negative values around late Jan/early Feb
    2020 (a known data-quality artifact -- an index cannot physically be negative;
    the Capesize market did genuinely crash in that window, real crash, impossible
    reading) -- filtered out here rather than fed to log(), which would otherwise
    silently poison every log-return that differences across one of those rows.
    """
    series_id = CLASS_INDEX_SERIES[vessel_class]
    master = pl.read_parquet(MASTER_LONG_PATH)
    sub = master.filter((pl.col("series_id") == series_id) & (pl.col("value") > 0)).sort("date")
    if as_of is not None:
        sub = sub.filter(pl.col("date") <= as_of)
    if sub.height < RATE_WINDOW_DAYS + 2:
        return None

    vals = sub["value"].to_numpy().astype(float)
    log_returns = np.diff(np.log(vals))
    result = _zscore_of_last(log_returns, RATE_WINDOW_DAYS)
    if result is None:
        return None
    last_return, z = result
    if abs(z) < RATE_Z_THRESHOLD:
        return None
    direction = "jumped" if last_return > 0 else "dropped"
    severity: Severity = "critical" if abs(z) >= 3.0 else "warning"
    return RiskAlert(
        category="rate_regime",
        severity=severity,
        message=(
            f"{vessel_class.value} rate {direction} {abs(last_return) * 100:.1f}% "
            f"(z={z:+.1f} vs its own {RATE_WINDOW_DAYS}-day return distribution)"
        ),
        metric_value=z,
        threshold=RATE_Z_THRESHOLD,
        subject=vessel_class.value,
    )


def port_congestion_alert(port_label: str, as_of: date | None = None) -> RiskAlert | None:
    """Real anomaly test on a port's daily dry-bulk call count."""
    try:
        path = port_csv_path(port_label)
    except PortIndexMissingError:
        return None
    if not path.exists():
        return None

    dates: list[str] = []
    counts: list[float] = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            dates.append(row["date"])
            counts.append(float(row["portcalls_dry_bulk"]))
    # F-09 fix: the source CSV is not stored in date order (Paradip alone
    # has 755 out-of-order steps across 2,782 rows) -- taking "the last row"
    # as today without sorting first tested a day from October 2025 as
    # though it were the real as_of date, against a "60-day baseline" drawn
    # from scrambled dates spanning three different years. Sort by date
    # before slicing, exactly like chokepoint_disruption_alert below
    # already (correctly) does.
    order = sorted(range(len(dates)), key=lambda i: dates[i])
    dates = [dates[i] for i in order]
    counts = [counts[i] for i in order]
    if as_of is not None:
        as_of_str = as_of.isoformat()
        counts = [c for d, c in zip(dates, counts) if d <= as_of_str]

    result = _zscore_of_last(np.array(counts), CONGESTION_WINDOW_DAYS)
    if result is None:
        return None
    last, z = result
    if z < CONGESTION_Z_THRESHOLD:  # only a *rise* in calls signals congestion risk
        return None
    severity: Severity = "critical" if z >= 3.0 else "warning"
    return RiskAlert(
        category="port_congestion",
        severity=severity,
        message=(
            f"{port_label} dry-bulk port calls at {last:.0f}/day, "
            f"z={z:+.1f} vs its own {CONGESTION_WINDOW_DAYS}-day baseline -- possible queue building"
        ),
        metric_value=z,
        threshold=CONGESTION_Z_THRESHOLD,
        subject=port_label,
    )


def chokepoint_disruption_alert(chokepoint_id: str, as_of: date | None = None) -> RiskAlert | None:
    """Real anomaly test on a chokepoint's daily dry-bulk transit count.

    Flags a *drop* only -- a spike in transits is not a disruption signal, a
    sudden fall (partial or full blockage, a grounding, a closure) is.
    """
    path = CHOKEPOINT_DIR / f"{chokepoint_id}_daily_transits.csv"
    if not path.exists():
        return None

    dates: list[str] = []
    counts: list[float] = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            dates.append(row["date"])
            counts.append(float(row["n_dry_bulk"]))
    order = sorted(range(len(dates)), key=lambda i: dates[i])
    dates = [dates[i] for i in order]
    counts = [counts[i] for i in order]
    if as_of is not None:
        as_of_str = as_of.isoformat()
        counts = [c for d, c in zip(dates, counts) if d <= as_of_str]

    result = _zscore_of_last(np.array(counts), CHOKEPOINT_WINDOW_DAYS)
    if result is None:
        return None
    last, z = result
    if z > -CHOKEPOINT_Z_THRESHOLD:  # only a *drop* signals disruption
        return None
    name = CHOKEPOINT_NAMES.get(chokepoint_id, chokepoint_id)
    severity: Severity = "critical" if z <= -3.0 else "warning"
    return RiskAlert(
        category="chokepoint_disruption",
        severity=severity,
        message=(
            f"{name} dry-bulk transits fell to {last:.0f}/day, "
            f"z={z:+.1f} vs its own {CHOKEPOINT_WINDOW_DAYS}-day baseline -- possible disruption"
        ),
        metric_value=z,
        threshold=-CHOKEPOINT_Z_THRESHOLD,
        subject=name,
    )


def _iso_weeks_in_range(start: date, end: date) -> list[int]:
    """Every distinct ISO week number touched by [start, end], in date order,
    each appearing once. Laycan windows are short (days to a few weeks), so a
    plain day-by-day walk is cheap and avoids getting the year-wraparound
    (week 52/53 -> week 1) wrong the way arithmetic on week numbers alone
    would."""
    if end < start:
        start, end = end, start
    weeks: list[int] = []
    seen: set[int] = set()
    d = start
    while d <= end:
        wk = d.isocalendar().week
        if wk not in seen:
            seen.add(wk)
            weeks.append(wk)
        d += timedelta(days=1)
    return weeks


def cyclone_season_alert(
    as_of: date,
    *,
    ports: Sequence[PortEnum] | None = None,
    laycan_start: date | None = None,
    laycan_end: date | None = None,
    climatology_path: Path | None = None,
) -> RiskAlert | None:
    """Real per-basin, per-ISO-week cyclone strike climatology, keyed off the
    actual ports on the quote -- not a live storm-track forecast (see module
    docstring for what that distinction means and doesn't mean).

    Returns None (never raises) when: no ``ports`` were supplied (nothing to
    resolve a basin from); every supplied port maps to no named basin (a real,
    correct outcome -- most of the world isn't one of the five basins this
    fleet trades in, see ``data_builders.build_cyclone_climatology.BASIN_BOUNDS``);
    the climatology file is missing (a warning is logged -- a quote must never
    fail because a moat's data file hasn't been built, same house pattern as
    ``port_congestion_alert``'s ``PortIndexMissingError`` handling); or the
    highest real strike_rate found doesn't clear ``CYCLONE_THRESHOLD_MULTIPLE``
    times the all-basin median.
    """
    if not ports:
        return None
    basins = sorted({b for p in ports for b in basins_for_port(p)})
    if not basins:
        return None

    path = climatology_path or CYCLONE_CLIMATOLOGY_PATH
    if not path.exists():
        LOGGER.warning(
            f"{path} not found -- run `python -m data_builders.build_cyclone_climatology` "
            "(after harvest_ibtracs); cyclone risk check skipped for this quote."
        )
        return None

    if laycan_start is not None and laycan_end is not None:
        weeks = _iso_weeks_in_range(laycan_start, laycan_end)
    else:
        weeks = [as_of.isocalendar().week]

    climatology = pl.read_parquet(path, columns=["basin", "iso_week", "strike_rate"])
    if climatology.is_empty():
        return None
    # Base threshold from the WHOLE table's own distribution, not just the
    # basins/weeks in play for this quote -- "significant" is relative to a
    # season-wide baseline, not to whatever subset happens to be queried.
    median_strike_rate = climatology["strike_rate"].median()
    if median_strike_rate is None or median_strike_rate <= 0:
        return None
    threshold = median_strike_rate * CYCLONE_THRESHOLD_MULTIPLE

    in_scope = climatology.filter(pl.col("basin").is_in(basins) & pl.col("iso_week").is_in(weeks))
    if in_scope.is_empty():
        return None

    top = in_scope.sort("strike_rate", descending=True).row(0, named=True)
    max_rate = float(top["strike_rate"])
    if max_rate < threshold:
        return None

    if max_rate >= threshold * _CYCLONE_CRITICAL_MULTIPLE:
        severity: Severity = "critical"
    elif max_rate >= threshold * _CYCLONE_WARNING_MULTIPLE:
        severity = "warning"
    else:
        severity = "info"

    basin = top["basin"]
    week_label = str(top["iso_week"]) if len(weeks) == 1 else f"{min(weeks)}-{max(weeks)}"
    return RiskAlert(
        category="cyclone_season",
        severity=severity,
        message=(
            f"{basin} cyclone climatology: {max_rate:.3f} storms/season-week in ISO week "
            f"{week_label} -- {max_rate / threshold:.1f}x the {threshold:.3f} "
            f"significant-risk threshold ({CYCLONE_THRESHOLD_MULTIPLE:g}x the all-basin "
            "median). Historical strike rate, not a live storm-track forecast."
        ),
        metric_value=max_rate,
        threshold=threshold,
        subject=basin,
    )


def assess_risk(
    vessel_class: VesselClass,
    port_labels: list[str],
    chokepoint_ids: list[str],
    as_of: date,
    *,
    ports: Sequence[PortEnum] | None = None,
    laycan_start: date | None = None,
    laycan_end: date | None = None,
) -> RiskAssessment:
    """Run every real check available and collect whatever actually fires.

    ``ports``/``laycan_start``/``laycan_end`` are optional and additive --
    forwarded only to the cyclone check (the only one keyed off real ports
    and a real laycan rather than a port-label string): omitting them keeps
    every existing caller working exactly as before, just with the cyclone
    check correctly finding no basin to check (see
    ``cyclone_season_alert``'s own docstring for why that's a real "nothing
    to alert on," not a degraded default).
    """
    alerts: list[RiskAlert] = []

    rate_alert = rate_regime_alert(vessel_class, as_of)
    if rate_alert is not None:
        alerts.append(rate_alert)

    for label in port_labels:
        a = port_congestion_alert(label, as_of)
        if a is not None:
            alerts.append(a)

    for cp_id in chokepoint_ids:
        a = chokepoint_disruption_alert(cp_id, as_of)
        if a is not None:
            alerts.append(a)

    cyclone = cyclone_season_alert(
        as_of, ports=ports, laycan_start=laycan_start, laycan_end=laycan_end
    )
    if cyclone is not None:
        alerts.append(cyclone)

    return RiskAssessment(as_of=as_of, alerts=tuple(alerts))
