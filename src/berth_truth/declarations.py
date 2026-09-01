"""Dhamra's Monthly Draft Declaration: parsing, and the strict validity rule.

Dhamra's BPTS states its berths' permissible draft is "promulgated on monthly
basis and issued to Trade" -- not in the BPTS itself, in a separate document.
The canonical URL for that document
(adaniports.com/.../dhamra-port/documents/monthly-draft-declaration.pdf) was
fetched twice for this project, a session apart: both times it served the
*same* 71,257-byte file, and that file's own content is entirely December
2017 -- 31 daily rows, all reading 17.20m, titled "MAX. SW ARRIVAL DRAFT AT
DHAMRA PORT BB1 & BB2 IMPORT BERTH". Whatever the URL's freshness implies,
the document behind it has not changed in at least the time between those two
fetches, and its content is nearly a decade old. That is the entire reason
:func:`resolve_draft` exists as a strict function rather than "use whatever
the file says": a naive reader would silently answer a 2026 feasibility
question with a 2017 number.

Text extraction (PDF bytes -> plain text) is deliberately kept out of this
module's hard dependencies -- see :func:`load_declaration_text`. The parsing
logic below (:func:`parse_declaration_text`) takes plain text and needs
nothing beyond the standard library; it is tested against
tests/berth_truth/fixtures/dhamra_monthly_draft_declaration_2026-08-27.txt,
which is the real, complete text of that document, captured verbatim
(including its literal Unicode hyphens, U+2010, in every date -- a real
artifact of the PDF's own encoding, not a fixture typo).
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Final

from berth_truth.models import DraftDeclaration, DraftResolution, DraftStatus, PortId

__all__ = [
    "load_declaration_text",
    "parse_declaration_text",
    "resolve_draft",
]

# PDF text extraction commonly yields a typographic hyphen/dash rather than
# ASCII '-' for a rendered '-' glyph -- confirmed in the real document, where
# every date reads e.g. "1‐Dec‐17" with U+2010 (HYPHEN), not U+002D
# (HYPHEN-MINUS). Normalising the handful of lookalikes is the alternative to
# writing a date regex that tries to enumerate them all.
_HYPHEN_LIKE: Final[str] = "‐‑‒–—"
_HYPHEN_NORMALISE: Final[dict[int, int]] = {ord(c): ord("-") for c in _HYPHEN_LIKE}

_DAILY_ROW_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?P<day>\d{1,2})-(?P<mon>[A-Za-z]{3})-(?P<yr>\d{2})\s+(?P<value>\d+(?:\.\d+)?)\s*$"
)
_SUMMARY_ROW_RE: Final[re.Pattern[str]] = re.compile(r"^(MAX|MIN)\s+IN\s+MONTH\b", re.IGNORECASE)
# The document's own title line, e.g. "MAX.  SW ARRIVAL DRAFT AT DHAMRA PORT
# BB1 & BB2 IMPORT BERTH" -- whitespace-normalised before matching. Berth
# identifiers are pulled generically from whatever sits between "PORT" and
# "BERTH", not hardcoded to "BB1 & BB2": a future month's file scoped to a
# different berth set must still parse correctly.
_TITLE_RE: Final[re.Pattern[str]] = re.compile(
    r"ARRIVAL\s+DRAFT\s+AT\s+\S+\s+PORT\s+(?P<scope>.+?)\s+(?:IMPORT|EXPORT)?\s*BERTH",
    re.IGNORECASE,
)
_BERTH_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"\b[A-Z]{2,5}\d[A-Z0-9]*\b")


def load_declaration_text(pdf_path: Path) -> str:
    """Extract plain text from a Monthly Draft Declaration PDF.

    Not a hard dependency of this package -- ``pyproject.toml`` was not
    extended to add a PDF library for this task, so this function does a
    lazy import and fails with a clear, actionable message if none is
    available. Every other function in this module works from plain text
    and needs this only as a convenience on a machine that happens to have
    ``pypdf`` installed; it is not exercised by the test suite, which parses
    the real captured text directly.
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError(
            "load_declaration_text needs a PDF text extraction library (e.g. "
            "`pip install pypdf`), which is not a declared project dependency. "
            "Extract the text some other way and call parse_declaration_text "
            "directly, or install pypdf in your own environment."
        ) from exc
    reader = PdfReader(str(pdf_path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def parse_declaration_text(
    text: str,
    *,
    port_id: PortId,
    source_url: str,
    retrieved_at: date,
) -> tuple[DraftDeclaration, ...]:
    """Parse one Monthly Draft Declaration's extracted text into rows.

    Skips the MAX/MIN summary lines (real, present in every document of this
    shape so far, and not a daily value). Text with no daily rows at all
    (including empty text) returns an empty tuple -- there is nothing to
    misattribute. But text that DOES contain daily rows and no title line
    raises ``ValueError``: that combination means real numbers exist with no
    citable berth_scope for them, and guessing one would be exactly the kind
    of silent inference this module exists to avoid. The check is ordered
    this way (parse rows first, validate scope only if rows were found) so
    that a garbled or truncated fetch degrades to "no data", not a crash, if
    it happens to contain no numbers either.
    """
    normalised_lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]

    daily_matches: list[tuple[str, str, str, str]] = []
    for line in normalised_lines:
        if not line or _SUMMARY_ROW_RE.match(line):
            continue
        match = _DAILY_ROW_RE.match(line.translate(_HYPHEN_NORMALISE))
        if match is None:
            continue  # title line, the bare month/year line, blanks -- not a daily row
        daily_matches.append(match.group("day", "mon", "yr", "value"))

    if not daily_matches:
        return ()

    title_line = next((ln for ln in normalised_lines if _TITLE_RE.search(ln)), None)
    if title_line is None:
        raise ValueError(
            "no title line found (expected something like 'MAX. SW ARRIVAL DRAFT AT "
            "<PORT> PORT <BERTHS> BERTH') -- refusing to guess a berth_scope for "
            f"{len(daily_matches)} daily row(s) that were otherwise parsed"
        )
    scope_text = _TITLE_RE.search(title_line).group("scope")  # type: ignore[union-attr]
    berth_scope = tuple(_BERTH_TOKEN_RE.findall(scope_text))
    if not berth_scope:
        raise ValueError(f"title line found but no berth identifiers extracted from it: {title_line!r}")

    rows: list[DraftDeclaration] = []
    for day, mon, yr, value in daily_matches:
        # Naive by construction: the document states a calendar date only, no
        # timezone -- same reasoning as parsers.adani_schedule.parse_timestamp
        # -- and .date() discards any time-of-day component immediately
        # regardless, so a tzinfo here could not survive to influence anything.
        row_date = datetime.strptime(f"{day}-{mon}-{yr}", "%d-%b-%y").date()  # noqa: DTZ007
        rows.append(
            DraftDeclaration(
                port_id=port_id,
                berth_scope=berth_scope,
                date=row_date,
                max_sw_arrival_draft_m=float(value),
                doc_internal_date=row_date,  # placeholder, corrected below
                source_url=source_url,
                retrieved_at=retrieved_at,
            )
        )

    if not rows:
        return ()

    # doc_internal_date is "the most recent date this document's content
    # covers" -- the same for every row from one document, computed only once
    # every row's own date is known, then applied uniformly.
    doc_internal_date = max(row.date for row in rows)
    return tuple(row.model_copy(update={"doc_internal_date": doc_internal_date}) for row in rows)


def resolve_draft(
    declarations: Sequence[DraftDeclaration],
    *,
    port_id: PortId,
    berth_id: str,
    as_of: date,
) -> DraftResolution:
    """The strict validity rule.

    A declared value is returned ONLY if a row exists with ``row.date ==
    as_of`` from a document whose own ``doc_internal_date`` falls in the same
    calendar month as ``as_of``. No nearest-match, no carry-forward, no
    month-boundary tolerance -- a draft that was not declared for the date
    being asked about is not known for that date, however plausible a nearby
    value might look.

    Always resolves to DECLARED or STALE_OR_UNAVAILABLE, never
    NOT_APPLICABLE -- that status means "this berth has no declaration
    mechanism at all", which is a decision resolver.py makes from a berth's
    own draft_source *before* calling this function, not something this
    function should infer merely because the ``declarations`` it happens to
    have been handed don't include a matching row. Handed zero declarations
    for a berth that genuinely does have a daily-declaration mechanism, the
    honest answer is "unavailable right now", not "no mechanism exists".
    """
    scoped = [d for d in declarations if d.port_id is port_id and berth_id in d.berth_scope]
    if not scoped:
        return DraftResolution(
            draft_status=DraftStatus.STALE_OR_UNAVAILABLE,
            permissible_draft_m=None,
            draft_as_of=None,
            warning=(
                f"no declaration data available for berth {berth_id!r} at {port_id.value} "
                f"as of {as_of.isoformat()} -- zero matching declarations were supplied"
            ),
        )

    exact = next((d for d in scoped if d.date == as_of), None)
    if exact is not None and exact.doc_internal_date.year == as_of.year and exact.doc_internal_date.month == as_of.month:
        return DraftResolution(
            draft_status=DraftStatus.DECLARED,
            permissible_draft_m=exact.max_sw_arrival_draft_m,
            draft_as_of=as_of,
            source_doc_id=f"dhamra-monthly-draft-declaration-{exact.doc_internal_date.isoformat()}",
        )

    nearest = min(scoped, key=lambda d: abs((d.date - as_of).days))
    gap_days = abs((as_of - nearest.doc_internal_date).days)
    return DraftResolution(
        draft_status=DraftStatus.STALE_OR_UNAVAILABLE,
        permissible_draft_m=None,
        draft_as_of=None,
        source_doc_id=f"dhamra-monthly-draft-declaration-{nearest.doc_internal_date.isoformat()}",
        warning=(
            f"declaration for {berth_id} exists, but the document available "
            f"(internally dated {nearest.doc_internal_date.isoformat()}) does not cover "
            f"{as_of.isoformat()} -- {gap_days} day(s) apart from that document's own date"
        ),
    )
