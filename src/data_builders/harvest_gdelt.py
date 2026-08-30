"""Weekly conflict-intensity series per chokepoint, from GDELT -- a LEADING
indicator alongside PortWatch's lagging transit counts (``opt.risk``'s
``chokepoint_disruption_alert`` flags a transit-count DROP that already
happened; this harvester counts real news coverage of assault/fight/
unconventional-mass-violence events geolocated at each chokepoint, which
shows up in coverage before it shows up in a monthly transit average -- a
blockade, a strike, a drought-driven transit-slot auction, a missile attack
all get reported before the ships stop moving).

Chokepoint geometry: read directly from ``opt.chokepoints`` (built for the
per-route chokepoint-detection feature) rather than redefined here -- one
source of truth for where each of the 28 PortWatch chokepoint ids actually
is. This makes ``data_builders`` depend on ``opt`` for this one module,
backwards from the usual "opt consumes what data_builders built" direction
-- a deliberate exception: chokepoint geometry is domain reference data, not
itself built from a raw harvest, and there is no reason for GDELT and
PortWatch to disagree about where "chokepoint6" is. Confirmed no import
cycle: ``opt.chokepoints`` imports only ``opt.network``, never this module.

Two access paths, evaluated for real before choosing
--------------------------------------------------------
(a) GDELT DOC 2.0 API (``api.gdeltproject.org/api/v2/doc/doc``): full-text
    search over GDELT-monitored news articles, with volume/tone timeline
    modes. Two real problems for this use case, not hypothetical: it has NO
    native geographic radius filter (only place-NAME keyword search, which
    both over-matches -- any article mentioning "Hormuz" in passing, not
    just ones describing an event located there -- and under-matches --
    a real event at Hormuz's exact coordinates reported without naming the
    strait), and it has no CAMEO event-type filter at all (it searches
    article text/metadata, not GDELT's separately-coded structured Event
    records, so there is no way to ask for just assault/fight/unconventional
    -mass-violence). It also turned out to be genuinely unreachable from
    this environment when tried directly (``curl`` to ``api.gdeltproject.org``
    hangs at the TLS handshake and times out; ``data.gdeltproject.org``,
    below, works immediately) -- a real, confirmed finding, not assumed.
(b) The raw GDELT Event Database, filtered on CAMEO root codes 18/19/20
    (Assault, Fight, Unconventional Mass Violence) plus the real per-event
    ``ActionGeo_Lat``/``ActionGeo_Long`` columns -- exactly what this
    analysis needs: real geocoding to test against ``opt.chokepoints``'
    real circles (reusing its own ``_haversine_nm``, not a second distance
    implementation that could quietly disagree), and real CAMEO codes to
    filter on. **Not the 15-minute update files** the task named, though --
    a 12-month backfill at 15-minute granularity is 35,040 files (at a
    polite 0.4 s delay, ~3.9 hours of network waits alone, before a single
    byte is parsed). GDELT separately publishes a DAILY aggregate of the
    identical schema at ``data.gdeltproject.org/events/YYYYMMDD.export.CSV.zip``
    -- confirmed live (downloaded and column-verified against a real file,
    not assumed from documentation): 58 tab-separated columns, the full
    GDELT 2.0 Event Database schema (``EventRootCode`` at index 28,
    ``AvgTone`` at 34, ``ActionGeo_Lat``/``ActionGeo_Long`` at 53/54,
    ``SOURCEURL`` present at 57), reachable for dates from well before this
    project's window up to yesterday. 365 files/year instead of 35,040, same
    completeness (a full day's events, not a sample), same schema richness
    -- the practical realization of "the raw Events CSV export files"
    option, not a silent downgrade to a different/older dataset.

Honesty requirements
---------------------
GDELT counts MEDIA COVERAGE, not events. Coverage volume is driven by news
attention as much as by ground truth -- a well-covered region (a major
strait near a large media market) generates more matching articles than a
poorly-covered one for the same real incident rate. This is exactly why a
consumer of this table (chunk 3.3) MUST z-score each chokepoint against ITS
OWN history and never compare raw event_count across chokepoints -- a
cross-chokepoint comparison here would measure media-market size, not
relative risk.

Provenance: the raw counts/tone/article numbers here are OBSERVED (the
matching articles genuinely exist, geolocated and coded by GDELT's own
pipeline). Any DISRUPTION SCORE a consumer derives from z-scoring this table
against its own history is MODEL_DERIVED -- never inherits this table's own
OBSERVED label. See ``data_builders.provenance``.

Output
------
``raw_data/gdelt/chokepoint_conflict_weekly.csv``, one row per
(chokepoint, ISO week) with at least one processed day:
``chokepoint_id, iso_year, iso_week, event_count, avg_tone,
n_source_articles, retrieved_at``. ``avg_tone`` is the unweighted mean of
each MATCHED event's own real ``AvgTone`` (GDELT's own document tone score
for the articles behind that event) -- never a sentiment model invented
here. ``n_source_articles`` is GDELT's own ``NumArticles`` (total article
count backing each matched event), summed across every matched event for
that chokepoint/week -- distinct from ``NumSources`` (distinct source
domains) and ``NumMentions`` (total mentions including re-reports); this is
the closest real GDELT field to what "number of source articles" literally
asks for. A (chokepoint, week) with zero matching events still gets a row
(``event_count=0``, ``avg_tone`` blank -- there is nothing to average, an
honest gap, not a fabricated 0.0) so a consumer can tell "checked, nothing
found" apart from "not harvested yet."

Incremental and checkpointed: a whole ISO week is skipped (no requests at
all) once ANY row for it already exists in the output CSV -- every week's
28 rows are appended together, once, after all of that week's real days
have been fetched and aggregated, so an interruption mid-week loses at most
the in-progress week, never a previously-completed one.

Runnable as a script:
    python -m data_builders.harvest_gdelt [--start YYYY-MM-DD] [--end YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import csv
import io
import logging
import time
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Final

import requests

from opt.chokepoints import CHOKEPOINT_GEOMETRY, _haversine_nm

LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
RAW_DATA: Final[Path] = REPO_ROOT / "raw_data" / "gdelt"
OUT_CSV: Final[Path] = RAW_DATA / "chokepoint_conflict_weekly.csv"
PULL_NOTES_PATH: Final[Path] = RAW_DATA / "PULL_NOTES.md"

#: The daily GDELT 2.0 Event Database export -- see module docstring for why
#: this (not the 15-minute update files) is the practical realization of
#: "the raw Events CSV export files."
DAILY_EVENTS_URL_TEMPLATE: Final[str] = "https://data.gdeltproject.org/events/{date}.export.CSV.zip"
_REQUEST_TIMEOUT_SECONDS: Final[float] = 60.0

#: Matches harvest_portwatch.py's own documented pace -- no rate limiting
#: observed there at this cadence; GDELT is a free public service, so this
#: harvester is a polite client at the same rate rather than testing limits.
REQUEST_DELAY_SECONDS: Final[float] = 0.4

#: CAMEO root event codes this analysis counts: 18 Assault, 19 Fight,
#: 20 Unconventional Mass Violence -- the conflict/disruption categories
#: named in the task this harvester was built for.
CONFLICT_ROOT_CODES: Final[frozenset[str]] = frozenset({"18", "19", "20"})

# Column indices into the real GDELT 2.0 Event Database daily export,
# verified directly against a real downloaded file (data.gdeltproject.org/
# events/20240115.export.CSV.zip), not recalled from documentation alone --
# see module docstring.
_COL_EVENT_ROOT_CODE: Final[int] = 28
_COL_AVG_TONE: Final[int] = 34
_COL_NUM_ARTICLES: Final[int] = 33
_COL_ACTION_GEO_LAT: Final[int] = 53
_COL_ACTION_GEO_LONG: Final[int] = 54
_MIN_COLUMNS: Final[int] = 58

_CSV_FIELDNAMES: Final[list[str]] = [
    "chokepoint_id", "iso_year", "iso_week", "event_count", "avg_tone",
    "n_source_articles", "retrieved_at",
]


@dataclass
class _WeekAccumulator:
    event_count: int = 0
    tone_sum: float = 0.0
    n_source_articles: int = 0


def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _fetch_daily_events(day: date) -> list[str] | None:
    """The real day's events, as raw TSV lines (undecoded further) -- or
    None on any failure (network, non-2xx, a corrupt zip). Never raises;
    a bad day is logged and simply contributes nothing to that week's
    counts, same as opt.weather_window's own network-failure handling.
    """
    url = DAILY_EVENTS_URL_TEMPLATE.format(date=day.strftime("%Y%m%d"))
    try:
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=_REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            name = z.namelist()[0]
            with z.open(name) as f:
                return f.read().decode("utf-8", errors="replace").splitlines()
    except (requests.RequestException, zipfile.BadZipFile, OSError, IndexError) as exc:
        LOGGER.warning(f"GDELT daily events fetch failed for {day.isoformat()} ({url}): {exc}")
        return None


def _accumulate_day(lines: list[str], accumulators: dict[str, _WeekAccumulator]) -> None:
    """Fold one day's real GDELT events into the running per-chokepoint
    week accumulators -- conflict-coded (root 18/19/20), real-geocoded
    events only, matched against every chokepoint's real circle via the
    same distance function opt.chokepoints itself uses (first match wins;
    chokepoint circles do not overlap in practice, see that module's own
    disjointness reasoning in its docstring)."""
    for line in lines:
        fields = line.split("\t")
        if len(fields) < _MIN_COLUMNS:
            continue
        if fields[_COL_EVENT_ROOT_CODE] not in CONFLICT_ROOT_CODES:
            continue
        lat_str, lon_str = fields[_COL_ACTION_GEO_LAT], fields[_COL_ACTION_GEO_LONG]
        if not lat_str or not lon_str:
            continue
        try:
            lat, lon = float(lat_str), float(lon_str)
            tone = float(fields[_COL_AVG_TONE])
            n_articles = int(float(fields[_COL_NUM_ARTICLES]))
        except ValueError:
            continue

        for cp_id, geom in CHOKEPOINT_GEOMETRY.items():
            if _haversine_nm((lon, lat), (geom.lon, geom.lat)) <= geom.radius_nm:
                acc = accumulators[cp_id]
                acc.event_count += 1
                acc.tone_sum += tone
                acc.n_source_articles += n_articles
                break


def _iso_weeks_in_range(start: date, end: date) -> list[tuple[int, int]]:
    """Every distinct (iso_year, iso_week) touched by [start, end], in
    order, each appearing once."""
    weeks: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    d = start
    while d <= end:
        iso = d.isocalendar()
        key = (iso.year, iso.week)
        if key not in seen:
            seen.add(key)
            weeks.append(key)
        d += timedelta(days=1)
    return weeks


def _days_in_iso_week(iso_year: int, iso_week: int) -> list[date]:
    return [date.fromisocalendar(iso_year, iso_week, weekday) for weekday in range(1, 8)]


def _existing_weeks(out_path: Path) -> set[tuple[int, int]]:
    """Every (iso_year, iso_week) already written -- a week is "done" once
    ANY row for it exists, since every week's rows are appended together in
    one batch (see _append_week_rows)."""
    if not out_path.exists():
        return set()
    weeks: set[tuple[int, int]] = set()
    with out_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            weeks.add((int(row["iso_year"]), int(row["iso_week"])))
    return weeks


def _append_week_rows(out_path: Path, rows: list[dict[str, object]]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not out_path.exists() or out_path.stat().st_size == 0
    with out_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDNAMES)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)


def harvest(
    start: date,
    end: date,
    *,
    out_path: Path = OUT_CSV,
    request_delay_seconds: float = REQUEST_DELAY_SECONDS,
) -> int:
    """Harvest weekly per-chokepoint conflict counts for every ISO week
    overlapping [start, end]. Incremental (see module docstring); returns
    the number of new (chokepoint, week) rows actually written.
    """
    existing = _existing_weeks(out_path)
    weeks_needed = [w for w in _iso_weeks_in_range(start, end) if w not in existing]
    LOGGER.info(f"{len(weeks_needed)} ISO week(s) to harvest ({len(existing)} already present, skipped).")

    rows_written = 0
    for iso_year, iso_week in weeks_needed:
        accumulators: dict[str, _WeekAccumulator] = {cp_id: _WeekAccumulator() for cp_id in CHOKEPOINT_GEOMETRY}
        any_day_fetched = False
        for day in _days_in_iso_week(iso_year, iso_week):
            if day > datetime.now(UTC).date():
                continue  # a future day within the week's Mon-Sun span -- nothing to fetch yet
            lines = _fetch_daily_events(day)
            time.sleep(request_delay_seconds)
            if lines is None:
                continue
            any_day_fetched = True
            _accumulate_day(lines, accumulators)

        if not any_day_fetched:
            LOGGER.warning(f"ISO week {iso_year}-W{iso_week:02d}: every day failed to fetch -- skipping, not writing a false zero.")
            continue

        retrieved_at = datetime.now(UTC).isoformat()
        rows = [
            {
                "chokepoint_id": cp_id,
                "iso_year": iso_year,
                "iso_week": iso_week,
                "event_count": acc.event_count,
                "avg_tone": (acc.tone_sum / acc.event_count) if acc.event_count > 0 else "",
                "n_source_articles": acc.n_source_articles,
                "retrieved_at": retrieved_at,
            }
            for cp_id, acc in accumulators.items()
        ]
        _append_week_rows(out_path, rows)
        rows_written += len(rows)
        LOGGER.info(f"ISO week {iso_year}-W{iso_week:02d}: wrote {len(rows)} rows.")

    return rows_written


def write_pull_notes(
    start: date,
    end: date,
    out_path: Path = OUT_CSV,
    notes_path: Path = PULL_NOTES_PATH,
) -> None:
    row_count = 0
    if out_path.exists():
        with out_path.open(newline="", encoding="utf-8") as f:
            row_count = sum(1 for _ in csv.DictReader(f))

    notes_path.parent.mkdir(parents=True, exist_ok=True)
    notes_path.write_text(
        "# GDELT Chokepoint Conflict-Intensity Weekly Series -- Pull Notes\n\n"
        f"**Retrieval date:** {datetime.now(UTC).date().isoformat()}\n"
        "**Source:** GDELT 2.0 Event Database, daily export "
        f"(`{DAILY_EVENTS_URL_TEMPLATE}`)\n"
        f"**Date range requested:** {start.isoformat()} to {end.isoformat()}\n"
        f"**Output:** `{out_path.relative_to(REPO_ROOT)}` ({row_count:,} rows)\n"
        f"**Filter:** CAMEO root event codes {sorted(CONFLICT_ROOT_CODES)} "
        "(Assault, Fight, Unconventional Mass Violence), geolocated within "
        "each chokepoint's real circle (`opt.chokepoints.CHOKEPOINT_GEOMETRY`).\n\n"
        "## Licence / citation\n\n"
        "GDELT is free and open for any use; the GDELT Project asks that "
        "publications and products using it cite:\n\n"
        "Leetaru, Kalev, and Schrodt, Philip A. \"GDELT: Global Data on Events, "
        "Language, and Tone, 1979-2012.\" International Studies Association "
        "Annual Conference, San Francisco, 2013.\n\n"
        "See https://www.gdeltproject.org/about.html#termsofuse for the full terms.\n\n"
        "## Honesty note\n\n"
        "GDELT counts media coverage, not events -- coverage volume is driven "
        "by news attention as much as by ground truth. Any consumer of this "
        "table must z-score each chokepoint against its OWN history and never "
        "compare raw event_count across chokepoints. See "
        "`data_builders.harvest_gdelt`'s own module docstring.\n",
        encoding="utf-8",
    )
    LOGGER.info(f"wrote {notes_path}")


def main() -> None:
    _setup_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    default_end = datetime.now(UTC).date() - timedelta(days=1)  # yesterday -- today's file may not exist yet
    default_start = default_end - timedelta(days=365)
    parser.add_argument("--start", type=date.fromisoformat, default=default_start)
    parser.add_argument("--end", type=date.fromisoformat, default=default_end)
    args = parser.parse_args()

    LOGGER.info(f"Harvesting GDELT chokepoint conflict intensity: {args.start} to {args.end} ...")
    n_written = harvest(args.start, args.end)
    write_pull_notes(args.start, args.end)
    LOGGER.info(f"harvest complete: {n_written} new rows written")


if __name__ == "__main__":
    main()
