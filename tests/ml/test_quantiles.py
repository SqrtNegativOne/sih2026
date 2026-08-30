"""Tests for ml.quantiles — crossed-quantile repair.

The property under test: repair must never discard a row, and the repaired
triple must be the rank-sorted version of the input (quantile rearrangement),
which is exact and requires no model retraining.
"""
from __future__ import annotations

import numpy as np
import pytest

from ml.quantiles import QuantileTriple, crossing_rate, repair_crossed_quantiles


def test_already_monotone_passes_through_unchanged() -> None:
    t = repair_crossed_quantiles(9_000.0, 11_000.0, 13_000.0)
    assert t == QuantileTriple(9_000.0, 11_000.0, 13_000.0, was_crossed=False)


def test_fully_inverted_triple_is_rank_sorted() -> None:
    """p10 > p50 > p90 -- the worst case, still repairable without dropping."""
    t = repair_crossed_quantiles(15_000.0, 12_000.0, 9_000.0)
    assert t.p10 == pytest.approx(9_000.0)
    assert t.p50 == pytest.approx(12_000.0)
    assert t.p90 == pytest.approx(15_000.0)
    assert t.was_crossed is True


def test_partial_crossing_p10_above_p50() -> None:
    t = repair_crossed_quantiles(12_000.0, 11_000.0, 13_000.0)
    assert (t.p10, t.p50, t.p90) == (11_000.0, 12_000.0, 13_000.0)
    assert t.was_crossed is True


def test_partial_crossing_p50_above_p90() -> None:
    t = repair_crossed_quantiles(9_000.0, 13_000.0, 11_000.0)
    assert (t.p10, t.p50, t.p90) == (9_000.0, 11_000.0, 13_000.0)
    assert t.was_crossed is True


def test_repair_is_idempotent() -> None:
    once = repair_crossed_quantiles(15_000.0, 12_000.0, 9_000.0)
    twice = repair_crossed_quantiles(once.p10, once.p50, once.p90)
    assert (twice.p10, twice.p50, twice.p90) == (once.p10, once.p50, once.p90)
    assert twice.was_crossed is False


def test_repair_never_invents_or_discards_a_value() -> None:
    """The multiset of values is preserved -- only the assignment to ranks changes."""
    inputs = (15_000.0, 9_000.0, 12_000.0)
    t = repair_crossed_quantiles(*inputs)
    assert sorted((t.p10, t.p50, t.p90)) == sorted(inputs)


def test_ties_are_monotone_non_strict() -> None:
    t = repair_crossed_quantiles(10_000.0, 10_000.0, 10_000.0)
    assert (t.p10, t.p50, t.p90) == (10_000.0, 10_000.0, 10_000.0)
    assert t.was_crossed is False


def test_crossing_rate_on_clean_data() -> None:
    p10 = np.array([1.0, 2.0, 3.0])
    p50 = np.array([2.0, 3.0, 4.0])
    p90 = np.array([3.0, 4.0, 5.0])
    assert crossing_rate(p10, p50, p90) == 0.0


def test_crossing_rate_matches_known_fraction() -> None:
    """One crossed row out of four -- the metric must be exactly 0.25, not rounded."""
    p10 = np.array([1.0, 5.0, 3.0, 1.0])
    p50 = np.array([2.0, 3.0, 4.0, 2.0])  # row 1 (index 1): p10 > p50 -> crossed
    p90 = np.array([3.0, 4.0, 5.0, 3.0])
    assert crossing_rate(p10, p50, p90) == pytest.approx(0.25)


def test_crossing_rate_empty_input() -> None:
    assert crossing_rate(np.array([]), np.array([]), np.array([])) == 0.0
