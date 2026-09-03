"""Daily route-level charter rates, in dollars per day.

Why this exists
---------------
F-05 is the register's oldest open major fault and the problem statement's
central ask: forecasting "for various vessel types **and trade routes**". The
desk does not do the routes part. Six different origin countries, same cargo
and laycan, return a ceiling identical to four decimal places, because the
route-basis mechanism in ``opt.basis`` has never had anything to activate on.

That module says exactly what it is waiting for:

    either activates automatically, with no code change, the moment real
    $/day-denominated route-level evidence for a family reaches the relevant
    sample size

and why the one route series already on disk cannot serve:
``SG_SUPRAMAX_INDONESIA_ECI_USD_T`` is three observations in **dollars per
tonne**, and converting a voyage $/tonne rate to a $/day equivalent needs a
chain of assumptions (cargo quantity, ballast leg, bunker cost) that would
"cross from modelled into invented".

handybulk publishes a daily page of indicative charter levels quoted directly
in **dollars per day**, by vessel class and named lane:

    Supramax open South China via Indonesia to East Coast India (ECI)
    fixed around $22,500

That is the missing denomination, for a lane that is a real route family
(``INDONESIA_EC_INDIA``). This module harvests it.

What this honestly is, and is not
---------------------------------
These are **indicative broker levels**, not settled fixtures. The page says
"fixed around", and a round $22,500 is a market assessment rather than a
recorded transaction. That puts them at ESTIMATED, alongside the Signal weekly
assessments already feeding ``opt.basis`` -- real market evidence, published by
a market participant, and weaker than a fixture report. Nothing here is
labelled OBSERVED.

Coverage is thin, and that matters more than the part that works. Every
``RouteFamily`` here is anchored on **East Coast India** as the destination, so
a lane only counts if it discharges there. Surveyed against the problem
statement's own origins on the day this was written, out of 92 published lanes:

===============  ==================  =====================  ==================
Origin           lanes published     ...reaching India      ...reaching EC-India
===============  ==================  =====================  ==================
Indonesia        13                  2                      **2**
South Africa     8                   2                      **0** (both WCI)
Australia        13                  0                      **0**
United States    12                  0                      **0**
Mozambique       0                   0                      **0**
Russia           0                   0                      **0**
===============  ==================  =====================  ==================

So on this day exactly **one** route family gains evidence: Indonesia to East
Coast India. South Africa looked like a second until the destination was
checked -- both its India lanes discharge on the **west** coast, which is a
different coast and a different market, and counting them would have been
quietly wrong. Australia, the US, Mozambique and Russia stay exactly as
route-blind as before, including Newcastle, the desk's most-quoted origin.

That is a real dent in F-05, not a fix for it. The "Class-only" badge stays
correct and visible for every family this does not reach, and the fault stays
open.

The page carries no archive, only the current day. History therefore
accumulates forward from the first run and cannot be backfilled.

Run it:
    uv run python -m data_builders.harvest_route_rates
"""

from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Final

from data_builders.harvest_handybulk import (
    REQUEST_TIMEOUT_S,
    USER_AGENT,
    _visible_lines,
)
from opt.network import RouteFamily

LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
CSV_PATH: Final[Path] = REPO_ROOT / "raw_data" / "handybulk_route_rates.csv"
MASTER_PATH: Final[Path] = REPO_ROOT / "src" / "data" / "master_long.parquet"

SOURCE_URL: Final[str] = "https://www.handybulk.com/ship-charter-rates/"

COLUMNS: Final[tuple[str, ...]] = (
    "date",
    "vessel_class",
    "origin_text",
    "dest_text",
    "usd_per_day",
    "route_family",
    "series_id",
    "source_url",
)

#: The page's class headings, mapped to the vessel class this project models.
#: Ultramax is a real, distinct size that this system does not model
#: separately; it sits between Supramax and Panamax and the market quotes the
#: two together ("ultramax/supramax"). Mapping it to Supramax follows the
#: Baltic's own BSI convention rather than inventing a fifth class.
_CLASS_FROM_HEADING: Final[dict[str, str]] = {
    "handy": "HANDYSIZE",
    "handysize": "HANDYSIZE",
    "supramax": "SUPRAMAX",
    "ultramax": "SUPRAMAX",
    "panamax": "PANAMAX",
    "kamsarmax": "PANAMAX",
    "capesize": "CAPESIZE",
}

#: Region wording on the page -> the route family that region is the origin of.
#: Deliberately narrow. A lane is only claimed when the origin text names a
#: region this project actually trades from; anything looser would attach a
#: rate to a family it was not quoted for, which is the precise failure
#: ``opt.basis`` exists to avoid.
_ORIGIN_PATTERNS: Final[tuple[tuple[str, RouteFamily], ...]] = (
    (r"\bindonesia\b", RouteFamily.INDONESIA_EC_INDIA),
    (r"\bsouth africa\b|\bsaf\b|\brichards bay\b", RouteFamily.SOUTH_AFRICA_EC_INDIA),
    (r"\baustralia\b|\bnewcastle\b|\bgladstone\b", RouteFamily.AUSTRALIA_EC_INDIA),
    (r"\bmozambique\b|\bbeira\b|\bnacala\b", RouteFamily.MOZAMBIQUE_EC_INDIA),
    (r"\bus gulf\b|\busg\b|\bus east coast\b|\busec\b|\bnola\b", RouteFamily.US_EC_INDIA),
    (r"\bsingapore\b", RouteFamily.SINGAPORE_EC_INDIA),
    (r"\brussia\b|\bvostochny\b", RouteFamily.RUSSIA_EC_INDIA),
)

