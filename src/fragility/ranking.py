"""P5 requirement 4: fragile/stable ranking across a FragilityReport's
findings, so a caller (the UI in particular) leads with what matters for
this specific cargo.

A pure, additive, presentation-layer function -- it reads a FragilityReport
that analyze_fragility already produced and does not call any engine itself,
so it cannot affect determinism, evaluation caps, or the UNAVAILABLE
discipline (nothing here searches anything).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from fragility.models import FlipPoint, FragilityReport

__all__ = ["FragilityCategory", "RankedFinding", "rank_findings"]

FragilityCategory = Literal["FRAGILE", "STABLE", "UNAVAILABLE"]

#: A sane ceiling on the fragility score for a "no flip found in the searched
#: range" finding -- large enough that any real flip_found=True score (a
#: percent delta, typically well under 1000%) sorts before it, so STABLE
#: findings always land after every real FRAGILE one, not interleaved by
#: coincidence of a huge-but-real percent delta.
_STABLE_SCORE: Final[float] = 1.0e12


@dataclass(frozen=True)
class RankedFinding:
    finding: FlipPoint
    category: FragilityCategory
    fragility_score: float | None
    """Lower = more fragile (closer to flipping). Only ever set for FRAGILE
    findings (a real percent/absolute delta to the flip point) -- None for
    both STABLE (searched, nothing found: there is no flip distance to
    report) and UNAVAILABLE (no search attempted at all). Earlier versions
    of this field reused the internal STABLE sort constant here too, which
    leaked to callers as a literal "score 1,000,000,000,000.0" (F-32) --
    that constant is now `_sort_key`-only, see below, and never appears
    outside this module."""
    rank: int
    """1-based position within the FRAGILE+STABLE ordering (fragile-first);
    UNAVAILABLE findings are appended after, each also numbered, so every
    finding still has a stable, total order for display, but a caller can
    tell the two groups apart via `category`."""


def _sort_key(fp: FlipPoint) -> float:
    """Internal ordering value only -- smaller sorts first (more fragile).
    Never returned to a caller; see `rank_findings`' own `fragility_score`
    for the public value, which is None wherever this sentinel would
    otherwise leak through as a fake "score"."""
    if fp.percent_delta is not None:
        return abs(fp.percent_delta)
    if fp.absolute_delta is not None:
        # base_value was 0 (percent_delta undefined) -- fall back to the raw
        # delta itself. Only reached by a variable whose base is genuinely
        # zero (none of the current v1 variables normally are), kept for
        # robustness rather than assumed unreachable.
        return abs(fp.absolute_delta)
    return _STABLE_SCORE  # flip_found=False, range searched, nothing found


def rank_findings(report: FragilityReport) -> tuple[RankedFinding, ...]:
    """FRAGILE findings first (smallest fragility_score = nearest flip),
    then STABLE (searched, no flip in range), then UNAVAILABLE (no search
    attempted at all) -- each finding appears exactly once, in exactly one
    category, never re-ordered within category by anything but its own
    score."""
    scored: list[tuple[FlipPoint, FragilityCategory, float]] = []
    for fp in report.findings:
        if fp.unavailable_reason is not None:
            scored.append((fp, "UNAVAILABLE", _STABLE_SCORE))
        elif fp.flip_found:
            scored.append((fp, "FRAGILE", _sort_key(fp)))
        else:
            scored.append((fp, "STABLE", _sort_key(fp)))

    fragile_and_stable = sorted(
        (t for t in scored if t[1] != "UNAVAILABLE"), key=lambda t: t[2]
    )
    unavailable = [t for t in scored if t[1] == "UNAVAILABLE"]

    out: list[RankedFinding] = []
    for i, (fp, category, sort_key) in enumerate(fragile_and_stable, start=1):
        public_score = sort_key if category == "FRAGILE" else None
        out.append(RankedFinding(finding=fp, category=category, fragility_score=public_score, rank=i))
    start = len(out) + 1
    for i, (fp, category, _sort_key_val) in enumerate(unavailable, start=start):
        out.append(RankedFinding(finding=fp, category=category, fragility_score=None, rank=i))
    return tuple(out)
