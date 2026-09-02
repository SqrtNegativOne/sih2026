"""The user and session store, on SQLite.

Why SQLite and not Postgres
---------------------------
SQLite is a real ACID database with real transactions, constraints and
foreign keys -- not a downgrade from Postgres so much as a different
deployment shape. What it buys here is that a fresh clone of this repository
runs with **no setup at all**: no server to install, no connection string to
configure, no migration step before the first login works. That property has
been treated as non-negotiable throughout this project and it is worth more,
at this stage, than the concurrency ceiling Postgres would raise.

What it costs is honest to state: one writer at a time, and no network
access from a second process on another machine. A chartering desk with a
handful of users on one deployment is comfortably inside that. If this ever
needs to outgrow it, every statement below is plain SQL and the swap is a
different ``_connect`` -- which is exactly why all the SQL is in this one
module and none of it leaks into the routes.

Timestamps are stored as ISO-8601 UTC strings, not integers: SQLite has no
native datetime type either way, and a stored value a human can read in a
database browser is worth the handful of bytes.

Nothing here is a demo fixture. There is no seeded account, no default
password, and no "admin/admin" anywhere in this file -- ``bootstrap_admin``
takes a real password from the caller and refuses to invent one.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

from auth.models import Role, Session, User
from auth.passwords import (
    MIN_PASSWORD_LENGTH,
    hash_password,
    needs_rehash,
    verify_password,
)

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]

#: Default location. Under raw_data/ beside the ledger, which is already the
#: home for state this system writes rather than reads.
DEFAULT_DB_PATH: Final[Path] = REPO_ROOT / "raw_data" / "auth" / "desk.sqlite3"

#: How long a login lasts. Long enough not to interrupt a working day,
#: short enough that an unattended browser is not a standing key.
SESSION_TTL: Final[timedelta] = timedelta(hours=12)

#: Session token entropy, in bytes. 32 bytes = 256 bits from
#: ``secrets.token_urlsafe``, well past any brute-force concern.
TOKEN_BYTES: Final[int] = 32

_SCHEMA: Final[str] = """
CREATE TABLE IF NOT EXISTS users (
    user_id       TEXT PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name  TEXT NOT NULL,
    role          TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
"""


class AuthError(RuntimeError):
    """Base for every named failure in this module."""


class UsernameTakenError(AuthError):
    """That username already exists. Usernames are compared case-insensitively
    so that ``S.Kumar`` and ``s.kumar`` cannot both exist and be confused for
    each other on a ledger line."""


class WeakPasswordError(AuthError):
    """Shorter than ``auth.passwords.MIN_PASSWORD_LENGTH``."""


class NoSuchUserError(AuthError):
    """No account with that id or username."""


class LastAdminError(AuthError):
    """Refusing to remove the last route back in.

    Disabling or demoting the only active admin would leave a deployment with
    no way to create accounts or restore access, recoverable only by editing
    the database by hand. That is a foot-gun, not a policy decision, so it is
    blocked at the store rather than left to the caller to remember.
    """


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class AuthStore:
    """Users and sessions. Every SQL statement in the system lives here."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        env = os.environ.get("DESK_AUTH_DB")
        raw = str(db_path or env or DEFAULT_DB_PATH)
        self.is_memory = raw == ":memory:"
        self.db_path = raw if self.is_memory else Path(raw)
        # An in-memory database exists only for as long as its connection
        # does. Opening one per statement -- correct and cheap for a file --
        # would drop the schema the moment __init__ returned, so the
        # in-memory case holds a single connection open for the store's life.
        # Used by tests; a real deployment is always a file.
        self._shared: sqlite3.Connection | None = None
        self._lock = threading.Lock() if self.is_memory else None
        if self.is_memory:
            self._shared = self._new_connection()
        else:
            assert isinstance(self.db_path, Path)
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _new_connection(self) -> sqlite3.Connection:
        # check_same_thread=False only for the shared in-memory connection:
        # FastAPI runs sync handlers in a threadpool, so a store held across
        # requests is touched from more than one thread. The `_lock` below is
        # what actually makes that safe; this flag only stops sqlite3 from
        # refusing outright. File-backed connections are opened per statement
        # and never cross a thread, so they keep the safer default.
        conn = sqlite3.connect(self.db_path, check_same_thread=not self.is_memory)
        conn.row_factory = sqlite3.Row
        # Off by default in SQLite, for backwards compatibility. Without it
        # the sessions -> users foreign key is documentation rather than a
        # constraint, and deleting a user would silently orphan live sessions.
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

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    # -- accounts ---------------------------------------------------------

    def create_user(
        self,
        username: str,
        password: str,
        display_name: str,
        role: Role,
    ) -> User:
        """Create an account. The password is hashed before it touches disk."""
        username = username.strip()
        if not username:
            raise AuthError("A username is required.")
        if len(password) < MIN_PASSWORD_LENGTH:
            raise WeakPasswordError(
                f"A password must be at least {MIN_PASSWORD_LENGTH} characters. "
                f"Length is the only rule -- NIST SP 800-63B advises against "
                f"composition requirements, which push people toward predictable "
                f"substitutions."
            )
        user_id = str(uuid.uuid4())
        now = _utc_now()
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO users (user_id, username, display_name, role, "
                    "password_hash, is_active, created_at, last_login_at) "
                    "VALUES (?, ?, ?, ?, ?, 1, ?, NULL)",
                    (
                        user_id,
                        username,
                        display_name.strip() or username,
                        role.value,
                        hash_password(password),
                        _iso(now),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise UsernameTakenError(f"The username {username!r} is already in use.") from exc
        return User(
            user_id=user_id,
            username=username,
            display_name=display_name.strip() or username,
            role=role,
            is_active=True,
            created_at=now,
        )

    def get_user(self, user_id: str) -> User | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return _row_to_user(row) if row else None

    def list_users(self) -> tuple[User, ...]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY username COLLATE NOCASE").fetchall()
        return tuple(_row_to_user(r) for r in rows)

    def count_active_admins(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM users WHERE role = ? AND is_active = 1",
                (Role.ADMIN.value,),
            ).fetchone()
        return int(row["n"])

    def set_active(self, user_id: str, is_active: bool) -> User:
        """Enable or disable an account.

        Disabling also drops every live session for that account: a disabled
        user with a valid cookie is still logged in, which defeats the point.
        """
        user = self.get_user(user_id)
        if user is None:
            raise NoSuchUserError(f"No account with id {user_id!r}.")
        if not is_active and user.role is Role.ADMIN and self.count_active_admins() <= 1:
            raise LastAdminError(
                "This is the only active admin. Disabling it would leave the "
                "deployment with no way to create accounts or restore access."
            )
        with self._connect() as conn:
            conn.execute("UPDATE users SET is_active = ? WHERE user_id = ?", (int(is_active), user_id))
            if not is_active:
                conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        return user.model_copy(update={"is_active": is_active})

    def set_role(self, user_id: str, role: Role) -> User:
        user = self.get_user(user_id)
        if user is None:
            raise NoSuchUserError(f"No account with id {user_id!r}.")
        if user.role is Role.ADMIN and role is not Role.ADMIN and self.count_active_admins() <= 1:
            raise LastAdminError(
                "This is the only active admin. Demoting it would leave the "
                "deployment with no way to create accounts or restore access."
            )
        with self._connect() as conn:
            conn.execute("UPDATE users SET role = ? WHERE user_id = ?", (role.value, user_id))
        return user.model_copy(update={"role": role})

    def set_password(self, user_id: str, password: str) -> None:
        if len(password) < MIN_PASSWORD_LENGTH:
            raise WeakPasswordError(
                f"A password must be at least {MIN_PASSWORD_LENGTH} characters."
            )
        if self.get_user(user_id) is None:
            raise NoSuchUserError(f"No account with id {user_id!r}.")
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE user_id = ?",
                (hash_password(password), user_id),
            )
            # Every other browser holding a session for this account is now
            # holding one that outlived the password it was issued against.
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    # -- authentication ---------------------------------------------------

    def authenticate(self, username: str, password: str) -> User | None:
        """Check a username and password.

        Returns None for every failure -- unknown user, wrong password,
        disabled account -- and never says which. The caller has no legitimate
        use for the distinction and an attacker does: an error that
        distinguishes "no such user" from "wrong password" is a free account
        enumeration oracle.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username.strip(),)
            ).fetchone()
        if row is None:
            # Hash anyway. Returning immediately makes an unknown username
            # answer in microseconds and a known one in ~270 ms, which is a
            # timing oracle wide enough to read over the network.
            hash_password(password)
            return None
        if not verify_password(password, row["password_hash"]):
            return None
        if not row["is_active"]:
            return None

        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET last_login_at = ? WHERE user_id = ?", (_iso(now), row["user_id"])
            )
            # The one moment the plaintext exists and the cost can be raised.
            if needs_rehash(row["password_hash"]):
                conn.execute(
                    "UPDATE users SET password_hash = ? WHERE user_id = ?",
                    (hash_password(password), row["user_id"]),
                )
        return _row_to_user(row).model_copy(update={"last_login_at": now})

    def create_session(self, user_id: str) -> Session:
        now = _utc_now()
        session = Session(
            token=secrets.token_urlsafe(TOKEN_BYTES),
            user_id=user_id,
            created_at=now,
            expires_at=now + SESSION_TTL,
        )
        with self._connect() as conn:
            # Drop this account's already-expired rows while we are here.
            # ``user_for_session`` refuses them either way, so nothing is
            # being secured -- but without this the table only ever grows,
            # one dead row per sign-in, for the life of the deployment. The
            # delete is indexed by user_id and bounded by one person's
            # history, so it costs nothing at the one moment it is free.
            conn.execute(
                "DELETE FROM sessions WHERE user_id = ? AND expires_at <= ?",
                (user_id, _iso(now)),
            )
            conn.execute(
                "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (session.token, session.user_id, _iso(session.created_at), _iso(session.expires_at)),
            )
        return session

    def user_for_session(self, token: str) -> User | None:
        """Resolve a session cookie to its account, or None.

        Expiry is checked here rather than swept on a timer, so an expired
        session stops working the moment it expires regardless of when the
        cleanup last ran.
        """
        if not token:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT u.*, s.expires_at AS session_expires_at "
                "FROM sessions s JOIN users u ON u.user_id = s.user_id "
                "WHERE s.token = ?",
                (token,),
            ).fetchone()
        if row is None:
            return None
        expires = _parse(row["session_expires_at"])
        if expires is None or expires <= _utc_now():
            self.destroy_session(token)
            return None
        if not row["is_active"]:
            return None
        return _row_to_user(row)

    def destroy_session(self, token: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))

    def purge_expired_sessions(self) -> int:
        """Delete sessions that have already expired. Housekeeping only --
        ``user_for_session`` already refuses them, so this frees rows rather
        than closing a hole."""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (_iso(_utc_now()),))
            return cur.rowcount

    # -- first run --------------------------------------------------------

    def has_any_user(self) -> bool:
        with self._connect() as conn:
            return conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None

    def bootstrap_admin(self, username: str, password: str, display_name: str = "") -> User:
        """Create the first admin, on an empty store only.

        Refuses once any account exists, so it cannot be used to mint a second
        way in. There is deliberately **no default password**: a well-known
        first-run credential is the single most reliably exploited thing in
        self-hosted software, and a system whose value is an auditable record
        of who decided what cannot start life with an account nobody owns.
        """
        username = username.strip()
        if not username:
            raise AuthError("A username is required.")
        if len(password) < MIN_PASSWORD_LENGTH:
            raise WeakPasswordError(
                f"A password must be at least {MIN_PASSWORD_LENGTH} characters."
            )

        user_id = str(uuid.uuid4())
        now = _utc_now()
        # Guarded INSERT rather than "check, then create". This is the only
        # unauthenticated write in the system, and a check followed by a
        # separate insert is two transactions: two requests arriving together
        # could both see an empty table and both create an admin, with
        # different usernames so the UNIQUE constraint never fires. One
        # statement whose WHERE NOT EXISTS is evaluated inside the same
        # transaction as its insert cannot be interleaved that way.
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO users (user_id, username, display_name, role, "
                "password_hash, is_active, created_at, last_login_at) "
                "SELECT ?, ?, ?, ?, ?, 1, ?, NULL "
                "WHERE NOT EXISTS (SELECT 1 FROM users)",
                (
                    user_id,
                    username,
                    display_name.strip() or username,
                    Role.ADMIN.value,
                    hash_password(password),
                    _iso(now),
                ),
            )
            created = cur.rowcount == 1
        if not created:
            raise AuthError(
                "The account store is not empty. bootstrap_admin only creates the "
                "first account; use an existing admin to create more."
            )
        return User(
            user_id=user_id,
            username=username,
            display_name=display_name.strip() or username,
            role=Role.ADMIN,
            is_active=True,
            created_at=now,
        )


def _row_to_user(row: sqlite3.Row) -> User:
    return User(
        user_id=row["user_id"],
        username=row["username"],
        display_name=row["display_name"],
        role=Role(row["role"]),
        is_active=bool(row["is_active"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        last_login_at=_parse(row["last_login_at"]),
    )
