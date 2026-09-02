"""Repo-wide static check: no hash-generated or synthetic value anywhere in
frontend/src/ -- the P3 Tonnage Field frontend guard test (P3's own Context
notes M1's earlier frontend WAS a string-hash placeholder, since deleted; this
is the tripwire against that pattern ever coming back, for any screen, not
just Tonnage Field).

Same philosophy as tests/test_frozen_test_guard.py: a plain, auditable regex
scan rather than an AST/parser walk -- easier to trust, and the thing being
guarded against (a deterministic hash/PRNG standing in for a real value) is a
textual pattern, not a semantic one that needs a parser to find.

One real, pre-existing hit is allowlisted by exact file+line, reviewed here
rather than silently ignored:
  - components/desk/quote-drawer.tsx: `Math.random()` contributes to a React
    list `key` (DOM reconciliation identity) -- not a data value rendered to
    the user.

(components/ui/sidebar.tsx's skeleton-loader-width entry was removed from the
allowlist below -- that file no longer exists in this repo.)
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
FRONTEND_SRC: Final[Path] = REPO_ROOT / "frontend" / "src"

#: Patterns that, in combination with rendering something as if it were real
#: data, indicate a hash/PRNG-derived stand-in value. Matched line-by-line,
#: same as the frozen-test guard.
_SUSPECT_PATTERNS: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(p)
    for p in (
        r"charCodeAt",  # the classic string -> pseudo-hash -> "random-looking" value trick
        r"hashCode",
        r"Math\.sin\(",  # a common fake-PRNG (sin of a large seed) idiom
        r"seedrandom",
        r"\bFNV\b",
        r"0x811c9dc5",  # FNV-1a offset basis, a recognisable fingerprint of that trick
        r"Math\.random\(\)",
    )
)

#: (relative path, line number, reason) -- reviewed exceptions, not a blanket
#: file exemption. Any new match anywhere else fails the test.
#:
#: Deliberately EMPTY. The single entry this ever held was the PRNG-derived
#: React list key in quote-drawer's newVesselDraft(); that is now a monotonic
#: counter, which is both a better key (uniqueness guaranteed rather than
#: merely likely) and no longer a match for any suspect pattern.
#:
#: Keep it empty if you can. Pinning an exception by LINE NUMBER means any
#: edit above it breaks the build on BOTH tests below -- the offender scan and
#: the stale-entry scan -- for a reason that has nothing to do with synthetic
#: data. That happened three separate times while the frontend was being
#: reworked. If a genuine non-data use ever needs an exception, prefer
#: removing the pattern (as here) over recording its coordinates.
_ALLOWLIST: Final[frozenset[tuple[str, int]]] = frozenset()


def _frontend_source_files() -> list[Path]:
    if not FRONTEND_SRC.exists():
        return []
    return sorted(
        p for p in FRONTEND_SRC.rglob("*")
        if p.suffix in (".ts", ".tsx") and "node_modules" not in p.parts
    )


def test_no_hash_or_prng_derived_value_outside_the_reviewed_allowlist() -> None:
    offenders: list[tuple[Path, int, str]] = []
    for path in _frontend_source_files():
        rel = path.relative_to(FRONTEND_SRC).as_posix()
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if (rel, lineno) in _ALLOWLIST:
                continue
            if any(pat.search(line) for pat in _SUSPECT_PATTERNS):
                offenders.append((path, lineno, line.strip()))

    if offenders:
        detail = "\n".join(
            f"  {p.relative_to(REPO_ROOT)}:{ln}: {content}" for p, ln, content in offenders
        )
        raise AssertionError(
            "Found a hash/PRNG-derived pattern in frontend/src/ outside the "
            "reviewed allowlist -- if this is a genuine synthetic-data "
            "placeholder, replace it with a real value or an explicit "
            "not-available state; if it is a reviewed, non-data use (a DOM "
            "key, a cosmetic skeleton size), add it to _ALLOWLIST with a "
            "reason:\n" + detail
        )


def test_allowlist_entries_still_exist_and_still_match() -> None:
    """Catches a stale allowlist entry -- a line number that drifted after an
    edit, or a pattern that no longer appears there at all."""
    for rel, lineno in _ALLOWLIST:
        path = FRONTEND_SRC / rel
        assert path.exists(), f"allowlisted file no longer exists: {rel}"
        lines = path.read_text(encoding="utf-8").splitlines()
        assert 0 < lineno <= len(lines), f"{rel}:{lineno} is out of range -- the file changed, update the allowlist"
        line = lines[lineno - 1]
        assert any(pat.search(line) for pat in _SUSPECT_PATTERNS), (
            f"{rel}:{lineno} no longer matches a suspect pattern -- the allowlist entry is stale, remove it"
        )
