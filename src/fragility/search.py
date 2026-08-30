"""The generic flip-point search: coarse geometric ladder outward from a base
value, then bisection to a tolerance -- deliberately independent of what
"evaluate" actually calls (a closed-form register check, fleet-mix, or a full
quote_envelope solve). tiers.py supplies the evaluator; this module only
knows how to search a 1-D real line for the first place a caller-supplied
equality test stops holding.

Assumes monotonicity: at most one boundary along a ray from the base value
outward in a given direction (requirement: "monotone: increasing wait never
un-flips once flipped"). This is a search-algorithm assumption, not
something verified at runtime -- a genuinely non-monotone underlying
function could make bisection converge to *a* boundary, not necessarily the
nearest one. None of the v1 variables are expected to behave that way (see
engine.py's per-variable design notes), and this is recorded, not hidden.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from fragility.models import DecisionSignature


@dataclass(frozen=True)
class SearchResult:
    flip_found: bool
    flip_value: float | None
    flipped_signature: DecisionSignature | None
    range_low: float
    range_high: float
    evaluations_used: int


@dataclass
class _Budget:
    """Mutable evaluation counter shared across one search call -- a plain
    counter, not a dataclass field default, so both directions of one search
    draw from the same cap rather than each getting their own."""

    max_evaluations: int
    used: int = field(default=0)

    def spend(self) -> bool:
        """True if there is budget left; increments regardless, so the
        caller's own loop condition can rely on "was there budget when I
        asked" without a separate re-check."""
        if self.used >= self.max_evaluations:
            return False
        self.used += 1
        return True


def search_flip(
    *,
    base_value: float,
    base_signature: DecisionSignature,
    evaluate: Callable[[float], DecisionSignature],
    tolerance: float,
    max_evaluations: int,
    min_value: float | None = None,
    max_value: float | None = None,
) -> SearchResult:
    """Search both directions from base_value for the nearest point where
    ``evaluate(...)`` returns a signature differing from ``base_signature``
    in any populated field. Tries the increase and decrease directions in
    small interleaved steps first (so a close flip on either side is found
    before the budget is spent over-extending the other way), then commits
    to whichever direction found one and bisects it to ``tolerance``.
    """
    budget = _Budget(max_evaluations=max_evaluations)

    # Interleaved coarse probing: one small step each way before doubling,
    # so budget isn't wasted extending far in the wrong direction first.
    probe_step = tolerance * 8.0
    up_lo, up_flip_candidate = base_value, None
    down_lo, down_flip_candidate = base_value, None
    up_bound_hit = min_value is not None and max_value is not None and max_value == base_value
    down_bound_hit = min_value is not None and min_value == base_value

    while budget.used < max_evaluations and up_flip_candidate is None and down_flip_candidate is None:
        progressed = False
        if not up_bound_hit:
            candidate = base_value + probe_step
            if max_value is not None and candidate >= max_value:
                candidate, up_bound_hit = max_value, True
            if candidate > up_lo and budget.spend():
                result = evaluate(candidate)
                progressed = True
                if result.changed_fields(base_signature):
                    up_flip_candidate = (up_lo, candidate, result)
                else:
                    up_lo = candidate
                    if up_bound_hit:
                        pass  # reached the bound with no flip; stop probing up
            elif up_bound_hit:
                pass
        if down_flip_candidate is None and not down_bound_hit and budget.used < max_evaluations:
            candidate = base_value - probe_step
            if min_value is not None and candidate <= min_value:
                candidate, down_bound_hit = min_value, True
            if candidate < down_lo and budget.spend():
                result = evaluate(candidate)
                progressed = True
                if result.changed_fields(base_signature):
                    down_flip_candidate = (down_lo, candidate, result)
                else:
                    down_lo = candidate
        probe_step *= 2.0
        if not progressed:
            break
        if up_bound_hit and down_bound_hit:
            break

    chosen = up_flip_candidate or down_flip_candidate
    if chosen is None:
        return SearchResult(
            flip_found=False,
            flip_value=None,
            flipped_signature=None,
            range_low=down_lo,
            range_high=up_lo,
            evaluations_used=budget.used,
        )

    lo, hi, hi_signature = chosen
    while abs(hi - lo) > tolerance and budget.spend():
        mid = (lo + hi) / 2.0
        result = evaluate(mid)
        if result.changed_fields(base_signature):
            hi, hi_signature = mid, result
        else:
            lo = mid

    return SearchResult(
        flip_found=True,
        flip_value=hi,
        flipped_signature=hi_signature,
        range_low=min(down_lo, base_value),
        range_high=max(up_lo, base_value),
        evaluations_used=budget.used,
    )
