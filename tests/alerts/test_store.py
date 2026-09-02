"""The watch and firing store.

Mostly about refusals: a watch whose parameters do not describe an evaluable
condition must be rejected at creation, because one that is stored and can
never fire reads to its owner exactly like a market that never moved.
"""

from __future__ import annotations

import pytest

from alerts.models import Direction, WatchKind
from alerts.store import AlertStore, InvalidWatchError, NoSuchWatchError


@pytest.fixture
def store() -> AlertStore:
    return AlertStore(":memory:")


class TestValidation:
    def test_a_watch_needs_a_label(self, store: AlertStore) -> None:
        with pytest.raises(InvalidWatchError):
            store.create_watch(
                kind=WatchKind.OUTCOME_OVERDUE, label="   ", overdue_days=7
            )

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"vessel_class": None, "threshold_usd_per_day": 20_000, "direction": Direction.BELOW},
            {"vessel_class": "Supramax", "threshold_usd_per_day": None, "direction": Direction.BELOW},
            {"vessel_class": "Supramax", "threshold_usd_per_day": 20_000, "direction": None},
            {"vessel_class": "Supramax", "threshold_usd_per_day": 0, "direction": Direction.BELOW},
            {"vessel_class": "Supramax", "threshold_usd_per_day": -5, "direction": Direction.BELOW},
        ],
    )
    def test_an_incomplete_crossing_watch_is_refused(
        self, store: AlertStore, kwargs: dict
    ) -> None:
        with pytest.raises(InvalidWatchError):
            store.create_watch(kind=WatchKind.RATE_CROSSES, label="x", **kwargs)

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"vessel_class": "Supramax", "move_pct": 0, "window_days": 30},
            {"vessel_class": "Supramax", "move_pct": -3, "window_days": 30},
            {"vessel_class": "Supramax", "move_pct": 3, "window_days": 0},
            {"vessel_class": None, "move_pct": 3, "window_days": 30},
        ],
    )
    def test_an_incomplete_move_watch_is_refused(self, store: AlertStore, kwargs: dict) -> None:
        with pytest.raises(InvalidWatchError):
            store.create_watch(kind=WatchKind.RATE_MOVES, label="x", **kwargs)

    @pytest.mark.parametrize("days", [None, 0, -1])
    def test_an_incomplete_overdue_watch_is_refused(
        self, store: AlertStore, days: int | None
    ) -> None:
        with pytest.raises(InvalidWatchError):
            store.create_watch(kind=WatchKind.OUTCOME_OVERDUE, label="x", overdue_days=days)


class TestLifecycle:
    def test_a_created_watch_starts_active_and_unevaluated(self, store: AlertStore) -> None:
        w = store.create_watch(
            kind=WatchKind.OUTCOME_OVERDUE, label="unsettled", overdue_days=7
        )
        assert w.is_active
        assert w.last_state is None
        assert w.last_evaluated_at is None

    def test_creator_is_recorded_and_may_be_absent(self, store: AlertStore) -> None:
        """An open deployment has nobody signed in. That is recorded as None
        rather than as an invented actor."""
        named = store.create_watch(
            kind=WatchKind.OUTCOME_OVERDUE, label="a", overdue_days=7, created_by="m.rao"
        )
        anon = store.create_watch(kind=WatchKind.OUTCOME_OVERDUE, label="b", overdue_days=7)
        assert named.created_by == "m.rao"
        assert anon.created_by is None

    def test_deactivating_hides_it_from_the_active_list_only(self, store: AlertStore) -> None:
        w = store.create_watch(kind=WatchKind.OUTCOME_OVERDUE, label="a", overdue_days=7)
        store.set_watch_active(w.watch_id, False)
        assert len(store.list_watches()) == 1
        assert store.list_watches(active_only=True) == ()

    def test_unknown_ids_raise(self, store: AlertStore) -> None:
        with pytest.raises(NoSuchWatchError):
            store.set_watch_active("no-such-id", False)
        with pytest.raises(NoSuchWatchError):
            store.delete_watch("no-such-id")


class TestFirings:
    def test_deleting_a_watch_cascades_to_its_firings(self, store: AlertStore) -> None:
        """The foreign key is only a constraint because `PRAGMA foreign_keys`
        is set on every connection -- SQLite leaves it off by default, and
        without it this leaves orphaned rows that the bell would keep
        counting."""
        w = store.create_watch(kind=WatchKind.OUTCOME_OVERDUE, label="a", overdue_days=7)
        store.record_firing(w.watch_id, "something happened")
        assert store.unread_count() == 1
        store.delete_watch(w.watch_id)
        assert store.unread_count() == 0
        assert store.list_firings() == ()

    def test_marking_read_clears_the_count_but_keeps_the_record(
        self, store: AlertStore
    ) -> None:
        w = store.create_watch(kind=WatchKind.OUTCOME_OVERDUE, label="a", overdue_days=7)
        store.record_firing(w.watch_id, "one")
        store.record_firing(w.watch_id, "two")
        assert store.mark_all_read() == 2
        assert store.unread_count() == 0
        assert len(store.list_firings()) == 2

    def test_firings_come_back_newest_first(self, store: AlertStore) -> None:
        w = store.create_watch(kind=WatchKind.OUTCOME_OVERDUE, label="a", overdue_days=7)
        for i in range(3):
            store.record_firing(w.watch_id, f"event {i}")
        messages = [f.message for f in store.list_firings()]
        assert messages[0] == "event 2"

    def test_unread_only_filters(self, store: AlertStore) -> None:
        w = store.create_watch(kind=WatchKind.OUTCOME_OVERDUE, label="a", overdue_days=7)
        store.record_firing(w.watch_id, "old")
        store.mark_all_read()
        store.record_firing(w.watch_id, "new")
        unread = store.list_firings(unread_only=True)
        assert [f.message for f in unread] == ["new"]
