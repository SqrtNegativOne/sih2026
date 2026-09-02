"""Evaluating watches against real data.

The behaviour worth pinning hardest is edge-triggering and the refusal to
invent a signal. Both are the kind of thing that looks fine in a demo and is
wrong in a way nobody notices for weeks: a level-triggered watch fires every
pass forever, and a watch that carries a stale rate forward fires about a
number that was never published.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from alerts.conditions import MASTER_LONG, evaluate, evaluate_rate_crosses
from alerts.models import Direction, Watch, WatchKind
from alerts.run import run_due_watches
from alerts.store import AlertStore


@pytest.fixture
def store() -> AlertStore:
    return AlertStore(":memory:")


def _crossing(store: AlertStore, threshold: float, direction: Direction) -> Watch:
    return store.create_watch(
        kind=WatchKind.RATE_CROSSES,
        label=f"Supramax {direction.value} {threshold:,.0f}",
        vessel_class="Supramax",
        threshold_usd_per_day=threshold,
        direction=direction,
    )


class TestRealDataOnly:
    def test_the_market_history_is_actually_on_disk(self) -> None:
        """Every other test in this file is meaningless if it is not."""
        assert MASTER_LONG.exists()

    def test_a_firing_carries_the_observations_own_date_not_today(
        self, store: AlertStore
    ) -> None:
        """A rate published on Friday and noticed on Monday fired on Monday
        about Friday's number. Stamping the firing with today would misdate
        the evidence it exists to preserve."""
        w = _crossing(store, 1_000_000, Direction.BELOW)  # certainly true
        store.record_state(w.watch_id, "out")
        fired = run_due_watches(store)
        assert len(fired) == 1
        assert fired[0].observed_on is not None
        assert fired[0].observed_on < datetime.now(UTC).date()

    def test_no_market_history_means_no_signal_not_a_crash(
        self, store: AlertStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A fresh clone that has not run the harvesters has no signal. The
        watch must report that, not raise into whatever called it."""
        monkeypatch.setattr("alerts.conditions.MASTER_LONG", Path("/nonexistent/master.parquet"))
        w = _crossing(store, 22_000, Direction.BELOW)
        assert evaluate(w) is None

    def test_an_unknown_vessel_class_yields_no_signal(self, store: AlertStore) -> None:
        w = store.create_watch(
            kind=WatchKind.RATE_CROSSES,
            label="Ultramax",
            vessel_class="Ultramax",
            threshold_usd_per_day=20_000,
            direction=Direction.BELOW,
        )
        assert evaluate(w) is None


