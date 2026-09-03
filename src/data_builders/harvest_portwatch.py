"""Scale the PortWatch harvest from 14 ports to ~150, plus all 28 chokepoints.

Why this exists
----------------
The original harvest (raw_data/portwatch/, documented in PULL_NOTES.md) pulled
daily dry-bulk port calls for the 14 ports directly named or implied by the
problem statement's lanes. That is enough to run the chartering optimizer on
the stated routes, but nowhere near enough to reconstruct where free tonnage
sits globally -- the Tonnage Field moat needs visibility into the whole basin
network a ballaster could be idle in, not just the origin/destination pairs
SAIL itself trades on.

This module has three stages, each independently runnable and checkpointed so
a network interruption doesn't waste the ~400ms/request budget Ark's original
pull already established as safe (no rate limiting observed at that pace):

1. resolve_candidates() -- take the curated list in _port_candidates.py and
   confirm each one actually exists in the PortWatch ports database, the same
   way the original 14 were confirmed. Writes port_index_extended.csv, which
   records hits AND misses (a candidate that doesn't resolve is data, not an
   error -- see raw_data/portwatch/PULL_NOTES.md's own "Unresolved ports"
   section for precedent).
2. pull_ports() -- daily port-calls data for every resolved port, in the same
   CSV shape as the original *_daily_portcalls.csv files.
3. pull_chokepoints() -- daily transit data for all 28 IMF PortWatch
   chokepoints (Suez, Panama, Malacca, Bab el-Mandeb, ...), which the original
   harvest explicitly skipped ("Also present but not used:
   Daily_Chokepoints_Data"). This dataset is richer than the ports table: it
   carries capacity_dry_bulk (tonnage), not just a call count, which the ports
   endpoint does not expose at all.

Run as a script to do all three stages in order:
    python -m data_builders.harvest_portwatch
"""
from __future__ import annotations

import csv
import datetime
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from data_builders._port_candidates import CANDIDATES, PortCandidate

LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
RAW_DATA: Final[Path] = REPO_ROOT / "raw_data" / "portwatch"

BASE: Final[str] = "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services"
PORTS_QUERY: Final[str] = f"{BASE}/PortWatch_ports_database/FeatureServer/0/query"
DAILY_PORTS_QUERY: Final[str] = f"{BASE}/Daily_Ports_Data/FeatureServer/0/query"
DAILY_CHOKEPOINTS_QUERY: Final[str] = f"{BASE}/Daily_Chokepoints_Data/FeatureServer/0/query"

#: Delay between requests. Ark's pull observed no rate limiting at ~400ms with
#: 14 ports x 3 pages; keep the same pace scaled to a much larger request count.
REQUEST_DELAY_SECONDS: Final[float] = 0.4

PAGE_SIZE: Final[int] = 1000

CHOKEPOINT_IDS: Final[tuple[str, ...]] = tuple(f"chokepoint{i}" for i in range(1, 29))

PORT_INDEX_PATH: Final[Path] = RAW_DATA / "port_index_extended.csv"


def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _get_json(url: str, params: dict[str, str], retries: int = 3) -> dict:
    """GET with polite retry. Raises on persistent failure rather than swallowing it."""
    query = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{query}", headers={"User-Agent": "Mozilla/5.0"})
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except (urllib.error.URLError, TimeoutError) as exc:
            last_exc = exc
            LOGGER.warning(f"request failed (attempt {attempt + 1}/{retries}): {exc}")
            time.sleep(2.0 * (attempt + 1))
    assert last_exc is not None
    raise last_exc


# ---------------------------------------------------------------------------
# Stage 1 -- resolve candidates against the live ports database
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedPort:
    label: str
    portid: str
    portname: str
    country: str
    iso3: str
    lat: float
    lon: float
    role: str
    basin: str


def _search_port(term: str) -> list[dict]:
    where = f"UPPER(portname) LIKE '%{term}%' OR UPPER(fullname) LIKE '%{term}%'"
    data = _get_json(
        PORTS_QUERY,
        {
            "where": where,
            "outFields": "portid,portname,country,ISO3,fullname,lat,lon",
            "returnGeometry": "false",
            "resultRecordCount": "10",
            "f": "json",
        },
    )
    return data.get("features", [])


