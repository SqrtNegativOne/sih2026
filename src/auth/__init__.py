"""Accounts, roles and sessions for the chartering desk.

``models`` defines the three roles and why there are three; ``passwords``
hashes with stdlib scrypt; ``store`` is the SQLite user and session store and
holds every SQL statement in the system.
"""

from auth.models import RANK, ROLE_DESCRIPTION, Role, Session, User, outranks_or_equals
from auth.passwords import MIN_PASSWORD_LENGTH, hash_password, verify_password
from auth.store import (
    AuthError,
    AuthStore,
    LastAdminError,
    NoSuchUserError,
    UsernameTakenError,
    WeakPasswordError,
)

__all__ = [
    "MIN_PASSWORD_LENGTH",
    "RANK",
    "ROLE_DESCRIPTION",
    "AuthError",
    "AuthStore",
    "LastAdminError",
    "NoSuchUserError",
    "Role",
    "Session",
    "User",
    "UsernameTakenError",
    "WeakPasswordError",
    "hash_password",
    "outranks_or_equals",
    "verify_password",
]
