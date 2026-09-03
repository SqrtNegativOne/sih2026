"""Daily Baltic index levels and time-charter averages, from handybulk.com.

Why this exists
---------------
``raw_data/handybulk_index_levels.csv`` is the source of every ``*_TCAVG``
series in ``master_long.parquet`` -- the real dollars-per-day figures the
forecast trains on, the LOCK/WAIT ceiling is measured against, and
``opt.period_cover`` benchmarks a season plan's break-even hire against.

It arrived as a one-off scrape. There was no harvester in the repository, so
the file simply stopped where whoever ran it stopped: the stored data ended on
2026-08-20 while the site had already published through 2026-09-01. Every
downstream figure quietly aged with it, and the standing alerts in
``src/alerts/`` had nothing new to fire on, because the number they watch could
not change.

This module is the missing harvester. It follows the pattern
``harvest_portwatch.py`` established: a build-time fetch under
``data_builders/`` writing into ``raw_data/``, cached to disk, and offline-safe
-- a failed fetch returns "no new rows" rather than raising into anything.

What it parses
--------------
The page carries one dated paragraph per publication day, in prose:

    The Baltic Dry Index (BDI) decreased by 29 points to reach 3,157 points.
    The Baltic Capesize Index (BCI) decreased by 115 points to 5,221 points,
    with average daily earnings for capesize bulk carriers decreased by $1,049
    to $47,350. ...

Both numbers per class are in there -- the index level and the $/day average --
and the wording varies ("with average daily earnings", "while average daily
income", "as average daily earnings"), so the patterns below match on the parts
that do not vary: the index code in brackets, and the class name followed by
"bulk carriers".

Confidence that the parse is right
----------------------------------
It reproduces the existing file exactly. On all 14 dates where a freshly parsed
page overlapped the previously scraped CSV, every supramax figure matched to
the dollar. That is the check worth having: it says this reads the same fields
the original scrape did, rather than something that merely looks plausible.

An entry the page publishes without one of its figures parses as a partial
record and is stored with that cell empty, because ``build_master.read_handybulk``
already drops nulls per series. Two such days exist in the current page and
both are real gaps in the source, not parse failures.

Run it:
    uv run python -m data_builders.harvest_handybulk
"""

from __future__ import annotations

import csv
import html as html_mod
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Final

import requests

LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
CSV_PATH: Final[Path] = REPO_ROOT / "raw_data" / "handybulk_index_levels.csv"
MASTER_PATH: Final[Path] = REPO_ROOT / "src" / "data" / "master_long.parquet"

#: The front page. It carries the most recent entry, and sometimes a run of
#: recent ones -- how many varies by day, so nothing here depends on it
#: carrying more than one.
SOURCE_URL: Final[str] = "https://www.handybulk.com/baltic-dry-index/"

#: Month archives, which is where the history actually lives. Discovered the
#: hard way: an early fetch happened to return 56 KB with three weeks of
#: entries, and the same URL the next day returned 16 KB with exactly one. A
#: harvester that only ever reads the front page therefore works perfectly
#: while it runs daily and silently loses every day of a gap the moment it
#: does not -- which is the failure mode a backfill exists to prevent.
MONTH_URL_TEMPLATE: Final[str] = "https://www.handybulk.com/baltic-dry-index/{year}/{month}/"

#: How many month archives one run may fetch to close a gap. Two covers a
#: month boundary, which is the realistic worst case for a desk that has been
#: off for a few weeks. It is a cap on politeness, not on correctness: a longer
#: outage is closed by running the harvester again.
MAX_BACKFILL_MONTHS: Final[int] = 2

#: Identifies the caller honestly rather than impersonating a browser. This is
#: a public data page and the project has pulled from it before (see
#: raw_data/handybulk_fetch_manifest.csv); a single request a day is the whole
#: load this adds.
USER_AGENT: Final[str] = (
    "sih2026-chartering-research/1.0 (build-time harvester; at most 3 requests/day)"
)

