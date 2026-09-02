"""Standing watches on the desk, and the firings they produce.

The top bar used to carry a notification bell. It was removed (F-79) with an
explicit note: *"Alerts need somewhere to persist and someone to notify; both
arrive with the account system, and until then the icon promises a capability
that does not exist anywhere in the stack."* Both now exist, so the capability
can be built rather than the icon restored.

What a watch may be about
-------------------------
Only conditions this system can evaluate **honestly, from real data already on
disk**. That rules more out than it lets in, and the exclusions are the
interesting part:

- ``RATE_CROSSES`` — a published class TC average crosses a level. Real: the
  Baltic class averages in ``master_long.parquet`` (``ml.units.CLASS_SERIES``).
- ``RATE_MOVES`` — that same average moves more than a given percentage over a
  window. Real, and computed from two real observations, never interpolated.
- ``OUTCOME_OVERDUE`` — a recommendation has sat in the decision ledger with no
  reported outcome for longer than a given number of days. Real, cheap, and
  the one that most directly protects the thing this system is judged on: the
  performance record is computed only from entries with linked outcomes, so
  entries nobody ever settles quietly shrink the evidence base.

Deliberately **not** offered:

- "Tell me when a vessel becomes available" — there is no live fleet feed here.
- "Tell me when a port's congestion changes" — PortWatch is a build-time
  harvest with its own cadence; a watch firing on the day the file happened to
  be rebuilt would be reporting on the harvest, not on the port.
- "Tell me when the market moves" with no threshold — a condition with no
  falsifiable trigger is a feeling, not an alert.

What "fires" means
------------------
A firing is a durable record that a condition was true at a moment, evaluated
against a real observation and stamped with the observation's own date. It is
not a push notification: nothing here emails, texts or calls anyone, and the
UI says so. Claiming delivery this system does not perform would be exactly
the failure the removed bell was avoiding.

Edge-triggered, not level-triggered
-----------------------------------
A watch fires on the **transition** into its condition, not on every
evaluation while the condition holds. A rate that sits below a threshold for a
fortnight is one piece of news, not fourteen; a bell showing 14 unread copies
of the same fact is how people learn to ignore a bell.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Final

from pydantic import BaseModel, ConfigDict


class WatchKind(str, Enum):
    """What a watch is about. Every member is evaluable from data on disk."""

    RATE_CROSSES = "rate_crosses"
    RATE_MOVES = "rate_moves"
    OUTCOME_OVERDUE = "outcome_overdue"


class Direction(str, Enum):
    ABOVE = "above"
    BELOW = "below"


#: What each kind means, in the words the UI shows.
WATCH_DESCRIPTION: Final[dict[WatchKind, str]] = {
    WatchKind.RATE_CROSSES: (
        "The published spot TC average for a vessel class crosses a level you set. "
        "Read from the real Baltic series on disk, on the day it was published."
    ),
    WatchKind.RATE_MOVES: (
        "That same average moves more than a percentage you set, over a window you "
        "set. Computed from two real observations — never from an interpolated one."
    ),
    WatchKind.OUTCOME_OVERDUE: (
        "A recommendation has sat in the decision ledger with no reported outcome "
        "for longer than you allow. The performance record is computed only from "
        "entries that have one, so unsettled entries quietly shrink the evidence."
    ),
}


class Watch(BaseModel):
    """A standing condition someone asked to be told about."""

    model_config = ConfigDict(frozen=True)

    watch_id: str
    kind: WatchKind
    label: str
    """What the person called it. Free text, shown wherever the watch appears."""

    is_active: bool
    created_at: datetime
    created_by: str | None
    """Username of whoever created it, or None on an open deployment where
    nobody was signed in. Recorded rather than invented — an unattributed
    watch says so by saying nothing."""

    # -- parameters, per kind ------------------------------------------
    vessel_class: str | None = None
    """RATE_CROSSES / RATE_MOVES. One of ml.units.CLASS_SERIES."""

    threshold_usd_per_day: float | None = None
    """RATE_CROSSES. The level."""

    direction: Direction | None = None
    """RATE_CROSSES. Which way the crossing counts."""

    move_pct: float | None = None
    """RATE_MOVES. Absolute percentage move that counts, e.g. 5.0."""

    window_days: int | None = None
    """RATE_MOVES. Over how many calendar days."""

    overdue_days: int | None = None
    """OUTCOME_OVERDUE. How long an entry may sit unsettled."""

    # -- edge-trigger state --------------------------------------------
    last_state: str | None = None
    """The condition's value at the previous evaluation, as a short opaque
    string. A watch fires only when this changes into the firing state, which
    is what makes it edge-triggered. None means never evaluated."""

    last_evaluated_at: datetime | None = None


class Firing(BaseModel):
    """A durable record that a watch's condition became true.

    Carries the real figure and the real date it was observed, so a firing can
    be checked against the data rather than taken on trust.
    """

    model_config = ConfigDict(frozen=True)

    firing_id: str
    watch_id: str
    fired_at: datetime
    """When this system noticed."""

    observed_on: date | None
    """The date of the real observation that triggered it — which is NOT the
    same as ``fired_at`` and is usually earlier. A rate published on Friday and
    noticed on Monday fired on Monday about Friday's number, and conflating
    the two would misdate the evidence."""

    message: str
    """One sentence, naming the real figures involved."""

    observed_value: float | None
    is_read: bool