def _filter_by_country(feats: list[dict], iso3: str | None) -> list[dict]:
    """Restrict hits to the expected country. Empty when the hint eliminates all of them."""
    if iso3 is None:
        return feats
    return [f for f in feats if f["attributes"].get("ISO3") == iso3]


def resolve_candidates(
    candidates: tuple[PortCandidate, ...] = CANDIDATES,
) -> tuple[list[ResolvedPort], list[PortCandidate]]:
    """Resolve each candidate against PortWatch. Returns (resolved, unresolved).

    A live run against the real service caught the reason this needs to be more
    careful than "take the first hit": searching "SANTOS" returns General Santos
    (Philippines) before Santos (Brazil), and "ROSARIO" returns Puerto Del Rosario
    (Canary Islands) before Rosario (Argentina) -- a plain first-match heuristic
    silently resolved both candidates to the wrong hemisphere. Every candidate here
    carries a ``country_hint`` (ISO3); hits are filtered to that country first, and
    only the country-filtered set is ever taken automatically.

    A search term whose hits contain none of the expected country is treated the
    same as a miss and the next search term is tried -- resolving to an
    admittedly-wrong-country port is worse than an honest gap, matching how
    opt.geography treats a missing distance as a bug to surface, not a number to
    invent. If the country-filtered set still has more than one hit (e.g. two
    same-named terminals in one country), the first is taken and logged, mirroring
    the judgement calls already recorded in PULL_NOTES.md for the original 14
    (e.g. Hay Point covering the adjacent Dalrymple Bay terminal).
    """
    resolved: list[ResolvedPort] = []
    unresolved: list[PortCandidate] = []

    for i, cand in enumerate(candidates):
        hit: dict | None = None
        for term in cand.search_terms:
            feats = _search_port(term)
            time.sleep(REQUEST_DELAY_SECONDS)
            if not feats:
                continue
            country_feats = _filter_by_country(feats, cand.country_hint)
            if not country_feats:
                LOGGER.info(
                    f"{cand.label}: {len(feats)} hit(s) for {term!r} but none in "
                    f"{cand.country_hint} ({[f['attributes']['portname'] for f in feats]}); "
                    f"trying next term"
                )
                continue
            if len(country_feats) > 1:
                LOGGER.info(
                    f"{cand.label}: {len(country_feats)} hits for {term!r} within "
                    f"{cand.country_hint}, taking first: "
                    f"{[f['attributes']['portname'] for f in country_feats]}"
                )
            hit = country_feats[0]
            break

        if hit is None:
            LOGGER.warning(f"{cand.label}: NOT RESOLVED (tried {cand.search_terms})")
            unresolved.append(cand)
            continue

        a = hit["attributes"]
        resolved.append(
            ResolvedPort(
                label=cand.label,
                portid=a["portid"],
                portname=a["portname"],
                country=a.get("country", ""),
                iso3=a.get("ISO3", ""),
                lat=a["lat"],
                lon=a["lon"],
                role=cand.role,
                basin=cand.basin,
            )
        )
        if (i + 1) % 20 == 0:
            LOGGER.info(f"resolved {i + 1}/{len(candidates)} candidates so far")

    LOGGER.info(f"resolution complete: {len(resolved)} resolved, {len(unresolved)} unresolved")
    return resolved, unresolved