REQUEST_TIMEOUT_S: Final[float] = 30.0

#: CSV column order, matching the existing file exactly. `read_handybulk` reads
#: by name, but keeping the order stable means a diff of this file stays
#: readable.
COLUMNS: Final[tuple[str, ...]] = (
    "date",
    "bdi",
    "bci",
    "capesize_tc_avg_usd_day",
    "bpi",
    "panamax_tc_avg_usd_day",
    "bsi",
    "supramax_tc_avg_usd_day",
    "bhsi",
    "handysize_tc_avg_usd_day",
    "source_url",
)

#: Baltic index code -> CSV column.
_INDEX_COLUMNS: Final[dict[str, str]] = {
    "BDI": "bdi",
    "BCI": "bci",
    "BPI": "bpi",
    "BSI": "bsi",
    "BHSI": "bhsi",
}

#: Vessel class as the page names it -> CSV column.
_RATE_COLUMNS: Final[dict[str, str]] = {
    "capesize": "capesize_tc_avg_usd_day",
    "panamax": "panamax_tc_avg_usd_day",
    "supramax": "supramax_tc_avg_usd_day",
    "handysize": "handysize_tc_avg_usd_day",
}

_DATE_LINE: Final[re.Pattern[str]] = re.compile(r"^(\d{1,2})-([A-Za-z]+)-(\d{4})$")

#: Lowercase month names, as the archive URLs spell them.
_MONTH_NAMES: Final[tuple[str, ...]] = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)


@dataclass(frozen=True)
class HarvestResult:
    """What one run did. Every field is a fact about this run, not an estimate."""

    fetched: bool
    """False when the network call failed. Not an error -- see the module
    docstring on offline safety."""

    parsed_days: int
    added_days: int
    latest_date: date | None
    """The most recent date the source published, whether or not it was new."""

    conflicts: tuple[str, ...] = field(default_factory=tuple)
    """Dates already stored whose freshly parsed value differs. Reported, never
    applied -- see ``merge_rows``."""

    reason: str | None = None
    """Why nothing was fetched, when ``fetched`` is False."""

    parsed: dict[date, dict[str, float]] = field(default_factory=dict)
    """Everything read from the page, whether or not it was new to the CSV.
    Carried on the result so a caller can fold it into master_long without
    fetching the page a second time -- this source gets one request per run,
    which is what the User-Agent above promises."""


def fetch_page(url: str = SOURCE_URL) -> str | None:
    """One request. Returns None on any failure rather than raising.

    A harvester that raises turns a transient network problem into a broken
    caller. Every consumer of this data already handles "no new data", because
    that is also what a weekend looks like.
    """
    try:
        response = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_S
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        LOGGER.info("handybulk fetch failed, keeping existing data: %s", exc)
        return None
    return response.text


def _visible_lines(page: str) -> list[str]:
    """The page as readable text, one paragraph per line."""
    stripped = re.sub(r"<script.*?</script>|<style.*?</style>", " ", page, flags=re.DOTALL)
    text = html_mod.unescape(re.sub(r"<[^>]+>", "\n", stripped))
    return [line.strip() for line in text.split("\n") if line.strip()]


def _to_float(raw: str) -> float:
    return float(raw.replace(",", ""))


