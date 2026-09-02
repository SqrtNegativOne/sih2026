"""Users, roles and sessions for the chartering desk.

Three roles, and the reason there are three rather than two.

The obvious split is "the SAIL official who uses it" and "the admin who runs
it". That is one role short, and the missing one is the important one: this
system keeps a **decision ledger** -- an append-only, forward-only record of
every recommendation it made and every outcome someone reported against it
(``opt.ledger``). That ledger is only worth anything if what it says happened
actually happened, which means recording an outcome has to be a privileged,
attributable act. If everyone who can read a quote can also write to the
ledger, the audit trail records "someone" and the performance statistics
computed from it (``opt.ledger.compute_performance``) mean nothing.

So:

- ``VIEWER`` -- can see everything and compute anything. Quotes, season plans,
  fragility sweeps, the portfolio frontier, the ledger's contents. Nothing a
  viewer does persists. This is the right level for most of a chartering desk:
  analysts, planners, anyone who needs the answer but does not fix the cargo.
- ``CHARTERING_MANAGER`` -- everything a viewer can do, plus **recording
  realised outcomes** against past recommendations. This is the person who
  actually fixed the vessel and therefore knows what it fixed at. Their name
  goes on the ledger line.
- ``ADMIN`` -- everything above, plus managing accounts and resetting the
  ledger. Ledger reset is destructive and irreversible by design (see
  ``opt.ledger.reset_ledger``), which is exactly why it sits at the top.

Roles are ordered, and permission checks compare rank rather than testing
equality against a set. An unordered check is where "admins can do everything
except the one thing someone forgot to add them to" comes from.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Final

from pydantic import BaseModel, ConfigDict


class Role(str, Enum):
    """What an account is allowed to do. Ordered by ``RANK`` below."""

    VIEWER = "viewer"
    CHARTERING_MANAGER = "chartering_manager"
    ADMIN = "admin"


#: Ordering for permission checks. Higher outranks lower.
RANK: Final[dict[Role, int]] = {
    Role.VIEWER: 0,
    Role.CHARTERING_MANAGER: 1,
    Role.ADMIN: 2,
}

#: What each role is for, in the words the UI shows a person choosing one.
ROLE_DESCRIPTION: Final[dict[Role, str]] = {
    Role.VIEWER: (
        "Can run and read everything — quotes, season plans, fragility, the "
        "portfolio frontier, the ledger. Cannot record an outcome or change "
        "an account."
    ),
    Role.CHARTERING_MANAGER: (
        "Everything a viewer can do, plus recording what a fixture actually "
        "achieved against the recommendation. Their name goes on the ledger "
        "line, which is what makes the performance record auditable."
    ),
    Role.ADMIN: (
        "Everything above, plus creating and disabling accounts and resetting "
        "the decision ledger. Ledger reset is irreversible."
    ),
}


def outranks_or_equals(held: Role, required: Role) -> bool:
    """Does an account holding ``held`` satisfy a requirement of ``required``?

    Rank comparison, not set membership: a new privileged action added later
    is automatically available to every role above the one it names, which is
    the behaviour people expect and the one an explicit allow-list gets wrong
    by omission.
    """
    return RANK[held] >= RANK[required]


class User(BaseModel):
    """An account. Never carries the password hash -- see ``auth.store``.

    The hash lives only in the store and is passed directly to
    ``auth.passwords.verify_password``; it is never loaded into a model that
    could be serialised into an HTTP response by accident. That is the whole
    reason this type exists separately from the database row.
    """

    model_config = ConfigDict(frozen=True)

    user_id: str
    username: str
    display_name: str
    role: Role
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None = None


class Session(BaseModel):
    """A logged-in browser.

    Server-side sessions rather than signed tokens: a session can be revoked
    the instant an account is disabled, whereas a self-contained JWT stays
    valid until it expires no matter what the user table says. For a system
    whose point is an auditable record of who did what, "logged out means
    logged out" is worth more than statelessness.
    """

    model_config = ConfigDict(frozen=True)

    token: str
    user_id: str
    created_at: datetime
    expires_at: datetime