#: Every route family here is anchored on EC-India as the DESTINATION
#: (``opt.network.RouteFamily``), so a lane only counts when it actually
#: discharges on India's east coast. "West Coast India" is a different coast
#: and a different market; counting it would be quietly wrong.
_EC_INDIA_DEST: Final[re.Pattern[str]] = re.compile(
    r"east coast india|\beci\b", re.IGNORECASE
)

#: Prefix for series this module writes. NOT ``SG_`` -- that prefix means
#: Signal, the weekly-report source, and reusing it would file one publisher's
#: assessments under another's name in a table whose entire purpose is
#: knowing where a number came from.
SERIES_PREFIX: Final[str] = "HB"

_HEADING: Final[re.Pattern[str]] = re.compile(
    r"^(Handy|Handysize|Supramax|Ultramax|Panamax|Kamsarmax|Capesize)\s+Charter Rates$",
    re.IGNORECASE,
)
_QUOTE: Final[re.Pattern[str]] = re.compile(
    r"^[•\-\*\s]*(?:\w[\w\- ]*?)\s+open\s+(.+?)\s+to\s+(.+?)\s+fixed around\s+\$([\d,]+)",
    re.IGNORECASE,
)
_PAGE_DATE: Final[re.Pattern[str]] = re.compile(r"(\d{1,2}-[A-Za-z]+-\d{4})\s+Daily Updated")


@dataclass(frozen=True)
class RouteQuote:
    """One published lane level."""

    quoted_on: date
    vessel_class: str
    origin_text: str
    dest_text: str
    usd_per_day: float
    route_family: RouteFamily | None
    """None for a lane this project does not trade -- kept in the CSV anyway,
    because a lane that is not mapped today may be mapped later, and throwing
    away real observations to save bytes is never the right trade."""

    @property
    def series_id(self) -> str | None:
        """The master_long series this quote belongs to, or None when the lane
        is not one of this project's route families.

        Ends ``_USD_DAY`` because that suffix is what ``opt.basis`` tests to
        decide an observation is usable for a basis fit.
        """
        if self.route_family is None:
            return None
        region = self.route_family.value.removesuffix("_ec_india").upper()
        return f"{SERIES_PREFIX}_{self.vessel_class}_{region}_ECI_USD_DAY"


@dataclass(frozen=True)
class RouteHarvestResult:
    fetched: bool
    quoted_on: date | None
    parsed: int
    mapped: int
    """How many parsed lanes belong to a route family this project trades."""
    added: int
    reason: str | None = None
    quotes: tuple[RouteQuote, ...] = field(default_factory=tuple)


def fetch_page(url: str = SOURCE_URL) -> str | None:
    """One request, offline-safe. Same contract as the index harvester."""
    import requests

    try:
        response = requests.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_S
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        LOGGER.info("route-rate fetch failed, keeping existing data: %s", exc)
        return None
    return response.text


def _route_family_for(origin_text: str, dest_text: str) -> RouteFamily | None:
    if not _EC_INDIA_DEST.search(dest_text):
        return None
    for pattern, family in _ORIGIN_PATTERNS:
        if re.search(pattern, origin_text, re.IGNORECASE):
            return family
    return None


def parse_page(page: str) -> tuple[date | None, list[RouteQuote]]:
    """Every lane the page quotes, with the page's own publication date.

    The date comes from the page heading ("3-September-2026 Daily Updated Ship
    Charter Rates") rather than from the clock, so a quote harvested late still
    carries the day it was actually published.
    """
    lines = _visible_lines(page)

    quoted_on: date | None = None
    for line in lines:
        match = _PAGE_DATE.search(line)
        if match:
            try:
                # A publication date, not an instant -- see the sibling
                # harvester for why the timezone lint is suppressed here.
                quoted_on = datetime.strptime(match.group(1), "%d-%B-%Y").date()  # noqa: DTZ007
            except ValueError:
                quoted_on = None
            break

    quotes: list[RouteQuote] = []
    vessel_class: str | None = None
    for line in lines:
        heading = _HEADING.match(line)
        if heading:
            vessel_class = _CLASS_FROM_HEADING.get(heading.group(1).lower())
            continue
        if vessel_class is None or quoted_on is None:
            continue
        quote = _QUOTE.match(line)
        if not quote:
            continue
        origin_text, dest_text = quote.group(1).strip(), quote.group(2).strip()
        quotes.append(
            RouteQuote(
                quoted_on=quoted_on,
                vessel_class=vessel_class,
                origin_text=origin_text,
                dest_text=dest_text,
                usd_per_day=float(quote.group(3).replace(",", "")),
                route_family=_route_family_for(origin_text, dest_text),
            )
        )
    return quoted_on, quotes