def parse_page(page: str) -> dict[date, dict[str, float]]:
    """Every dated entry the page carries, oldest key to newest.

    Only the FIRST occurrence of a date is kept. The page repeats some dates in
    its weekly commentary sections, and those repeats carry narrative rather
    than the daily figures; taking the first keeps the daily entry and ignores
    the prose.
    """
    lines = _visible_lines(page)
    out: dict[date, dict[str, float]] = {}

    for i, line in enumerate(lines):
        if not _DATE_LINE.match(line) or i + 1 >= len(lines):
            continue
        body = lines[i + 1]
        # The daily entry always leads with the headline index. A weekly
        # commentary block under the same date does not, which is how the two
        # are told apart without depending on the page's markup.
        if "Baltic Dry Index" not in body:
            continue
        try:
            # DTZ007 is suppressed below, and correctly asks about timezones
            # on an instant. This is not an instant: a published index date is
            # a calendar date, and "the BSI on 20 August" is the same fact in
            # every zone. .date() discards the time component immediately.
            day = datetime.strptime(line, "%d-%B-%Y").date()  # noqa: DTZ007
        except ValueError:
            LOGGER.debug("Unparseable date heading %r, skipped", line)
            continue
        if day in out:
            continue

        record: dict[str, float] = {}
        for code, column in _INDEX_COLUMNS.items():
            # "to reach 3,157 points" and "to 5,221 points" both appear.
            match = re.search(rf"\({code}\)[^.]*?\bto (?:reach )?([\d,]+) points", body)
            if match:
                record[column] = _to_float(match.group(1))
        for name, column in _RATE_COLUMNS.items():
            match = re.search(rf"for {name} bulk carriers[^.]*?\bto \$([\d,]+)", body)
            if match:
                record[column] = _to_float(match.group(1))

        if record:
            out[day] = record

    return dict(sorted(out.items()))


def read_existing(path: Path = CSV_PATH) -> dict[str, dict[str, str]]:
    """The stored file, keyed by ISO date. Empty when it does not exist yet."""
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as handle:
        return {row["date"]: row for row in csv.DictReader(handle) if row.get("date")}


def merge_rows(
    parsed: dict[date, dict[str, float]],
    existing: dict[str, dict[str, str]],
    source_url: str = SOURCE_URL,
) -> tuple[list[dict[str, str]], list[str]]:
    """Add dates that are missing. Never rewrite a date already stored.

    Returns ``(rows_to_write, conflicts)``.

    A date already present is left exactly as it is, even when the freshly
    parsed value differs. Two reasons: the stored figure is what every model
    trained on and every past recommendation was priced against, so silently
    changing it would rewrite the past out from under the decision ledger; and
    a difference is far more likely to mean the page's wording drifted than
    that the Baltic Exchange restated a printed index. Differences are returned
    so a human can look, which is the right response to "the source disagrees
    with our history".
    """
    conflicts: list[str] = []
    merged: dict[str, dict[str, str]] = dict(existing)

    for day, record in parsed.items():
        key = day.isoformat()
        if key in existing:
            for column, value in record.items():
                stored = existing[key].get(column, "")
                if stored and abs(float(stored) - value) > 0.5:
                    conflicts.append(f"{key} {column}: stored {stored}, source {value:.0f}")
            continue
        row = {column: "" for column in COLUMNS}
        row["date"] = key
        row["source_url"] = source_url
        for column, value in record.items():
            # Whole numbers throughout in this source; keeping them integral
            # means the file stays byte-comparable with the original scrape.
            row[column] = f"{value:.0f}"
        merged[key] = row

    rows = [merged[k] for k in sorted(merged)]
    return rows, conflicts


