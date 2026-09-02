"""Evaluating watches against real data on disk.

Every condition here resolves against a real observation or does not resolve
at all. There is no interpolation, no carry-forward, and no "closest available
date": a watch that cannot be evaluated today returns ``None`` and the caller
leaves its state untouched, so tomorrow's evaluation still sees the same edge
it saw today.

That matters more than it sounds. The obvious convenience — "no rate published
today, use the last one" — would make a watch fire on a day the market did not
move, about a number that was not published, and the firing would carry today's
date. A record like that is worse than no record: it looks like evidence.

Network policy
--------------
Nothing here fetches anything. `master_long.parquet` and the decision ledger
are both already on disk, written by the build-time harvesters under
``src/data_builders/``. This module is a consumer, per the repository's
network policy, and degrades to "no signal" when a file is missing rather than
raising into a caller.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Final

import polars as pl

from alerts.models import Direction, Watch, WatchKind
from ml import units

LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
MASTER_LONG: Final[Path] = REPO_ROOT / "src" / "data" / "master_long.parquet"


class Evaluation:
    """The result of evaluating one watch.

    ``state`` is the condition's value now, stored back on the watch so the
    next evaluation can tell a transition from a continuation. ``message`` and
    ``value`` are set only when the watch has just crossed INTO its firing
    state -- an edge, not a level.
    """

    __slots__ = ("message", "observed_on", "state", "value")

    def __init__(
        self,
        state: str,
        message: str | None = None,
        value: float | None = None,
        observed_on: date | None = None,
    ) -> None:
        self.state = state
        self.message = message
        self.value = value
        self.observed_on = observed_on

    @property
    def fired(self) -> bool:
        return self.message is not None


def _series_for(vessel_class: str) -> str | None:
    entry = units.CLASS_SERIES.get(vessel_class)
    return entry[1] if entry else None


def _rate_history(series_id: str) -> pl.DataFrame | None:
    """Every real observation of one series, oldest first. None if the market
    history is not on disk at all -- a fresh clone that has not run the
    harvesters has no signal, which is reported rather than raised."""
    if not MASTER_LONG.exists():
        LOGGER.info("No market history at %s; rate watches have no signal.", MASTER_LONG)
        return None
    df = (
        pl.read_parquet(MASTER_LONG)
        .filter(pl.col("series_id") == series_id)
        .select(["date", "value"])
        .sort("date")
    )
    return df if df.height else None


def evaluate_rate_crosses(watch: Watch) -> Evaluation | None:
    """Has the published class average crossed the watch's level?

    The state is which side of the threshold the LATEST REAL observation sits
    on. Firing happens on the transition onto the watched side, so a rate that
    sits above a level for a fortnight is one piece of news rather than
    fourteen.
    """
    if watch.vessel_class is None or watch.threshold_usd_per_day is None or watch.direction is None:
        return None
    series_id = _series_for(watch.vessel_class)
    if series_id is None:
        return None
    hist = _rate_history(series_id)
    if hist is None:
        return None

    latest = hist.row(-1, named=True)
    value = float(latest["value"])
    observed_on = latest["date"]
    threshold = watch.threshold_usd_per_day

    inside = value > threshold if watch.direction is Direction.ABOVE else value < threshold
    state = "in" if inside else "out"

    # An edge, and only an edge. `last_state is None` is a watch that has
    # never been evaluated: it does NOT fire on its first look even if the
    # condition already holds, because "this was already true when you asked"
    # is not news -- and a watch created deliberately against a condition that
    # is already true would otherwise fire instantly and pointlessly.
    if inside and watch.last_state == "out":
        side = "above" if watch.direction is Direction.ABOVE else "below"
        return Evaluation(
            state=state,
            message=(
                f"{watch.vessel_class} spot TC average is {side} "
                f"${threshold:,.0f}/day — {series_id} published "
                f"${value:,.0f}/day on {observed_on.isoformat()}."
            ),
            value=value,
            observed_on=observed_on,
        )
    return Evaluation(state=state)


def evaluate_rate_moves(watch: Watch) -> Evaluation | None:
    """Has the class average moved more than the watched percentage?

    Both endpoints are real published observations. The window start is the
    LAST observation on or before the cutoff date, not an interpolation to the
    cutoff itself: the index is not published every calendar day, and inventing
    a value for a day it was not quoted would put a fabricated number on both
    sides of the comparison.
    """
    if watch.vessel_class is None or watch.move_pct is None or watch.window_days is None:
        return None
    series_id = _series_for(watch.vessel_class)
    if series_id is None:
        return None
    hist = _rate_history(series_id)
    if hist is None or hist.height < 2:
        return None

    latest = hist.row(-1, named=True)
    cutoff = latest["date"] - timedelta(days=watch.window_days)
    earlier = hist.filter(pl.col("date") <= cutoff)
    if earlier.is_empty():
        # The history does not reach back far enough. Reporting "no move"
        # would be a claim about a period there is no data for.
        return None

    start = earlier.row(-1, named=True)
    start_value = float(start["value"])
    end_value = float(latest["value"])
    if start_value == 0:
        return None

    pct = (end_value - start_value) / start_value * 100.0
    moved = abs(pct) >= watch.move_pct
    state = "in" if moved else "out"

    if moved and watch.last_state == "out":
        direction = "up" if pct > 0 else "down"
        return Evaluation(
            state=state,
            message=(
                f"{watch.vessel_class} spot TC average moved {direction} {abs(pct):.1f}% "
                f"over {watch.window_days} days — ${start_value:,.0f}/day on "
                f"{start['date'].isoformat()} to ${end_value:,.0f}/day on "
                f"{latest['date'].isoformat()}."
            ),
            value=pct,
            observed_on=latest["date"],
        )
    return Evaluation(state=state)


def evaluate_outcome_overdue(watch: Watch, *, now: datetime | None = None) -> Evaluation | None:
    """Are there ledger entries nobody has settled?

    This is the watch that protects the evidence base.
    ``opt.ledger.compute_performance`` scores this system only over entries
    with a linked outcome, so an entry that is never settled does not make the
    record look bad -- it silently removes itself from the record. A desk that
    never notices that is grading itself on a shrinking, self-selected sample.

    The state is the count of overdue entries, so the watch fires again when
    the count rises rather than only on the first one.
    """
    if watch.overdue_days is None:
        return None
    # Imported here rather than at module scope: opt.ledger pulls in the whole
    # optimizer type graph, and a rate watch has no business paying for that.
    from opt import ledger

    reference = now or datetime.now(UTC)
    cutoff = reference - timedelta(days=watch.overdue_days)
    try:
        entries = ledger.read_entries()
    except (OSError, ValueError) as exc:
        LOGGER.info("Ledger unreadable, outcome watch has no signal: %s", exc)
        return None

    settled = {o.entry_id for o in ledger.read_outcomes()}
    overdue = [
        e
        for e in entries
        if e.entry_id not in settled and e.decision_timestamp.astimezone(UTC) < cutoff
    ]
    state = str(len(overdue))

    previous = int(watch.last_state) if (watch.last_state or "").isdigit() else 0
    if overdue and len(overdue) > previous:
        oldest = min(overdue, key=lambda e: e.decision_timestamp)
        age = (reference - oldest.decision_timestamp.astimezone(UTC)).days
        return Evaluation(
            state=state,
            message=(
                f"{len(overdue)} ledger {'entry' if len(overdue) == 1 else 'entries'} "
                f"unsettled for more than {watch.overdue_days} days. The oldest "
                f"({oldest.origin_port} to {oldest.dest_port}) has waited {age} days. "
                f"Performance is scored only over entries with a reported outcome."
            ),
            value=float(len(overdue)),
            observed_on=oldest.decision_timestamp.date(),
        )
    return Evaluation(state=state)


def evaluate(watch: Watch, *, now: datetime | None = None) -> Evaluation | None:
    """Evaluate one watch. None means "no real signal today"."""
    if not watch.is_active:
        return None
    if watch.kind is WatchKind.RATE_CROSSES:
        return evaluate_rate_crosses(watch)
    if watch.kind is WatchKind.RATE_MOVES:
        return evaluate_rate_moves(watch)
    if watch.kind is WatchKind.OUTCOME_OVERDUE:
        return evaluate_outcome_overdue(watch, now=now)
    return None
