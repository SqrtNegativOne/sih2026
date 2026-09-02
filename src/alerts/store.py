"""Watches and firings, in the same SQLite file the accounts live in.

One database file rather than two, because they are one deployment's state and
a watch's ``created_by`` refers to a real account. The two modules own separate
tables and neither reaches into the other's; ``auth.store`` holds every
statement about accounts and this holds every statement about alerts.

The connection handling deliberately mirrors ``auth.store``: a connection per
statement for a file, one shared connection for ``:memory:`` (which exists only
while a connection is open, so opening per statement would drop the schema),
and ``PRAGMA foreign_keys = ON`` every time, because SQLite leaves it off by
default and without it the firings-to-watches foreign key is a comment.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Final

from alerts.models import Direction, Firing, Watch, WatchKind

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]

#: The same file auth.store uses. Overridable together, on purpose: they are
#: one deployment's state and separating them would let a restore put accounts
#: and their watches out of step.
DEFAULT_DB_PATH: Final[Path] = REPO_ROOT / "raw_data" / "auth" / "desk.sqlite3"

_SCHEMA: Final[str] = """
CREATE TABLE IF NOT EXISTS watches (
    watch_id             TEXT PRIMARY KEY,
    kind                 TEXT NOT NULL,
    label                TEXT NOT NULL,
    is_active            INTEGER NOT NULL DEFAULT 1,
    created_at           TEXT NOT NULL,
    created_by           TEXT,
    vessel_class         TEXT,
    threshold_usd_per_day REAL,
    direction            TEXT,
    move_pct             REAL,
    window_days          INTEGER,
    overdue_days         INTEGER,
    last_state           TEXT,
    last_evaluated_at    TEXT
);

