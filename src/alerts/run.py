"""Evaluating every active watch, once.

Separated from ``conditions`` (which decides whether one condition holds) and
from ``store`` (which persists), because this is the part with the ordering
rule that matters: **state is recorded whether or not the watch fired**.

Getting that backwards is the classic bug in edge-triggered alerting. Record
state only on a firing and the watch never leaves its firing state, so it
fires again on every subsequent evaluation forever; record it only when the
condition is false and it never fires at all. The state write belongs on every
evaluation that produced a real answer, and on no evaluation that did not --
"no data today" must leave yesterday's edge intact so tomorrow can still see
it.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Final

from alerts.conditions import evaluate
from alerts.models import Firing
from alerts.store import AlertStore

LOGGER: Final[logging.Logger] = logging.getLogger(__name__)


def run_due_watches(store: AlertStore, *, now: datetime | None = None) -> list[Firing]:
    """Evaluate every active watch and record what happened.

    Returns the firings this run produced -- empty is the normal, healthy
    result, and means every watched condition is where it was last time.
    """
    fired: list[Firing] = []
    for watch in store.list_watches(active_only=True):
        try:
            result = evaluate(watch, now=now)
        except Exception:
            # One malformed watch must not stop the rest from being
            # evaluated. Logged rather than swallowed silently, and the watch
            # keeps its previous state so it is not quietly reset by its own
            # failure.
            LOGGER.exception("Watch %s (%s) failed to evaluate", watch.watch_id, watch.label)
            continue
        if result is None:
            # No real signal. Deliberately no state write: leaving yesterday's
            # value means an edge that has not yet been seen is still there to
            # be seen tomorrow.
            continue
        if result.fired and result.message is not None:
            fired.append(
                store.record_firing(
                    watch.watch_id,
                    result.message,
                    observed_value=result.value,
                    observed_on=result.observed_on,
                )
            )
        store.record_state(watch.watch_id, result.state, at=now)
    return fired
