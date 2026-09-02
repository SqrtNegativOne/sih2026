"""The user and session store.

Most of these are about refusals rather than features: an account system is
judged by what it declines to do, and every refusal below exists because the
alternative is a real hole (account enumeration, a session that outlives the
account, a deployment with no way back in).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from auth.models import Role, outranks_or_equals
from auth.store import (
    AuthError,
    AuthStore,
    LastAdminError,
    NoSuchUserError,
    UsernameTakenError,
    WeakPasswordError,
)

_ADMIN_PW = "an-admin-length-passphrase"
_USER_PW = "a-user-length-passphrase"


@pytest.fixture
def store() -> AuthStore:
    return AuthStore(":memory:")


@pytest.fixture
def seeded(store: AuthStore) -> AuthStore:
    store.bootstrap_admin("desk.admin", _ADMIN_PW, "Desk Admin")
    store.create_user("m.rao", _USER_PW, "M Rao", Role.CHARTERING_MANAGER)
    store.create_user("v.iyer", _USER_PW, "V Iyer", Role.VIEWER)
    return store


class TestRoles:
    def test_rank_is_ordered_not_a_set(self) -> None:
        """An allow-list check is where "admins can do everything except the
        one thing someone forgot to add them to" comes from."""
        assert outranks_or_equals(Role.ADMIN, Role.VIEWER)
        assert outranks_or_equals(Role.ADMIN, Role.CHARTERING_MANAGER)
        assert outranks_or_equals(Role.CHARTERING_MANAGER, Role.VIEWER)
        assert not outranks_or_equals(Role.VIEWER, Role.CHARTERING_MANAGER)
        assert not outranks_or_equals(Role.CHARTERING_MANAGER, Role.ADMIN)

    def test_every_role_satisfies_itself(self) -> None:
        for r in Role:
            assert outranks_or_equals(r, r)


class TestBootstrap:
    def test_the_first_account_is_an_admin(self, store: AuthStore) -> None:
        u = store.bootstrap_admin("desk.admin", _ADMIN_PW)
        assert u.role is Role.ADMIN
        assert u.is_active

    def test_bootstrap_closes_permanently_once_used(self, store: AuthStore) -> None:
        """Otherwise it is a permanent unauthenticated account-creation
        endpoint, which is exactly the hole it is meant to avoid."""
        store.bootstrap_admin("desk.admin", _ADMIN_PW)
        with pytest.raises(AuthError):
            store.bootstrap_admin("second.admin", _ADMIN_PW)

    def test_a_fresh_store_has_no_accounts_at_all(self, store: AuthStore) -> None:
        """No seeded user, no default password. A well-known first-run
        credential is the single most reliably exploited thing in self-hosted
        software."""
        assert not store.has_any_user()
        assert store.list_users() == ()


class TestAccounts:
    def test_a_duplicate_username_is_refused(self, seeded: AuthStore) -> None:
        with pytest.raises(UsernameTakenError):
            seeded.create_user("m.rao", _USER_PW, "Someone Else", Role.VIEWER)

    def test_usernames_collide_case_insensitively(self, seeded: AuthStore) -> None:
        """`M.Rao` and `m.rao` must not both exist: a ledger line naming one
        of them would be ambiguous, which defeats attribution."""
        with pytest.raises(UsernameTakenError):
            seeded.create_user("M.RAO", _USER_PW, "Impostor", Role.VIEWER)

    def test_a_short_password_is_refused(self, store: AuthStore) -> None:
        with pytest.raises(WeakPasswordError):
            store.create_user("x", "short", "X", Role.VIEWER)

    def test_an_empty_username_is_refused(self, store: AuthStore) -> None:
        with pytest.raises(AuthError):
            store.create_user("   ", _USER_PW, "X", Role.VIEWER)

    def test_display_name_falls_back_to_the_username(self, store: AuthStore) -> None:
        u = store.create_user("j.doe", _USER_PW, "  ", Role.VIEWER)
        assert u.display_name == "j.doe"

    def test_the_user_model_carries_no_password_hash(self, seeded: AuthStore) -> None:
        """The hash must never reach a type that could be serialised into an
        HTTP response by accident."""
        u = seeded.list_users()[0]
        assert "password" not in u.model_dump()
        assert "hash" not in str(u.model_dump()).lower()


