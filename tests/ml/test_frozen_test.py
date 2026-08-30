"""Tests for ml.frozen_test -- the guard on reading the frozen test split."""
from __future__ import annotations

import pytest

from ml.frozen_test import (
    FrozenTestAccessError,
    allow_test_set_access,
    load_frozen_test,
)


def test_direct_access_is_refused() -> None:
    with pytest.raises(FrozenTestAccessError, match="allow_test_set_access"):
        load_frozen_test()


def test_access_inside_allowed_block_succeeds() -> None:
    with allow_test_set_access("unit test: verifying the guard opens"):
        df = load_frozen_test()
    assert df.height > 0
    assert "target_class" in df.columns


def test_access_is_refused_again_after_the_block_closes() -> None:
    with allow_test_set_access("first legitimate read"):
        load_frozen_test()
    with pytest.raises(FrozenTestAccessError):
        load_frozen_test()


def test_reason_is_required() -> None:
    with pytest.raises(ValueError, match="reason"), allow_test_set_access(""):
        pass


def test_whitespace_only_reason_is_rejected() -> None:
    with pytest.raises(ValueError, match="reason"), allow_test_set_access("   "):
        pass


def test_nested_blocks_do_not_revoke_the_outer_grant() -> None:
    """An inner allow-block closing must not lock out the outer caller."""
    with allow_test_set_access("outer"):
        with allow_test_set_access("inner"):
            load_frozen_test()
        # Inner block has exited; outer grant must still be in effect.
        load_frozen_test()
    with pytest.raises(FrozenTestAccessError):
        load_frozen_test()


def test_exception_inside_block_still_closes_access() -> None:
    class Boom(Exception):
        pass

    with pytest.raises(Boom), allow_test_set_access("about to fail"):
        raise Boom()
    with pytest.raises(FrozenTestAccessError):
        load_frozen_test()
