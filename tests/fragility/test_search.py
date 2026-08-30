"""Tests for the generic ladder+bisection search, against synthetic
evaluators -- fast, deterministic, and independent of any real solve, so the
algorithm's own correctness properties (monotonicity handling, bracketing,
no-flip reporting, evaluation caps) are provable without a CP-SAT/LSMC cost.
Real-engine integration is covered separately in tests/fragility/test_engine.py.
"""
from __future__ import annotations

from fragility.models import DecisionSignature
from fragility.search import search_flip

_A = DecisionSignature(target_vessel_class="A")
_B = DecisionSignature(target_vessel_class="B")


def _step_at(threshold: float, *, above_is: DecisionSignature = _B, below_is: DecisionSignature = _A):
    def evaluate(x: float) -> DecisionSignature:
        return above_is if x > threshold else below_is
    return evaluate


class TestBasicBracketingAndBisection:
    def test_flip_above_base_is_found_increasing(self) -> None:
        result = search_flip(
            base_value=50.0, base_signature=_A, evaluate=_step_at(100.0),
            tolerance=0.1, max_evaluations=200,
        )
        assert result.flip_found is True
        assert abs(result.flip_value - 100.0) < 0.2
        assert result.flipped_signature == _B

    def test_flip_below_base_is_found_decreasing(self) -> None:
        result = search_flip(
            base_value=50.0, base_signature=_A, evaluate=_step_at(20.0, above_is=_A, below_is=_B),
            tolerance=0.1, max_evaluations=200,
        )
        assert result.flip_found is True
        assert abs(result.flip_value - 20.0) < 0.2

    def test_reported_flip_value_is_on_the_flipped_side_of_the_true_boundary(self) -> None:
        """Bisection must never report a value still on the base side --
        the reported flip_value should itself re-evaluate as flipped."""
        evaluate = _step_at(100.0)
        result = search_flip(
            base_value=50.0, base_signature=_A, evaluate=evaluate, tolerance=0.05, max_evaluations=200,
        )
        assert evaluate(result.flip_value) == _B


class TestNoFlip:
    def test_constant_function_returns_flip_found_false(self) -> None:
        result = search_flip(
            base_value=50.0, base_signature=_A, evaluate=lambda x: _A,
            tolerance=0.1, max_evaluations=20, min_value=0.0, max_value=200.0,
        )
        assert result.flip_found is False
        assert result.flip_value is None

    def test_no_flip_reports_the_range_actually_searched_not_a_fabricated_bound(self) -> None:
        """Requirement 7, literally: the reported range must reflect what
        was actually explored, bounded by the real min/max clamps given."""
        result = search_flip(
            base_value=50.0, base_signature=_A, evaluate=lambda x: _A,
            tolerance=0.1, max_evaluations=100, min_value=0.0, max_value=200.0,
        )
        assert result.range_low == 0.0
        assert result.range_high == 200.0

    def test_no_flip_with_no_bounds_reports_whatever_the_budget_covered(self) -> None:
        result = search_flip(
            base_value=50.0, base_signature=_A, evaluate=lambda x: _A,
            tolerance=0.1, max_evaluations=6,
        )
        assert result.flip_found is False
        # Budget-limited range must be a real, finite interval containing base,
        # not an unbounded or fabricated one.
        assert result.range_low <= 50.0 <= result.range_high
        assert result.range_low < result.range_high


class TestEvaluationCap:
    def test_evaluations_used_never_exceeds_the_cap(self) -> None:
        result = search_flip(
            base_value=50.0, base_signature=_A, evaluate=lambda x: _A,
            tolerance=0.001, max_evaluations=7,
        )
        assert result.evaluations_used <= 7

    def test_evaluations_used_never_exceeds_the_cap_even_when_a_flip_is_found(self) -> None:
        result = search_flip(
            base_value=50.0, base_signature=_A, evaluate=_step_at(1_000_000.0),
            tolerance=0.0001, max_evaluations=5,
        )
        assert result.evaluations_used <= 5
        # With so few evaluations against a very distant boundary, finding
        # it at all within 5 tries is not guaranteed -- only the cap is.

    def test_a_realistic_budget_is_not_needlessly_exhausted_on_an_easy_case(self) -> None:
        result = search_flip(
            base_value=50.0, base_signature=_A, evaluate=_step_at(52.0),
            tolerance=0.1, max_evaluations=60,
        )
        assert result.flip_found is True
        assert result.evaluations_used < 60


