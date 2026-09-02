"""Password hashing for the desk's user accounts.

Uses ``hashlib.scrypt`` from the standard library rather than a third-party
password library. scrypt is a standardised memory-hard KDF (RFC 7914) and is
one of the three OWASP-recommended choices for password storage alongside
Argon2id and bcrypt; taking it from the stdlib means there is no additional
dependency to audit, pin or trust for the single most security-sensitive
operation in the system.

Cost parameters
---------------
``n = 2**17, r = 8, p = 1`` is the OWASP Password Storage Cheat Sheet's stated
minimum for scrypt. Measured on the development machine at 272 ms per hash,
which is the right order for an operation that happens at login and nowhere
else. ``maxmem`` has to be passed explicitly: OpenSSL's default ceiling is
32 MiB and this configuration needs 128 MiB (128 * r * n bytes), so without it
the call raises rather than silently weakening.

The parameters are stored **inside** each encoded hash rather than read from
these constants at verify time. Raising the cost later therefore does not
invalidate existing passwords -- old hashes keep verifying against the
parameters they were made with, and are re-hashed on the owner's next
successful login (see ``needs_rehash``).

What this module does not do
----------------------------
It does not implement a password policy. Length and composition rules belong
with the account-creation call that has the user in front of it, not with the
primitive; ``MIN_PASSWORD_LENGTH`` is exported for that caller to enforce and
is deliberately the only rule -- NIST SP 800-63B advises against composition
requirements, which push users toward predictable substitutions.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from typing import Final

#: scrypt CPU/memory cost. Powers of two only.
SCRYPT_N: Final[int] = 2**17
#: Block size. 8 is the standard value every published parameter set uses.
SCRYPT_R: Final[int] = 8
#: Parallelisation. 1 keeps the whole cost in the memory-hard part.
SCRYPT_P: Final[int] = 1
#: Derived key length in bytes.
SCRYPT_DKLEN: Final[int] = 32
#: Salt length in bytes. 16 is the RFC 7914 recommendation.
SALT_BYTES: Final[int] = 16

#: NIST SP 800-63B's minimum for a user-chosen secret. The only rule enforced
#: anywhere in this system: composition requirements (a digit, a symbol, mixed
#: case) measurably reduce entropy by steering users to predictable patterns,
#: and NIST advises against them.
MIN_PASSWORD_LENGTH: Final[int] = 12

_PREFIX: Final[str] = "scrypt"


class InvalidPasswordHashError(ValueError):
    """The stored string is not a hash this module produced.

    Raised rather than returning False, because a malformed hash in the user
    table is a corrupted store or a coding error -- not a wrong password, and
    it must not be reported as one.
    """


def _maxmem(n: int, r: int) -> int:
    """OpenSSL's memory ceiling for these parameters, with headroom.

    scrypt needs 128 * r * n bytes. The doubling covers OpenSSL's own
    accounting, which is slightly above the theoretical figure.
    """
    return 128 * r * n * 2


def hash_password(plaintext: str) -> str:
    """Hash a password with a fresh random salt.

    Returns ``scrypt$n$r$p$<salt b64>$<hash b64>`` -- self-describing, so a
    later cost increase does not strand existing hashes.
    """
    salt = os.urandom(SALT_BYTES)
    derived = hashlib.scrypt(
        plaintext.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        maxmem=_maxmem(SCRYPT_N, SCRYPT_R),
        dklen=SCRYPT_DKLEN,
    )
    return "$".join(
        (
            _PREFIX,
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(derived).decode("ascii"),
        )
    )


def verify_password(plaintext: str, encoded: str) -> bool:
    """Check a password against a stored hash, in constant time.

    The comparison uses ``hmac.compare_digest``: a plain ``==`` on bytes short
    -circuits at the first differing byte, and the timing difference is a real
    (if narrow) oracle on the stored digest.
    """
    n, r, p, salt, expected = _decode(encoded)
    derived = hashlib.scrypt(
        plaintext.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        maxmem=_maxmem(n, r),
        dklen=len(expected),
    )
    return hmac.compare_digest(derived, expected)


def needs_rehash(encoded: str) -> bool:
    """True when a stored hash was made with weaker parameters than current.

    Callers re-hash on the next successful login, which is the only moment the
    plaintext is available. A hash that is already at or above current cost is
    left alone -- never downgraded.
    """
    n, r, p, _salt, _expected = _decode(encoded)
    return n < SCRYPT_N or r < SCRYPT_R or p < SCRYPT_P


def _decode(encoded: str) -> tuple[int, int, int, bytes, bytes]:
    parts = encoded.split("$")
    if len(parts) != 6 or parts[0] != _PREFIX:
        raise InvalidPasswordHashError(
            f"Not a scrypt hash produced by this module: {encoded[:16]!r}..."
        )
    try:
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        salt = base64.b64decode(parts[4], validate=True)
        expected = base64.b64decode(parts[5], validate=True)
    except (ValueError, TypeError) as exc:
        raise InvalidPasswordHashError(f"Malformed scrypt hash: {exc}") from exc
    if n <= 1 or n & (n - 1) or r < 1 or p < 1 or not salt or not expected:
        raise InvalidPasswordHashError(
            f"scrypt parameters out of range: n={n}, r={r}, p={p}, "
            f"salt={len(salt)}B, hash={len(expected)}B"
        )
    return n, r, p, salt, expected