CREATE TABLE IF NOT EXISTS firings (
    firing_id      TEXT PRIMARY KEY,
    watch_id       TEXT NOT NULL REFERENCES watches(watch_id) ON DELETE CASCADE,
    fired_at       TEXT NOT NULL,
    observed_on    TEXT,
    message        TEXT NOT NULL,
    observed_value REAL,
    is_read        INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_firings_watch ON firings(watch_id);
CREATE INDEX IF NOT EXISTS idx_firings_unread ON firings(is_read, fired_at);
"""


class AlertError(RuntimeError):
    """Base for every named failure in this module."""


class NoSuchWatchError(AlertError):
    """No watch with that id."""


class InvalidWatchError(AlertError):
    """The watch's parameters do not describe an evaluable condition.

    Raised rather than stored, because a watch that cannot be evaluated is a
    watch that will silently never fire -- which reads to its owner exactly
    like a market that never moved.
    """


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


class AlertStore:
    def __init__(self, db_path: Path | str | None = None) -> None:
        env = os.environ.get("DESK_AUTH_DB")
        raw = str(db_path or env or DEFAULT_DB_PATH)
        self.is_memory = raw == ":memory:"
        self.db_path = raw if self.is_memory else Path(raw)
        self._shared: sqlite3.Connection | None = None
        self._lock = threading.Lock() if self.is_memory else None
        if self.is_memory:
            self._shared = self._new_connection()
        else:
            assert isinstance(self.db_path, Path)
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _new_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=not self.is_memory)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        if self._shared is not None:
            assert self._lock is not None
            with self._lock:
                try:
                    yield self._shared
                    self._shared.commit()
                except Exception:
                    self._shared.rollback()
                    raise
            return
        conn = self._new_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # -- watches ----------------------------------------------------------

    def create_watch(
        self,
        *,
        kind: WatchKind,
        label: str,
        created_by: str | None = None,
        vessel_class: str | None = None,
        threshold_usd_per_day: float | None = None,
        direction: Direction | None = None,
        move_pct: float | None = None,
        window_days: int | None = None,
        overdue_days: int | None = None,
    ) -> Watch:
        """Create a watch, refusing one that could never fire."""
        label = label.strip()
        if not label:
            raise InvalidWatchError("A watch needs a label — it is how you will recognise it.")

        if kind is WatchKind.RATE_CROSSES:
            if not vessel_class or threshold_usd_per_day is None or direction is None:
                raise InvalidWatchError(
                    "A rate-crossing watch needs a vessel class, a threshold and a direction."
                )
            if threshold_usd_per_day <= 0:
                raise InvalidWatchError("A rate threshold must be a positive dollar figure.")
        elif kind is WatchKind.RATE_MOVES:
            if not vessel_class or move_pct is None or window_days is None:
                raise InvalidWatchError(
                    "A rate-move watch needs a vessel class, a percentage and a window in days."
                )
            if move_pct <= 0:
                raise InvalidWatchError(
                    "A move threshold must be positive. The watch already fires on a move in "
                    "either direction, so a negative figure would describe nothing."
                )
            if window_days < 1:
                raise InvalidWatchError("A move window must be at least one day.")
        elif kind is WatchKind.OUTCOME_OVERDUE:
            if overdue_days is None or overdue_days < 1:
                raise InvalidWatchError(
                    "An overdue-outcome watch needs a whole number of days, at least one."
                )

        watch = Watch(
            watch_id=str(uuid.uuid4()),
            kind=kind,
            label=label,
            is_active=True,
            created_at=_utc_now(),
            created_by=created_by,
            vessel_class=vessel_class,
            threshold_usd_per_day=threshold_usd_per_day,
            direction=direction,
            move_pct=move_pct,
            window_days=window_days,
            overdue_days=overdue_days,
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO watches (watch_id, kind, label, is_active, created_at, created_by, "
                "vessel_class, threshold_usd_per_day, direction, move_pct, window_days, "
                "overdue_days, last_state, last_evaluated_at) "
                "VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
                (
                    watch.watch_id,
                    watch.kind.value,
                    watch.label,
                    _iso(watch.created_at),
                    watch.created_by,
                    watch.vessel_class,
                    watch.threshold_usd_per_day,
                    watch.direction.value if watch.direction else None,
                    watch.move_pct,
                    watch.window_days,
                    watch.overdue_days,
                ),
            )
        return watch

    def list_watches(self, *, active_only: bool = False) -> tuple[Watch, ...]:
        sql = "SELECT * FROM watches"
        if active_only:
            sql += " WHERE is_active = 1"
        sql += " ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(sql).fetchall()
        return tuple(_row_to_watch(r) for r in rows)

    def get_watch(self, watch_id: str) -> Watch | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM watches WHERE watch_id = ?", (watch_id,)).fetchone()
        return _row_to_watch(row) if row else None

    def set_watch_active(self, watch_id: str, is_active: bool) -> Watch:
        if self.get_watch(watch_id) is None:
            raise NoSuchWatchError(f"No watch with id {watch_id!r}.")
        with self._connect() as conn:
            conn.execute(
                "UPDATE watches SET is_active = ? WHERE watch_id = ?", (int(is_active), watch_id)
            )
        watch = self.get_watch(watch_id)
        assert watch is not None
        return watch

    def delete_watch(self, watch_id: str) -> None:
        """Delete a watch and, by cascade, its firings.

        Unlike the decision ledger -- which is append-only because it is
        evidence about this system's own recommendations -- a watch is a
        preference. Deleting one destroys no record of anything that happened
        in the market, only a standing request to be told about it.
        """
        if self.get_watch(watch_id) is None:
            raise NoSuchWatchError(f"No watch with id {watch_id!r}.")
        with self._connect() as conn:
            conn.execute("DELETE FROM watches WHERE watch_id = ?", (watch_id,))

    def record_state(self, watch_id: str, state: str, at: datetime | None = None) -> None:
        """Store the condition's current value, so the next evaluation can
        tell a transition from a continuation."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE watches SET last_state = ?, last_evaluated_at = ? WHERE watch_id = ?",
                (state, _iso(at or _utc_now()), watch_id),
            )

    # -- firings ----------------------------------------------------------

    def record_firing(
        self,
        watch_id: str,
        message: str,
        observed_value: float | None = None,
        observed_on: date | None = None,
    ) -> Firing:
        firing = Firing(
            firing_id=str(uuid.uuid4()),
            watch_id=watch_id,
            fired_at=_utc_now(),
            observed_on=observed_on,
            message=message,
            observed_value=observed_value,
            is_read=False,
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO firings (firing_id, watch_id, fired_at, observed_on, message, "
                "observed_value, is_read) VALUES (?, ?, ?, ?, ?, ?, 0)",
                (
                    firing.firing_id,
                    firing.watch_id,
                    _iso(firing.fired_at),
                    observed_on.isoformat() if observed_on else None,
                    firing.message,
                    firing.observed_value,
                ),
            )
        return firing

    def list_firings(self, *, limit: int = 50, unread_only: bool = False) -> tuple[Firing, ...]:
        sql = "SELECT * FROM firings"
        if unread_only:
            sql += " WHERE is_read = 0"
        sql += " ORDER BY fired_at DESC LIMIT ?"
        with self._connect() as conn:
            rows = conn.execute(sql, (limit,)).fetchall()
        return tuple(_row_to_firing(r) for r in rows)

    def unread_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM firings WHERE is_read = 0").fetchone()
        return int(row["n"])

    def mark_all_read(self) -> int:
        with self._connect() as conn:
            cur = conn.execute("UPDATE firings SET is_read = 1 WHERE is_read = 0")
            return cur.rowcount


def _row_to_watch(row: sqlite3.Row) -> Watch:
    return Watch(
        watch_id=row["watch_id"],
        kind=WatchKind(row["kind"]),
        label=row["label"],
        is_active=bool(row["is_active"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        created_by=row["created_by"],
        vessel_class=row["vessel_class"],
        threshold_usd_per_day=row["threshold_usd_per_day"],
        direction=Direction(row["direction"]) if row["direction"] else None,
        move_pct=row["move_pct"],
        window_days=row["window_days"],
        overdue_days=row["overdue_days"],
        last_state=row["last_state"],
        last_evaluated_at=(
            datetime.fromisoformat(row["last_evaluated_at"]) if row["last_evaluated_at"] else None
        ),
    )


def _row_to_firing(row: sqlite3.Row) -> Firing:
    return Firing(
        firing_id=row["firing_id"],
        watch_id=row["watch_id"],
        fired_at=datetime.fromisoformat(row["fired_at"]),
        observed_on=date.fromisoformat(row["observed_on"]) if row["observed_on"] else None,
        message=row["message"],
        observed_value=row["observed_value"],
        is_read=bool(row["is_read"]),
    )
