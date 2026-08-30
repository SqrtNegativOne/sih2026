"""P6 requirement: a test asserting ``src/impact/`` is not referenced by
product claims.

P3's identification gate found ``tonnage.stockflow``'s ``stock_dwt`` to be
``IndexType.RELATIVE``, not ``ABSOLUTE``, and its sign-diagnosis toolkit found
``tonnage.supplycurve``'s rate~tightness slope wrong-signed (Capesize,
Handysize) or regime-unstable (Panamax, Supramax) for every vessel class --
see ``src/impact/__init__.py`` for the precise, cited findings. Per the P6
prompt's own explicit branching ("if the rate~tightness elasticity does not
validate -> mark src/impact/ experimental... this is the expected outcome and
it is the correct one"), ``src/impact/`` stays in the tree as real, documented
code, but must not be imported by any live source package or referenced as a
shipped capability in the frontend.

Same philosophy as tests/test_no_synthetic_frontend_data.py and
tests/test_frozen_test_guard.py: a plain, auditable static scan, not a
behavioural test -- what's being guarded against (an import edge, or a
marketing claim) is a structural/textual fact, not something that needs a
live run to observe.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
SRC_ROOT: Final[Path] = REPO_ROOT / "src"
BACKEND_ROOT: Final[Path] = REPO_ROOT / "backend"
FRONTEND_SRC: Final[Path] = REPO_ROOT / "frontend" / "src"

#: Any src/ package other than impact itself, plus backend/ -- the complete
#: set of locations a real, live decision path could import impact from.
_IMPORT_PATTERN: Final[re.Pattern[str]] = re.compile(r"^\s*(?:from|import)\s+impact(?:\.\w+)?\b")

#: Words that, if they appear in shipped frontend source, would read as a
#: product claim about this experimental capability. Case-sensitive on
#: purpose -- "impact" alone is far too common an English word in UI copy
#: (e.g. "market impact" as a plain-English phrase) to grep safely; the
#: distinguishing terms are the specific technical names.
_PRODUCT_CLAIM_PATTERNS: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(p)
    for p in (
        r"[Ee]xecution [Ff]rontier",
        r"execution-frontier",
        r"[Aa]lmgren",
        r"impact\.elasticity",
        r"impact\.execution",
        r"impact\.fixedpoint",
        r"impact/elasticity",
        r"impact/execution",
        r"impact/fixedpoint",
    )
)


def _python_source_files(root: Path, *, exclude: Path | None = None) -> list[Path]:
    if not root.exists():
        return []
    files = []
    for p in root.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        if exclude is not None and exclude in p.parents:
            continue
        files.append(p)
    return sorted(files)


def _frontend_source_files() -> list[Path]:
    if not FRONTEND_SRC.exists():
        return []
    return sorted(
        p for p in FRONTEND_SRC.rglob("*")
        if p.suffix in (".ts", ".tsx") and "node_modules" not in p.parts
    )


def test_no_live_src_package_imports_impact() -> None:
    """The definitive "is it wired in" check: no file under src/ (other than
    impact/ itself) or backend/ imports from the impact package."""
    impact_pkg = SRC_ROOT / "impact"
    candidates = _python_source_files(SRC_ROOT, exclude=impact_pkg) + _python_source_files(BACKEND_ROOT)

    offenders: list[tuple[Path, int, str]] = []
    for path in candidates:
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _IMPORT_PATTERN.match(line):
                offenders.append((path, lineno, line.strip()))

    if offenders:
        detail = "\n".join(f"  {p.relative_to(REPO_ROOT)}:{ln}: {content}" for p, ln, content in offenders)
        raise AssertionError(
            "src/impact/ is marked EXPERIMENTAL and must not be imported by any "
            "live src/ package or backend/ module (see src/impact/__init__.py "
            "for why). If this import is deliberate, the P6 experimental "
            "marking decision needs to be revisited first, not this test:\n" + detail
        )


def test_frontend_makes_no_claim_about_impact_or_execution_frontier() -> None:
    """No shipped UI screen, label, or copy references the experimental
    package's specific technical vocabulary."""
    offenders: list[tuple[Path, int, str]] = []
    for path in _frontend_source_files():
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if any(pat.search(line) for pat in _PRODUCT_CLAIM_PATTERNS):
                offenders.append((path, lineno, line.strip()))

    if offenders:
        detail = "\n".join(f"  {p.relative_to(REPO_ROOT)}:{ln}: {content}" for p, ln, content in offenders)
        raise AssertionError(
            "frontend/src/ references src/impact/'s experimental vocabulary "
            "(Almgren-Chriss / execution frontier) as if it were a shipped "
            "capability -- either remove the reference or, if impact/ has "
            "since been validated and formally reconnected, delete this "
            "test's expectation deliberately:\n" + detail
        )


def test_impact_package_docstring_states_experimental_status() -> None:
    """A cheap tripwire against the __init__.py docstring silently drifting
    back to an unqualified "MOAT 2" claim without this test file being
    updated to match."""
    init_text = (SRC_ROOT / "impact" / "__init__.py").read_text(encoding="utf-8")
    assert "EXPERIMENTAL" in init_text
    assert "NOT CONNECTED" in init_text
