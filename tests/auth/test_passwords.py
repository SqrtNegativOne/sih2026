"""Password hashing.

The properties worth pinning are the ones whose absence is invisible: a hash
that verifies but uses a fixed salt, or a comparison that leaks timing, looks
exactly like a correct one from the outside.
"""

from __future__ import annotations

import pytest

from auth.passwords import (
    MIN_PASSWORD_LENGTH,
    SCRYPT_N,
    SCRYPT_P,
    SCRYPT_R,
    InvalidPasswordHashError,
    hash_password,
    needs_rehash,
    verify_password,
)

_PW = "a-real-length-passphrase"


class TestHashing:
    def test_a_password_verifies_against_its_own_hash(self) -> None:
        assert verify_password(_PW, hash_password(_PW))

    def test_a_wrong_password_does_not(self) -> None:
        assert not verify_password("not-the-right-passphrase", hash_password(_PW))

    def test_the_plaintext_never_appears_in_the_hash(self) -> None:
        assert _PW not in hash_password(_PW)

    def test_every_hash_uses_a_fresh_salt(self) -> None:
        """Two accounts with the same password must not share a hash.

        A shared hash means one precomputation breaks every account that chose
        that password, and it leaks which accounts share one.
        """
        assert hash_password(_PW) != hash_password(_PW)

    def test_the_hash_records_the_parameters_it_was_made_with(self) -> None:
        """So that raising the cost later does not strand existing hashes."""
        parts = hash_password(_PW).split("$")
        assert parts[0] == "scrypt"
        assert (int(parts[1]), int(parts[2]), int(parts[3])) == (SCRYPT_N, SCRYPT_R, SCRYPT_P)

    def test_cost_is_at_least_the_owasp_minimum(self) -> None:
        """OWASP's Password Storage Cheat Sheet minimum for scrypt. A guard
        against someone lowering it to speed up a test run."""
        assert SCRYPT_N >= 2**17
        assert SCRYPT_R >= 8
        assert SCRYPT_P >= 1


class TestRehash:
    def test_a_current_hash_does_not_need_rehashing(self) -> None:
        assert not needs_rehash(hash_password(_PW))

    def test_a_weaker_hash_is_flagged_and_still_verifies(self) -> None:
        """Old hashes must keep working -- upgrading cost cannot lock people
        out of their own accounts. They are re-hashed on next login, which is
        the only moment the plaintext exists."""
        weak = hash_password(_PW).split("$")
        weak[1] = str(2**14)
        # A hash genuinely made at the lower cost, not a doctored string.
        import base64
        import hashlib

        salt = base64.b64decode(weak[4])
        derived = hashlib.scrypt(
            _PW.encode(), salt=salt, n=2**14, r=8, p=1, maxmem=128 * 8 * 2**14 * 2, dklen=32
        )
        weak[5] = base64.b64encode(derived).decode()
        encoded = "$".join(weak)
        assert verify_password(_PW, encoded)
        assert needs_rehash(encoded)


class TestMalformed:
    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "not-a-hash",
            "bcrypt$1$2$3$4$5",
            "scrypt$0$8$1$AAAA$AAAA",
            "scrypt$notanumber$8$1$AAAA$AAAA",
            "scrypt$65536$8$1$AAAA",
        ],
    )
    def test_a_malformed_hash_raises_rather_than_reporting_a_wrong_password(
        self, bad: str
    ) -> None:
        """A corrupted row in the user table is a broken store or a coding
        error. Reporting it as "wrong password" would send someone to reset a
        password that was never the problem."""
        with pytest.raises(InvalidPasswordHashError):
            verify_password(_PW, bad)

    def test_a_non_power_of_two_cost_is_rejected(self) -> None:
        """scrypt requires n to be a power of two; anything else means the
        stored string was not produced by this module."""
        with pytest.raises(InvalidPasswordHashError):
            verify_password(_PW, "scrypt$65535$8$1$QUFBQQ==$QUFBQQ==")


def test_minimum_length_is_the_nist_floor() -> None:
    """NIST SP 800-63B's minimum for a user-chosen secret, and deliberately
    the only rule: composition requirements measurably reduce entropy by
    steering people to predictable substitutions."""
    assert MIN_PASSWORD_LENGTH >= 12