def write_port_index(
    resolved: list[ResolvedPort], unresolved: list[PortCandidate], path: Path = PORT_INDEX_PATH
) -> None:
    """Write the extended port index, hits AND misses, like ports_index.csv does."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["label", "portid", "portname", "country", "iso3", "lat", "lon", "role", "basin", "resolved"])
        for r in resolved:
            w.writerow([r.label, r.portid, r.portname, r.country, r.iso3, r.lat, r.lon, r.role, r.basin, "yes"])
        for c in unresolved:
            w.writerow([c.label, "", "", "", "", "", "", c.role, c.basin, "no"])
    LOGGER.info(f"wrote {path} ({len(resolved)} resolved, {len(unresolved)} unresolved)")


# ---------------------------------------------------------------------------
# Stage 2 -- pull daily port-calls data
# ---------------------------------------------------------------------------


def _pull_paginated(query_url: str, where: str) -> list[dict]:
    """Page through a query at PAGE_SIZE until exceededTransferLimit is absent.

    Mirrors the original harvest's documented pagination quirk: on a
    single-page response, exceededTransferLimit is omitted entirely rather than
    set false, so a plain dict.get with a falsy default is required.
    """
    rows: list[dict] = []
    offset = 0
    while True:
        data = _get_json(
            query_url,
            {
                "where": where,
                "outFields": "*",
                "orderByFields": "ObjectId",
                "resultRecordCount": str(PAGE_SIZE),
                "resultOffset": str(offset),
                "f": "json",
            },
        )
        feats = data.get("features", [])
        rows.extend(f["attributes"] for f in feats)
        time.sleep(REQUEST_DELAY_SECONDS)
        if not data.get("exceededTransferLimit", False):
            break
        offset += PAGE_SIZE
    return rows


def _existing_file_matches(out_path: Path, expected_portid: str) -> bool:
    """Does the file already on disk actually contain the port we think it does?

    A checkpoint that only checks file *existence* trusts whatever wrote that
    file, which is unsafe the moment two runs can touch the same directory. That
    happened for real during this harvest: a supposedly-killed prior run (its
    Windows process outlived the stop signal) kept writing into raw_data/portwatch/
    with stale, pre-fix resolution logic, and a plain existence check let 4 wrong-
    country files (Santos -> Philippines, Vancouver -> USA instead of Canada, ...)
    silently survive a second, corrected run that should have overwritten them.
    Checking the portid actually inside the file turns that into a loud mismatch
    instead of a silent skip.
    """
    if not out_path.exists():
        return False
    try:
        with out_path.open(encoding="utf-8") as fh:
            first_row = next(csv.DictReader(fh), None)
    except (OSError, StopIteration):
        return False
    return first_row is not None and first_row.get("portid") == expected_portid


def pull_ports(resolved: list[ResolvedPort], out_dir: Path = RAW_DATA, skip_existing: bool = True) -> None:
    """Pull daily dry-bulk port-calls data for every resolved port.

    Checkpointed: a port whose output CSV already exists AND whose content
    actually matches the expected portid is skipped unless ``skip_existing=False``.
    A file that exists but belongs to a different port (see
    ``_existing_file_matches``) is treated as missing and re-pulled -- this is
    what stops a stale or foreign write from silently surviving a re-run.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, port in enumerate(resolved):
        out_path = out_dir / f"{port.label}_daily_portcalls.csv"
        if skip_existing and _existing_file_matches(out_path, port.portid):
            LOGGER.debug(f"{port.label}: already pulled, skipping")
            continue
        if out_path.exists() and skip_existing:
            LOGGER.warning(
                f"{port.label}: existing file does not match expected portid "
                f"{port.portid} -- overwriting rather than trusting it"
            )

        rows = _pull_paginated(DAILY_PORTS_QUERY, f"portid='{port.portid}'")
        if not rows:
            LOGGER.warning(f"{port.label} ({port.portid}): zero rows returned")
            continue

        fieldnames = list(rows[0].keys())
        with out_path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)

        LOGGER.info(f"[{i + 1}/{len(resolved)}] {port.label}: {len(rows)} rows -> {out_path.name}")


# ---------------------------------------------------------------------------
# Stage 3 -- pull all 28 chokepoints
# ---------------------------------------------------------------------------