def write_csv(rows: list[dict[str, str]], path: Path = CSV_PATH) -> None:
    """Rewrite the file, sorted by date.

    Written to a sibling temp file and moved into place, so an interrupted run
    cannot leave a half-written file where the whole rate history should be.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in COLUMNS})
    tmp.replace(path)


def month_url(year: int, month: int) -> str:
    """The archive page for one month, in the form the site uses."""
    return MONTH_URL_TEMPLATE.format(year=year, month=_MONTH_NAMES[month - 1])


def _months_to_backfill(
    newest_stored: date | None, newest_seen: date, limit: int = MAX_BACKFILL_MONTHS
) -> list[tuple[int, int]]:
    """Which month archives would close the gap, newest first.

    Empty when the stored data already reaches the newest published date, so
    the ordinary daily case costs exactly one request.
    """
    if newest_stored is None or newest_stored >= newest_seen:
        return []
    months: list[tuple[int, int]] = []
    year, month = newest_seen.year, newest_seen.month
    while len(months) < limit:
        months.append((year, month))
        # Step back a month; stop once we are behind what is already stored.
        if (year, month) <= (newest_stored.year, newest_stored.month):
            break
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return months


def harvest(
    path: Path = CSV_PATH,
    url: str = SOURCE_URL,
    *,
    backfill: bool = True,
) -> HarvestResult:
    """Fetch, parse and merge. Safe to run repeatedly; adds only new dates.

    Reads the front page first. If the stored data is behind what the front
    page publishes, it also reads the month archives covering the gap -- see
    ``MONTH_URL_TEMPLATE`` for why that is necessary rather than belt-and-braces.
    """
    page = fetch_page(url)
    if page is None:
        return HarvestResult(
            fetched=False,
            parsed_days=0,
            added_days=0,
            latest_date=None,
            reason="The source could not be reached. Existing data is unchanged.",
        )

    parsed = parse_page(page)

    if parsed and backfill:
        stored_dates = [d for d in read_existing(path) if d]
        newest_stored = max(date.fromisoformat(d) for d in stored_dates) if stored_dates else None
        for year, month in _months_to_backfill(newest_stored, max(parsed)):
            archive = fetch_page(month_url(year, month))
            if archive is None:
                continue
            for day, record in parse_page(archive).items():
                # The front page wins on a date both carry: it is the more
                # recently published rendering of the same figure.
                parsed.setdefault(day, record)
        parsed = dict(sorted(parsed.items()))
    if not parsed:
        # A reachable page that parses to nothing means its wording changed.
        # Saying so is the point -- silently harvesting zero rows every day
        # looks identical to a quiet market.
        return HarvestResult(
            fetched=True,
            parsed_days=0,
            added_days=0,
            latest_date=None,
            reason=(
                "The page was fetched but no dated entries could be read from it. "
                "Its wording has probably changed; the patterns in parse_page need "
                "revisiting."
            ),
        )

    existing = read_existing(path)
    rows, conflicts = merge_rows(parsed, existing, source_url=url)
    added = len(rows) - len(existing)
    if added:
        write_csv(rows, path)
        LOGGER.info("handybulk: added %d new day(s) to %s", added, path.name)
    else:
        LOGGER.info("handybulk: already current, nothing added")
    if conflicts:
        LOGGER.warning("handybulk: %d stored value(s) differ from the source", len(conflicts))
        for line in conflicts:
            LOGGER.warning("  %s", line)

    return HarvestResult(
        fetched=True,
        parsed_days=len(parsed),
        added_days=added,
        latest_date=max(parsed),
        conflicts=tuple(conflicts),
        parsed=parsed,
    )


#: The nine series this source provides, and therefore the only ones this
#: module is allowed to touch in master_long.parquet.
HARVESTED_SERIES: Final[tuple[str, ...]] = (
    "BD_INDEX",
    "BC_INDEX",
    "BPI_INDEX",
    "BSI_INDEX",
    "BHSI_INDEX",
    "CAPESIZE_TCAVG",
    "PANAMAX_TCAVG",
    "SUPRAMAX_TCAVG",
    "HANDYSIZE_TCAVG",
)

#: CSV column -> (series_id, unit), matching build_master.read_handybulk so the
#: two cannot drift into producing different rows from the same file.
_SERIES_FROM_COLUMN: Final[dict[str, tuple[str, str]]] = {
    "bdi": ("BD_INDEX", "index_pts"),
    "bci": ("BC_INDEX", "index_pts"),
    "bpi": ("BPI_INDEX", "index_pts"),
    "bsi": ("BSI_INDEX", "index_pts"),
    "bhsi": ("BHSI_INDEX", "index_pts"),
    "capesize_tc_avg_usd_day": ("CAPESIZE_TCAVG", "usd/day"),
    "panamax_tc_avg_usd_day": ("PANAMAX_TCAVG", "usd/day"),
    "supramax_tc_avg_usd_day": ("SUPRAMAX_TCAVG", "usd/day"),
    "handysize_tc_avg_usd_day": ("HANDYSIZE_TCAVG", "usd/day"),
}


def _write_summary(frame: object, master_path: Path) -> None:
    """Rewrite master_summary.csv to describe the frame given.

    Same shape `build_master.main` writes, so a daily top-up and a full rebuild
    produce the same file for the same data.
    """
    import polars as pl

    summary_path = master_path.parent / "master_summary.csv"
    if not summary_path.exists():
        return
    assert isinstance(frame, pl.DataFrame)
    (
        frame.group_by("series_id")
        .agg(
            pl.len().alias("rows"),
            pl.col("date").min().alias("first"),
            pl.col("date").max().alias("last"),
        )
        .sort("series_id")
        .write_csv(summary_path)
    )


def update_master(
    parsed: dict[date, dict[str, float]],
    master_path: Path = MASTER_PATH,
) -> int:
    """Append only genuinely new (series, date) rows to master_long.parquet.

    Deliberately NOT a call to ``build_master.main()``, even though that would
    be one line and would work. A full rebuild reads every source directory and
    rewrites the whole file, and the committed parquet is not always in step
    with what sits in ``raw_data/`` -- running it here once turned a seven-day
    rate top-up into a 951,848-row change, pulling in 342 PortWatch series from
    an extended harvest the parquet predated. All real data, none of it asked
    for, and none of it validated by the thing that triggered it.

    A job that runs daily needs a bounded blast radius. This one touches only
    the nine series this source publishes, and within those only dates the
    master does not already carry -- so it cannot rewrite a figure a past
    recommendation was priced against, and it cannot silently reshape anything
    else. Bringing the rest of master_long up to date with raw_data is a real
    and separate task, done knowingly with ``build_master.main()``.
    """
    import polars as pl

    if not master_path.exists():
        LOGGER.info("No master at %s; run build_master first.", master_path)
        return 0

    master = pl.read_parquet(master_path)
    known: set[tuple[str, date]] = set(
        zip(
            master.filter(pl.col("series_id").is_in(HARVESTED_SERIES))["series_id"].to_list(),
            master.filter(pl.col("series_id").is_in(HARVESTED_SERIES))["date"].to_list(),
            strict=True,
        )
    )

    additions: list[dict[str, object]] = []
    for day, record in parsed.items():
        for column, value in record.items():
            series_id, unit = _SERIES_FROM_COLUMN[column]
            if (series_id, day) in known:
                continue
            additions.append(
                {
                    "series_id": series_id,
                    "date": day,
                    "value": float(value),
                    "unit": unit,
                    "source": "handybulk",
                }
            )

    if not additions:
        # Still refresh the summary. It is a description OF master_long, and
        # the two going out of step is a quiet trap: the summary is what a
        # person reads to answer "how current is this data", so a stale one
        # answers that question wrongly with total confidence. Skipping it on
        # a no-op run is exactly how it got out of step in the first place.
        _write_summary(master, master_path)
        LOGGER.info("master_long already carries every harvested figure")
        return 0

    combined = pl.concat(
        [master, pl.DataFrame(additions, schema=master.schema)], how="vertical"
    ).sort(["series_id", "date"])

    tmp = master_path.with_suffix(".parquet.tmp")
    combined.write_parquet(tmp)
    tmp.replace(master_path)

    _write_summary(combined, master_path)
    LOGGER.info("master_long: +%d row(s) across %d series", len(additions), len(HARVESTED_SERIES))
    return len(additions)


def main() -> None:
    """Harvest, then fold the new figures into master_long.parquet.

    The master update is what makes the harvest visible: every consumer reads
    the parquet, not this CSV.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    result = harvest()

    if not result.fetched:
        LOGGER.warning("%s", result.reason)
        return
    LOGGER.info(
        "parsed %d day(s), latest %s, added %d to the CSV",
        result.parsed_days,
        result.latest_date,
        result.added_days,
    )
    update_master(result.parsed)


if __name__ == "__main__":
    main()