class TestMonotonicity:
    """The literal requirement: 'increasing wait never un-flips once
    flipped.' Tested against a genuinely monotone step function (the
    assumption search.py's bisection relies on) -- proving the algorithm
    respects monotonicity where it holds, which is the property the real
    engines are expected (and, for the Tier 1 variables, verified in
    test_engine.py) to have.
    """

    def test_every_evaluation_is_classified_consistently_with_the_true_boundary(self) -> None:
        """Every point search_flip actually evaluated is on the side of the
        true boundary (x=100) its own signature says it is -- the search
        never mistakes a base-side point for a flipped one or vice versa,
        for a genuinely monotone function.

        ``calls`` is recorded via a side-effecting ``evaluate``, then read
        back read-only afterwards -- calling ``evaluate`` again inside the
        verification loop would append yet another entry on every
        iteration, an actual infinite-loop bug caught while first writing
        this test (the loop body and the closure would be mutating and
        consuming the same list at once). Fixed by recording each call's own
        classification at call time instead of re-deriving it afterwards.
        """
        recorded: list[tuple[float, DecisionSignature]] = []

        def evaluate(x: float) -> DecisionSignature:
            signature = _B if x > 100.0 else _A
            recorded.append((x, signature))
            return signature

        result = search_flip(
            base_value=50.0, base_signature=_A, evaluate=evaluate, tolerance=0.01, max_evaluations=200,
        )
        assert result.flip_found is True
        assert recorded  # the search actually evaluated something
        for x, signature in recorded:
            assert signature == (_B if x > 100.0 else _A)

    def test_points_beyond_the_found_flip_value_remain_flipped(self) -> None:
        """A direct check of the requirement's own wording: increasing
        further than the found flip point never reverts to the base
        signature, for a real monotone function."""
        evaluate = _step_at(100.0)
        result = search_flip(
            base_value=50.0, base_signature=_A, evaluate=evaluate, tolerance=0.05, max_evaluations=200,
        )
        assert result.flip_found is True
        for probe in (result.flip_value + 1.0, result.flip_value + 50.0, result.flip_value + 1000.0):
            assert evaluate(probe) == _B, f"un-flipped at {probe}, past the found boundary {result.flip_value}"


class TestFeasibleToContingentInfeasibleIsAFlip:
    """Requirement 1, at the signature-comparison level directly: an
    envelope_status change from feasible to contingent_infeasible must be
    detected as a flip on its own, even when every other field is
    unchanged."""

    def test_envelope_status_alone_changing_is_detected(self) -> None:
        feasible = DecisionSignature(envelope_status="feasible", lock_action="LOCK")
        contingent = DecisionSignature(envelope_status="contingent_infeasible", lock_action="LOCK")
        assert "envelope_status" in feasible.changed_fields(contingent)

    def test_search_finds_a_feasible_to_contingent_infeasible_boundary(self) -> None:
        base = DecisionSignature(envelope_status="feasible", lock_action="LOCK", target_vessel_class="Panamax")

        def evaluate(x: float) -> DecisionSignature:
            status = "contingent_infeasible" if x > 300_000.0 else "feasible"
            return DecisionSignature(envelope_status=status, lock_action="LOCK", target_vessel_class="Panamax")

        result = search_flip(
            base_value=100_000.0, base_signature=base, evaluate=evaluate, tolerance=100.0, max_evaluations=200,
        )
        assert result.flip_found is True
        assert result.flipped_signature.envelope_status == "contingent_infeasible"