class TestEdgeTriggering:
    def test_a_watch_does_not_fire_on_its_first_look(self, store: AlertStore) -> None:
        """"This was already true when you asked" is not news. A watch created
        deliberately against a condition that already holds would otherwise
        fire instantly and pointlessly."""
        _crossing(store, 1_000_000, Direction.BELOW)
        assert run_due_watches(store) == []

    def test_a_watch_fires_once_on_the_transition_and_not_again(
        self, store: AlertStore
    ) -> None:
        """A rate that sits below a level for a fortnight is one piece of news,
        not fourteen. A bell showing fourteen copies of the same fact is how
        people learn to ignore a bell."""
        w = _crossing(store, 1_000_000, Direction.BELOW)
        store.record_state(w.watch_id, "out")
        assert len(run_due_watches(store)) == 1
        assert run_due_watches(store) == []
        assert run_due_watches(store) == []

    def test_state_is_recorded_even_when_nothing_fires(self, store: AlertStore) -> None:
        """The classic edge-triggering bug is writing state only on a firing,
        which leaves the watch permanently in its firing state."""
        w = _crossing(store, 1_000_000, Direction.BELOW)
        assert store.get_watch(w.watch_id).last_state is None
        run_due_watches(store)
        assert store.get_watch(w.watch_id).last_state == "in"

    def test_no_signal_leaves_the_previous_state_intact(
        self, store: AlertStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An edge that has not been seen yet must still be there to see
        tomorrow. Overwriting state on a no-data pass would silently consume
        it."""
        w = _crossing(store, 1_000_000, Direction.BELOW)
        store.record_state(w.watch_id, "out")
        monkeypatch.setattr("alerts.conditions.MASTER_LONG", Path("/nonexistent/master.parquet"))
        assert run_due_watches(store) == []
        assert store.get_watch(w.watch_id).last_state == "out"
        # Data comes back; the edge is still there.
        monkeypatch.undo()
        assert len(run_due_watches(store)) == 1

    def test_an_inactive_watch_is_not_evaluated(self, store: AlertStore) -> None:
        w = _crossing(store, 1_000_000, Direction.BELOW)
        store.record_state(w.watch_id, "out")
        store.set_watch_active(w.watch_id, False)
        assert run_due_watches(store) == []


class TestDirection:
    def test_above_and_below_are_opposite(self, store: AlertStore) -> None:
        """One of these must hold and the other must not, whatever the real
        rate happens to be today."""
        low = _crossing(store, 1.0, Direction.ABOVE)
        high = _crossing(store, 1_000_000.0, Direction.ABOVE)
        assert evaluate(low).state == "in"
        assert evaluate(high).state == "out"


class TestRateMoves:
    def test_a_window_longer_than_the_history_yields_no_signal(
        self, store: AlertStore
    ) -> None:
        """Reporting "no move" over a period there is no data for would be a
        claim about nothing."""
        w = store.create_watch(
            kind=WatchKind.RATE_MOVES,
            label="decade",
            vessel_class="Supramax",
            move_pct=1.0,
            window_days=100_000,
        )
        assert evaluate(w) is None

    def test_an_impossible_move_never_holds(self, store: AlertStore) -> None:
        w = store.create_watch(
            kind=WatchKind.RATE_MOVES,
            label="impossible",
            vessel_class="Supramax",
            move_pct=100_000.0,
            window_days=30,
        )
        result = evaluate(w)
        assert result is not None
        assert result.state == "out"

    def test_a_trivial_move_always_holds(self, store: AlertStore) -> None:
        w = store.create_watch(
            kind=WatchKind.RATE_MOVES,
            label="trivial",
            vessel_class="Supramax",
            move_pct=0.0001,
            window_days=30,
        )
        result = evaluate(w)
        assert result is not None
        assert result.state == "in"


class TestOutcomeOverdue:
    def test_an_empty_ledger_has_nothing_overdue(
        self, store: AlertStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr("opt.ledger.ENTRIES_LOG", tmp_path / "entries.jsonl")
        monkeypatch.setattr("opt.ledger.OUTCOMES_LOG", tmp_path / "outcomes.jsonl")
        w = store.create_watch(
            kind=WatchKind.OUTCOME_OVERDUE, label="unsettled", overdue_days=7
        )
        result = evaluate(w, now=datetime.now(UTC))
        assert result is not None
        assert result.state == "0"
        assert not result.fired

    def test_the_state_is_the_count_so_a_rising_count_fires_again(
        self, store: AlertStore
    ) -> None:
        """One overdue entry and then five is new information; a watch that
        only ever fired on the first would go quiet exactly as the problem got
        worse."""
        w = store.create_watch(
            kind=WatchKind.OUTCOME_OVERDUE, label="unsettled", overdue_days=3650
        )
        result = evaluate(w, now=datetime.now(UTC) + timedelta(days=1))
        assert result is not None
        assert result.state.isdigit()


class TestFailureIsolation:
    def test_one_broken_watch_does_not_stop_the_others(
        self, store: AlertStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A background loop that aborts on the first bad watch leaves every
        later one silently unevaluated."""
        broken = _crossing(store, 1_000_000, Direction.BELOW)
        good = _crossing(store, 1.0, Direction.ABOVE)
        store.record_state(good.watch_id, "out")

        real = evaluate_rate_crosses

        def explode(w: Watch):
            if w.watch_id == broken.watch_id:
                raise RuntimeError("deliberate")
            return real(w)

        monkeypatch.setattr("alerts.conditions.evaluate_rate_crosses", explode)
        fired = run_due_watches(store)
        assert len(fired) == 1
        assert fired[0].watch_id == good.watch_id
        # The broken watch keeps its previous state rather than being reset by
        # its own failure.
        assert store.get_watch(broken.watch_id).last_state is None