def pull_chokepoints(out_dir: Path = RAW_DATA, skip_existing: bool = True) -> None:
    """Pull daily transit data for every IMF PortWatch chokepoint.

    Unlike the ports endpoint, this one carries capacity_dry_bulk directly --
    an actual tonnage figure, not just a call count -- which is why the
    original harvest's decision to skip it (PULL_NOTES.md: "Also present but
    not used: Daily_Chokepoints_Data") left real signal on the table.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, cp_id in enumerate(CHOKEPOINT_IDS):
        out_path = out_dir / f"{cp_id}_daily_transits.csv"
        if skip_existing and _existing_file_matches(out_path, cp_id):
            LOGGER.debug(f"{cp_id}: already pulled, skipping")
            continue

        rows = _pull_paginated(DAILY_CHOKEPOINTS_QUERY, f"portid='{cp_id}'")
        if not rows:
            LOGGER.warning(f"{cp_id}: zero rows returned")
            continue

        fieldnames = list(rows[0].keys())
        with out_path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)

        name = rows[0].get("portname", cp_id)
        LOGGER.info(f"[{i + 1}/{len(CHOKEPOINT_IDS)}] {cp_id} ({name}): {len(rows)} rows -> {out_path.name}")



# ---------------------------------------------------------------------------
# Incremental refresh -- keeping what was harvested current
# ---------------------------------------------------------------------------
#
# The original harvest is a one-shot: `pull_ports` skips any port whose file
# already exists, so re-running it changes nothing, and forcing it with
# `skip_existing=False` re-downloads every port's full history and rewrites
# 53 MB. Neither of those keeps the data current, and in practice nothing did:
# every one of the 128 port files sat at the same stale date while the desk
# quietly served congestion and tightness figures derived from them.
#
# This is the cheap path. Each port is asked only for rows NEWER than the last
# date its own file already carries -- one small query per port instead of a
# full re-download -- and the rows are appended. Nothing already on disk is
# rewritten, for the same reason the rate harvester never rewrites a stored
# figure: it is what the models were fitted on and what past recommendations
# were priced against.


@dataclass(frozen=True)
class RefreshResult:
    """What one incremental pass did."""

    files_checked: int
    files_updated: int
    rows_added: int
    newest_date: str | None
    failures: tuple[str, ...] = ()
    """Files that could not be refreshed, named. A silent partial refresh
    would leave some ports current and others not, with nothing to say which."""


def _existing_bounds(path: Path) -> tuple[str | None, str | None, list[str]]:
    """(portid, latest date, fieldnames) for a harvested file.

    The portid is read from the file's own rows rather than from the port
    index, so a refresh cannot ask one port for another's data even if the
    index and the directory have drifted apart.
    """
    try:
        with path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            fieldnames = list(reader.fieldnames or [])
            portid: str | None = None
            latest: str | None = None
            for row in reader:
                pid = (row.get("portid") or "").strip()
                if pid:
                    if portid is None:
                        portid = pid
                    elif pid != portid:
                        # Two ports in one file: refusing beats guessing.
                        return None, None, fieldnames
                day = (row.get("date") or "")[:10]
                if day and (latest is None or day > latest):
                    latest = day
        return portid, latest, fieldnames
    except OSError as exc:
        LOGGER.warning(f"{path.name}: unreadable ({exc})")
        return None, None, []


def refresh_ports(out_dir: Path = RAW_DATA) -> RefreshResult:
    """Append rows newer than each existing file's own latest date.

    Only touches files that already exist: this brings a harvest up to date, it
    does not start one. A port that has never been pulled needs `pull_ports`,
    which is a deliberate, much heavier operation.
    """
    files = sorted(out_dir.glob("*_daily_portcalls.csv"))
    checked = updated = added = 0
    newest: str | None = None
    failures: list[str] = []

    for path in files:
        checked += 1
        portid, latest, fieldnames = _existing_bounds(path)
        if not portid or not latest or not fieldnames:
            failures.append(path.name)
            continue
        try:
            # `date > DATE 'x'` is ArcGIS's own date-literal syntax, verified
            # against the live service before this was written: the unfiltered
            # query returns 2019 rows and the filtered one returns 2026-08-15
            # onward, so the predicate is genuinely applied server-side rather
            # than silently ignored.
            rows = _pull_paginated(
                DAILY_PORTS_QUERY, f"portid='{portid}' AND date > DATE '{latest}'"
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # Offline-safe: one unreachable port must not abort the rest, and
            # must not look like "this port had no new data".
            LOGGER.info(f"{path.name}: refresh failed, left unchanged ({exc})")
            failures.append(path.name)
            continue

        fresh = [r for r in rows if str(r.get("date", ""))[:10] > latest]
        if not fresh:
            continue

        with path.open("a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
            for row in fresh:
                writer.writerow({k: row.get(k, "") for k in fieldnames})
        updated += 1
        added += len(fresh)
        for row in fresh:
            day = str(row.get("date", ""))[:10]
            if day and (newest is None or day > newest):
                newest = day
        LOGGER.info(f"{path.name}: +{len(fresh)} row(s)")

    LOGGER.info(
        f"port refresh: {checked} file(s) checked, {updated} updated, "
        f"{added} row(s) added, newest {newest}"
    )
    if added:
        update_master_ports(out_dir)
    return RefreshResult(
        files_checked=checked,
        files_updated=updated,
        rows_added=added,
        newest_date=newest,
        failures=tuple(failures),
    )


#: How build_master derives a series id from a port file's name. Duplicated
#: here rather than imported so this module does not pull in the whole master
#: build; `test_the_series_naming_matches_build_master` pins the two together.
def _series_slug(path: Path) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", path.stem.split("_daily")[0].upper()).strip("_")


def update_master_ports(out_dir: Path = RAW_DATA, master_path: Path | None = None) -> int:
    """Fold newly appended port-call rows into master_long.parquet.

    Without this the refresh only half-lands. `tonnage.stockflow.reconstruct`
    reads the CSVs directly and so goes current immediately, but
    `ml.features.congestion` reads ``PW_<PORT>_CALLS`` / ``_IMPORT_T`` /
    ``_EXPORT_T`` out of the parquet -- those are **model features in the rate
    forecast**. Leaving them behind would mean the tonnage screen advanced
    while the forecast quietly kept using nineteen-day-old congestion.

    Bounded exactly like the rate harvester: only ``PW_``-prefixed series that
    master_long ALREADY carries, and only dates it does not. It never
    introduces a new series -- the 342 series from the extended harvest that
    the parquet has never carried are a separate, deliberate decision, not
    something a daily top-up should make on anyone's behalf.
    """
    import polars as pl

    path = master_path or (REPO_ROOT / "src" / "data" / "master_long.parquet")
    if not path.exists():
        LOGGER.info(f"No master at {path}; port series not updated.")
        return 0

    master = pl.read_parquet(path)
    existing_pw = master.filter(pl.col("series_id").str.starts_with("PW_"))
    if existing_pw.is_empty():
        return 0
    known_series = set(existing_pw["series_id"].unique().to_list())
    known_keys = set(
        zip(existing_pw["series_id"].to_list(), existing_pw["date"].to_list(), strict=True)
    )

    additions: list[dict] = []
    for file in sorted(out_dir.glob("*_daily_portcalls.csv")):
        slug = _series_slug(file)
        wanted = {
            f"PW_{slug}_CALLS": ("portcalls_dry_bulk", "calls"),
            f"PW_{slug}_IMPORT_T": ("import_dry_bulk", "mt"),
            f"PW_{slug}_EXPORT_T": ("export_dry_bulk", "mt"),
        }
        if not any(sid in known_series for sid in wanted):
            continue
        with file.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                day_text = (row.get("date") or "")[:10]
                if not day_text:
                    continue
                try:
                    day = datetime.date.fromisoformat(day_text)
                except ValueError:
                    continue
                for sid, (column, unit) in wanted.items():
                    if sid not in known_series or (sid, day) in known_keys:
                        continue
                    raw_value = row.get(column)
                    if raw_value in (None, ""):
                        continue
                    try:
                        value = float(raw_value)
                    except ValueError:
                        continue
                    additions.append(
                        {
                            "series_id": sid,
                            "date": day,
                            "value": value,
                            "unit": unit,
                            "source": "portwatch",
                        }
                    )

    if not additions:
        return 0

    combined = pl.concat(
        [master, pl.DataFrame(additions, schema=master.schema)], how="vertical"
    ).sort(["series_id", "date"])
    tmp = path.with_suffix(".parquet.tmp")
    combined.write_parquet(tmp)
    tmp.replace(path)
    LOGGER.info(f"master_long: +{len(additions)} port row(s)")
    return len(additions)

def main() -> None:
    _setup_logging()

    LOGGER.info(f"Stage 1: resolving {len(CANDIDATES)} port candidates against PortWatch...")
    resolved, unresolved = resolve_candidates()
    write_port_index(resolved, unresolved)

    LOGGER.info(f"Stage 2: pulling daily port-calls for {len(resolved)} resolved ports...")
    pull_ports(resolved)

    LOGGER.info(f"Stage 3: pulling daily transit data for {len(CHOKEPOINT_IDS)} chokepoints...")
    pull_chokepoints()

    LOGGER.info("harvest complete")


if __name__ == "__main__":
    main()