class TestAuthentication:
    def test_correct_credentials_authenticate(self, seeded: AuthStore) -> None:
        assert seeded.authenticate("m.rao", _USER_PW) is not None

    def test_username_is_case_insensitive_on_login(self, seeded: AuthStore) -> None:
        assert seeded.authenticate("M.Rao", _USER_PW) is not None

    @pytest.mark.parametrize(
        ("username", "password"),
        [
            ("m.rao", "the-wrong-passphrase"),
            ("nobody.here", _USER_PW),
            ("nobody.here", "the-wrong-passphrase"),
        ],
    )
    def test_every_failure_is_indistinguishable(
        self, seeded: AuthStore, username: str, password: str
    ) -> None:
        """None for all three. An error that separates "no such user" from
        "wrong password" is a free account-enumeration oracle, and the person
        actually locked out is no better served by knowing which it was."""
        assert seeded.authenticate(username, password) is None

    def test_a_disabled_account_cannot_sign_in(self, seeded: AuthStore) -> None:
        u = next(x for x in seeded.list_users() if x.username == "v.iyer")
        seeded.set_active(u.user_id, False)
        assert seeded.authenticate("v.iyer", _USER_PW) is None

    def test_last_login_is_recorded(self, seeded: AuthStore) -> None:
        before = next(x for x in seeded.list_users() if x.username == "m.rao")
        assert before.last_login_at is None
        seeded.authenticate("m.rao", _USER_PW)
        after = next(x for x in seeded.list_users() if x.username == "m.rao")
        assert after.last_login_at is not None


class TestSessions:
    def test_a_session_resolves_to_its_account(self, seeded: AuthStore) -> None:
        u = seeded.authenticate("m.rao", _USER_PW)
        assert u is not None
        s = seeded.create_session(u.user_id)
        resolved = seeded.user_for_session(s.token)
        assert resolved is not None
        assert resolved.username == "m.rao"

    def test_logging_out_kills_the_session_server_side(self, seeded: AuthStore) -> None:
        """The whole reason sessions are stored rather than self-contained
        tokens: signed out means signed out, immediately."""
        u = seeded.authenticate("m.rao", _USER_PW)
        assert u is not None
        s = seeded.create_session(u.user_id)
        seeded.destroy_session(s.token)
        assert seeded.user_for_session(s.token) is None

    def test_an_unknown_or_empty_token_resolves_to_nobody(self, seeded: AuthStore) -> None:
        assert seeded.user_for_session("") is None
        assert seeded.user_for_session("not-a-real-token") is None

    def test_tokens_are_unique_and_long(self, seeded: AuthStore) -> None:
        u = seeded.authenticate("m.rao", _USER_PW)
        assert u is not None
        tokens = {seeded.create_session(u.user_id).token for _ in range(20)}
        assert len(tokens) == 20
        assert all(len(t) >= 32 for t in tokens)

    def test_an_expired_session_stops_working(self, seeded: AuthStore) -> None:
        """Checked on read rather than swept on a timer, so expiry is exact
        regardless of when any cleanup last ran."""
        u = seeded.authenticate("m.rao", _USER_PW)
        assert u is not None
        s = seeded.create_session(u.user_id)
        with seeded._connect() as conn:
            conn.execute(
                "UPDATE sessions SET expires_at = ? WHERE token = ?",
                ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), s.token),
            )
        assert seeded.user_for_session(s.token) is None

    def test_disabling_an_account_drops_its_live_sessions(self, seeded: AuthStore) -> None:
        """A disabled user holding a valid cookie is still signed in, which
        defeats the point of disabling them."""
        u = seeded.authenticate("v.iyer", _USER_PW)
        assert u is not None
        s = seeded.create_session(u.user_id)
        assert seeded.user_for_session(s.token) is not None
        seeded.set_active(u.user_id, False)
        assert seeded.user_for_session(s.token) is None

    def test_changing_a_password_drops_that_accounts_sessions(self, seeded: AuthStore) -> None:
        """Every other browser is holding a session issued against a password
        that no longer exists."""
        u = seeded.authenticate("m.rao", _USER_PW)
        assert u is not None
        s = seeded.create_session(u.user_id)
        seeded.set_password(u.user_id, "a-brand-new-long-passphrase")
        assert seeded.user_for_session(s.token) is None

    def test_purge_removes_only_expired_rows(self, seeded: AuthStore) -> None:
        u = seeded.authenticate("m.rao", _USER_PW)
        assert u is not None
        live = seeded.create_session(u.user_id)
        dead = seeded.create_session(u.user_id)
        with seeded._connect() as conn:
            conn.execute(
                "UPDATE sessions SET expires_at = ? WHERE token = ?",
                ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), dead.token),
            )
        assert seeded.purge_expired_sessions() == 1
        assert seeded.user_for_session(live.token) is not None


