"""Dynamic port congestion -> expected wait-days (Sub-problem 2/3 input):
PS deliverable (d)'s other half, "real-time port congestion... for both
origin and destination," previously not real at all.

``opt.network.Port.expected_wait_days`` is a static, hand-set literal per
port. It is correctly *used* -- as the opex-per-unproductive-day cost input
in both ``opt.voyage`` (origin port queue wait, priced into the CP-SAT
objective) and ``opt.repositioning`` (candidate-port wait, priced into the
repositioning score) -- but it never changes, no matter what's actually
happening at that port right now.

``opt.risk.port_congestion_alert`` already reads the same real IMF PortWatch
daily dry-bulk call-count data, but a z-score answers "is this unusual,"
not "how many days will a ship actually wait" -- a different question this
module answers instead, without inventing a queueing model this data can't
support: scale the existing static baseline by how much busier the port's
real recent call count is versus its own longer-run average. A port running
at its normal call rate gets its normal wait-day baseline back unchanged; a
port currently seeing twice its usual traffic gets roughly double the wait
allowance, clamped to a conservative range so a handful of noisy recent days
can't produce an implausible number.

Falls back to the static baseline, untouched, wherever real data doesn't
support a dynamic estimate (no PortWatch coverage for this port, not enough
real history yet, or any unexpected failure reading it) -- exactly the same
graceful-degradation contract every other real-data signal in this codebase
already follows. ``opt.voyage``'s CP-SAT scheduler in particular must never
be broken by this enrichment failing to compute.
"""
from __future__ import annotations

import csv
import functools
from datetime import date

from opt.network import PORT_TO_TONNAGE_LABEL, PortEnum
from tonnage.basins import port_csv_path

__all__ = ["clear_wait_days_cache", "dynamic_wait_days"]

#: "Current congestion" window -- short enough to reflect real, present
#: conditions. Half of opt.risk's own 60-day congestion baseline window.
RECENT_WINDOW_DAYS = 14
#: "Normal for this port" window, ending right before the recent window
#: starts -- matches opt.risk.CONGESTION_WINDOW_DAYS so both signals read
#: the same baseline length.
BASELINE_WINDOW_DAYS = 60

#: The multiplier on the static baseline is clamped to this range -- real,
#: current relative congestion, not an unbounded extrapolation from a
#: handful of noisy recent days.
_MIN_MULTIPLIER = 0.5
_MAX_MULTIPLIER = 3.0


def _congestion_multiplier(recent_counts: list[float], baseline_counts: list[float]) -> float | None:
    """(mean of recent) / (mean of baseline), clamped to
    [_MIN_MULTIPLIER, _MAX_MULTIPLIER]. None if the baseline has no real
    activity to compare against (mean <= 0) -- a multiplier isn't meaningful
    there, not "assume calm."
    """
    baseline_mean = sum(baseline_counts) / len(baseline_counts)
    if baseline_mean <= 0:
        return None
    recent_mean = sum(recent_counts) / len(recent_counts)
    return max(_MIN_MULTIPLIER, min(_MAX_MULTIPLIER, recent_mean / baseline_mean))


def _real_wait_days(port: PortEnum, static_baseline: float, as_of: date | None) -> tuple[float, bool]:
    label = PORT_TO_TONNAGE_LABEL.get(port)
    if label is None:
        return static_baseline, False
    path = port_csv_path(label)
    if not path.exists():
        return static_baseline, False

    dates: list[str] = []
    counts: list[float] = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            dates.append(row["date"])
            counts.append(float(row["portcalls_dry_bulk"]))

    order = sorted(range(len(dates)), key=lambda i: dates[i])
    dates = [dates[i] for i in order]
    counts = [counts[i] for i in order]
    # F-21 fix: this used to always read the file's own trailing rows
    # regardless of what date was actually being priced -- a reproducible
    # historical quote (as_of in the past) got today's real congestion
    # instead of the congestion as of the date it claimed to price. Trim
    # to real rows on or before as_of first, same as-of discipline used
    # throughout this codebase, before taking the trailing windows below.
    if as_of is not None:
        as_of_str = as_of.isoformat()
        cutoff = next((i for i, d in enumerate(dates) if d > as_of_str), len(dates))
        dates = dates[:cutoff]
        counts = counts[:cutoff]

    needed = BASELINE_WINDOW_DAYS + RECENT_WINDOW_DAYS
    if len(counts) < needed:
        return static_baseline, False

    recent = counts[-RECENT_WINDOW_DAYS:]
    baseline_window = counts[-needed:-RECENT_WINDOW_DAYS]
    multiplier = _congestion_multiplier(recent, baseline_window)
    if multiplier is None:
        return static_baseline, False
    return static_baseline * multiplier, True


@functools.cache
def dynamic_wait_days(port: PortEnum, as_of: date | None = None) -> tuple[float, bool]:
    """(expected wait-days at ``port``, is_real_data).

    is_real_data is False exactly when no real PortWatch coverage exists for
    this port, there isn't enough history yet, or reading it failed for any
    reason -- in every such case, value is the unmodified static baseline
    (``port.value.expected_wait_days``), not a fabricated dynamic one. Never
    raises: this feeds opt.voyage's CP-SAT objective directly, which must
    keep working even if this enrichment can't compute.

    as_of:
        F-21 fix: optional, defaults to None (unchanged legacy behaviour --
        always the file's own most recent real rows, i.e. "today"). Pass a
        real date to price congestion as of that date instead -- needed for
        a genuinely reproducible historical quote, which otherwise silently
        got today's real congestion regardless of what date it claimed to
        price. ``functools.cache`` keys on this parameter too, so an
        as_of-bearing call and a legacy (None) call for the same port are
        correctly cached separately, not conflated.

    Cached for the process lifetime (same reasoning as
    ``opt.repositioning._port_class_hazard_rates``: real per-port file I/O,
    called once per (vessel, cargo) pair or candidate port otherwise). Call
    ``clear_wait_days_cache()`` after rebuilding the P1/P2 harvest within a
    live process.
    """
    static_baseline = port.value.expected_wait_days
    try:
        return _real_wait_days(port, static_baseline, as_of)
    except (OSError, ValueError, KeyError):
        return static_baseline, False


def clear_wait_days_cache() -> None:
    """Drop the cached wait-day estimates. Call after rebuilding the P1/P2
    harvest within a live process."""
    dynamic_wait_days.cache_clear()
