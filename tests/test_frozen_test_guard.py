"""Repo-wide static check: nothing reads the frozen test split except the guard.

This is the second half of the tripwire described in ml.frozen_test. The runtime
guard (tests/ml/test_frozen_test.py) stops an ungated *call* to load_frozen_test()
from succeeding; this test stops a *new* ungated call to ``load_split("test")``
from ever being written in the first place, by failing CI the moment one appears
anywhere in the source tree outside the one sanctioned call site.

A plain grep rather than an AST walk is deliberate: the thing being guarded against
is exactly the textual pattern ``load_split("test")`` (or the equivalent single-quoted
form), and a simple, auditable regex is easier to trust than a parser that could itself
have false negatives.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
SRC: Final[Path] = REPO_ROOT / "src"

#: The one function allowed to actually load the test split by name.
_CALL_PATTERN: Final[re.Pattern[str]] = re.compile(r"""load_split\(\s*['"]test['"]\s*\)""")

#: Files permitted to contain that call. Anything added here should be reviewed as
#: carefully as a change to ml.frozen_test itself.
ALLOWLIST: Final[frozenset[Path]] = frozenset(
    {SRC / "ml" / "frozen_test.py"}
)


def _all_python_files() -> list[Path]:
    files: list[Path] = []
    for root in (SRC, REPO_ROOT):
        for p in root.rglob("*.py"):
            if ".venv" in p.parts or "__pycache__" in p.parts:
                continue
            if "tests" in p.parts:
                continue
            files.append(p)
    # REPO_ROOT.rglob also walks SRC; de-duplicate.
    return sorted(set(files))


def test_no_file_outside_the_guard_calls_load_split_test_directly() -> None:
    offenders: list[tuple[Path, int, str]] = []
    for path in _all_python_files():
        if path.resolve() in {p.resolve() for p in ALLOWLIST}:
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _CALL_PATTERN.search(line):
                offenders.append((path, lineno, line.strip()))

    if offenders:
        detail = "\n".join(
            f"  {p.relative_to(REPO_ROOT)}:{ln}: {content}" for p, ln, content in offenders
        )
        raise AssertionError(
            "Found load_split(\"test\") outside the frozen-test guard. Route this "
            "through ml.frozen_test.load_frozen_test() inside an "
            "allow_test_set_access(...) block instead:\n" + detail
        )


def test_allowlist_entries_still_exist() -> None:
    """Catches a stale allowlist entry if ml.frozen_test is ever moved or renamed."""
    for path in ALLOWLIST:
        assert path.exists(), f"allowlisted path no longer exists: {path}"


def test_the_guard_module_actually_contains_the_call() -> None:
    """The allowlist should not silently become dead weight.

    If ml.frozen_test stops calling load_split("test") -- e.g. it starts reading the
    parquet file directly -- the allowlist entry above is no longer doing anything,
    which usually means the guard itself changed in a way worth a second look.
    """
    guard_path = SRC / "ml" / "frozen_test.py"
    text = guard_path.read_text(encoding="utf-8")
    assert _CALL_PATTERN.search(text), (
        "ml.frozen_test no longer calls load_split(\"test\") -- update this test "
        "(and re-check the guard) if the loading mechanism changed intentionally."
    )