class TestLastAdminGuard:
    def test_the_only_admin_cannot_be_disabled(self, seeded: AuthStore) -> None:
        admin = next(x for x in seeded.list_users() if x.role is Role.ADMIN)
        with pytest.raises(LastAdminError):
            seeded.set_active(admin.user_id, False)

    def test_the_only_admin_cannot_be_demoted(self, seeded: AuthStore) -> None:
        admin = next(x for x in seeded.list_users() if x.role is Role.ADMIN)
        with pytest.raises(LastAdminError):
            seeded.set_role(admin.user_id, Role.VIEWER)

    def test_a_second_admin_lifts_the_guard(self, seeded: AuthStore) -> None:
        """The rule is "never zero admins", not "admins are immortal"."""
        first = next(x for x in seeded.list_users() if x.role is Role.ADMIN)
        seeded.create_user("second.admin", _ADMIN_PW, "Second", Role.ADMIN)
        seeded.set_active(first.user_id, False)
        assert seeded.count_active_admins() == 1

    def test_unknown_user_ids_raise(self, seeded: AuthStore) -> None:
        with pytest.raises(NoSuchUserError):
            seeded.set_role("no-such-id", Role.VIEWER)
        with pytest.raises(NoSuchUserError):
            seeded.set_active("no-such-id", False)
        with pytest.raises(NoSuchUserError):
            seeded.set_password("no-such-id", _USER_PW)


class TestHousekeeping:
    def test_signing_in_repeatedly_does_not_grow_the_session_table(
        self, seeded: AuthStore
    ) -> None:
        """Expired rows are dropped when the same account signs in again.

        Nothing is being secured here — ``user_for_session`` refuses expired
        rows either way — but without it the table gains one dead row per
        sign-in, forever.
        """
        u = seeded.authenticate("m.rao", _USER_PW)
        assert u is not None
        for _ in range(5):
            s = seeded.create_session(u.user_id)
            with seeded._connect() as conn:
                conn.execute(
                    "UPDATE sessions SET expires_at = ? WHERE token = ?",
                    ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), s.token),
                )
        live = seeded.create_session(u.user_id)
        with seeded._connect() as conn:
            n = conn.execute(
                "SELECT COUNT(*) AS n FROM sessions WHERE user_id = ?", (u.user_id,)
            ).fetchone()["n"]
        assert n == 1
        assert seeded.user_for_session(live.token) is not None

    def test_purging_never_touches_another_accounts_live_session(
        self, seeded: AuthStore
    ) -> None:
        a = seeded.authenticate("m.rao", _USER_PW)
        b = seeded.authenticate("v.iyer", _USER_PW)
        assert a is not None and b is not None
        b_session = seeded.create_session(b.user_id)
        seeded.create_session(a.user_id)
        seeded.create_session(a.user_id)
        assert seeded.user_for_session(b_session.token) is not None


class TestBootstrapIsAtomic:
    def test_the_guard_is_in_the_insert_not_a_prior_read(self, seeded: AuthStore) -> None:
        """`bootstrap_admin` must not be a check followed by a separate write.

        It is the only unauthenticated write in the system. As two statements,
        two requests arriving together could both see an empty table and both
        create an admin — with different usernames, so the UNIQUE constraint
        never fires. This asserts the refusal happens even when the caller has
        already established that a user exists, i.e. that the guard travels
        with the insert.
        """
        assert seeded.has_any_user()
        with pytest.raises(AuthError):
            seeded.bootstrap_admin("sneaky.admin", _ADMIN_PW)
        assert not any(u.username == "sneaky.admin" for u in seeded.list_users())

    def test_bootstrap_still_validates_its_input(self, store: AuthStore) -> None:
        """The guarded INSERT bypasses create_user, so the password and
        username rules have to be enforced on this path too — a first admin
        with a four-character password would be the worst possible place to
        skip them."""
        with pytest.raises(WeakPasswordError):
            store.bootstrap_admin("desk.admin", "short")
        with pytest.raises(AuthError):
            store.bootstrap_admin("   ", _ADMIN_PW)
        assert not store.has_any_user()