def read_existing(path: Path = CSV_PATH) -> set[tuple[str, str, str, str]]:
    """Keys already stored: (date, class, origin, destination)."""
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as handle:
        return {
            (r["date"], r["vessel_class"], r["origin_text"], r["dest_text"])
            for r in csv.DictReader(handle)
            if r.get("date")
        }


def append_quotes(quotes: list[RouteQuote], path: Path = CSV_PATH) -> int:
    """Append lanes not already stored. Returns how many were added.

    Append-only by key. The page republishes the same lanes every day at new
    levels, so the key includes the date; re-running within a day adds nothing.
    """
    known = read_existing(path)
    new = [
        q
        for q in quotes
        if (q.quoted_on.isoformat(), q.vessel_class, q.origin_text, q.dest_text) not in known
    ]
    if not new:
        return 0

    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS))
        if not exists:
            writer.writeheader()
        for q in new:
            writer.writerow(
                {
                    "date": q.quoted_on.isoformat(),
                    "vessel_class": q.vessel_class,
                    "origin_text": q.origin_text,
                    "dest_text": q.dest_text,
                    "usd_per_day": f"{q.usd_per_day:.0f}",
                    "route_family": q.route_family.value if q.route_family else "",
                    "series_id": q.series_id or "",
                    "source_url": SOURCE_URL,
                }
            )
    return len(new)


def update_master(quotes: list[RouteQuote], master_path: Path = MASTER_PATH) -> int:
    """Fold mapped lanes into master_long as ``HB_*_USD_DAY`` series.

    Only lanes that map to a route family are written: an unmapped lane has no
    consumer and would be noise in a file every model reads. Bounded the same
    way the index harvester is -- only ``HB_``-prefixed series, only dates not
    already present.
    """
    import polars as pl

    if not master_path.exists():
        LOGGER.info("No master at %s; nothing to update.", master_path)
        return 0

    mapped = [q for q in quotes if q.series_id is not None]
    if not mapped:
        return 0

    master = pl.read_parquet(master_path)
    hb = master.filter(pl.col("series_id").str.starts_with(f"{SERIES_PREFIX}_"))
    known = set(zip(hb["series_id"].to_list(), hb["date"].to_list(), strict=True))

    additions = [
        {
            "series_id": q.series_id,
            "date": q.quoted_on,
            "value": q.usd_per_day,
            "unit": "usd/day",
            "source": "handybulk_route",
        }
        for q in mapped
        if (q.series_id, q.quoted_on) not in known
    ]
    # One lane per series per day. The page can quote the same family twice in
    # a day (two Indonesia lanes, say); keeping the first is arbitrary but
    # stable, and averaging two indicative levels would invent a third number
    # neither publisher quoted.
    deduped: dict[tuple[str, date], dict] = {}
    for row in additions:
        deduped.setdefault((row["series_id"], row["date"]), row)
    if not deduped:
        return 0

    combined = pl.concat(
        [master, pl.DataFrame(list(deduped.values()), schema=master.schema)], how="vertical"
    ).sort(["series_id", "date"])
    tmp = master_path.with_suffix(".parquet.tmp")
    combined.write_parquet(tmp)
    tmp.replace(master_path)
    LOGGER.info("master_long: +%d route-rate row(s)", len(deduped))
    return len(deduped)


def harvest(path: Path = CSV_PATH, url: str = SOURCE_URL) -> RouteHarvestResult:
    page = fetch_page(url)
    if page is None:
        return RouteHarvestResult(
            fetched=False,
            quoted_on=None,
            parsed=0,
            mapped=0,
            added=0,
            reason="The source could not be reached. Existing data is unchanged.",
        )

    quoted_on, quotes = parse_page(page)
    if quoted_on is None or not quotes:
        return RouteHarvestResult(
            fetched=True,
            quoted_on=quoted_on,
            parsed=len(quotes),
            mapped=0,
            added=0,
            reason=(
                "The page was fetched but no dated lane quotes could be read. Its wording "
                "has probably changed; the patterns in parse_page need revisiting."
            ),
        )

    added = append_quotes(quotes, path)
    mapped = sum(1 for q in quotes if q.route_family is not None)
    LOGGER.info(
        "route rates %s: %d lane(s) parsed, %d on a traded route, %d new",
        quoted_on.isoformat(),
        len(quotes),
        mapped,
        added,
    )
    return RouteHarvestResult(
        fetched=True,
        quoted_on=quoted_on,
        parsed=len(quotes),
        mapped=mapped,
        added=added,
        quotes=tuple(quotes),
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    result = harvest()
    if not result.fetched or result.reason:
        LOGGER.warning("%s", result.reason)
        return
    update_master(list(result.quotes))


if __name__ == "__main__":
    main()
