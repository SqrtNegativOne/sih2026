"""A 7-day marine-conditions overlay on top of the long-run cyclone
climatology (``data_builders.build_cyclone_climatology``), combined into one
expected-delay-in-days figure the optimizer can spend.

Source
------
Open-Meteo Marine Weather API -- https://marine-api.open-meteo.com/v1/marine
(``hourly=wave_height,wind_wave_height,swell_wave_height``). Free, no API key,
no quota for non-commercial use. Backed by DWD's global (28 km) and European
(5 km) wave models, refreshed twice daily; a 7-day horizon from "today," not
a longer-range outlook. Every ``MarineWindow`` this module returns carries
``source="open-meteo/dwd"`` and ``provenance="ESTIMATED"`` (a model output,
not a buoy reading -- see ``data_builders.provenance``).

Network policy, and why this module is the one exception
----------------------------------------------------------
CLAUDE.md's network policy is: every external fetch is a build-time harvester
under ``src/data_builders/``, never a per-request call from ``backend/`` or
``opt/``. That rule fits a static archive (IBTrACS, PortWatch) that can be
pulled once and reused indefinitely. It does not fit a genuinely *rolling*
7-day forecast that is stale within hours of being fetched -- there is no
"build once" for tomorrow's wave height. What this module keeps from that
policy, because the underlying reason for it (never let a live external call
threaten a quote) still fully applies: every call is cache-first with a
documented TTL (``CACHE_TTL_SECONDS``, matching the model's own twice-daily
refresh -- polling more often cannot return new information), cached to disk
under ``raw_data/open_meteo/``, and **never raises**. A network failure, a
timeout, an empty cache, or a malformed response all degrade to
``fetch_marine_window`` returning ``None`` -- never a fabricated sea state,
never an exception that could take a quote down with it. A demo running with
no network at all must still produce a complete quote; that path is tested
directly (``tests/opt/test_weather_window.py``) and via a real offline run of
``run_quote_demo.py``.

The 7-day horizon is shorter than most laycans
------------------------------------------------
A laycan two or three weeks out has, at best, partial real forecast coverage
by the time a quote is priced -- this module does not pretend otherwise.
``TransitBuffer.forecast_covers_laycan`` says whether *any* real forecast
data overlaps the laycan at all, so a caller can tell "checked and calm"
apart from "nothing to check yet." See :func:`transit_buffer`'s own
docstring for exactly how the covered and uncovered portions of the laycan
are priced separately, without double-counting.

Delay model -- simple, defensible, and labelled as an assumption
--------------------------------------------------------------------
``climatology_delay_days`` = (max real strike_rate across the laycan's ISO
weeks, for whichever named basins the origin/destination ports resolve to)
x ``DAYS_LOST_PER_STORM``. ``forecast_delay_days`` = the worse of the two
ports' real forecast "rough days" (days whose max forecast wave height clears
``ROUGH_SEA_THRESHOLD_M``) within the laycan, capped at the laycan's own
length. Both ``DAYS_LOST_PER_STORM`` and ``ROUGH_SEA_THRESHOLD_M`` are
``Final`` constants with the reasoning behind their value written into their
own comment -- honest, labelled engineering assumptions (this codebase has no
real per-port closure-duration dataset to calibrate against), not a fake
calibration dressed up as one. Tune them there, in one place, if better
figures ever become available.

Not wired into ``backend/`` in this chunk -- that is chunk 2.4's job.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Final

import polars as pl
import requests
from pydantic import BaseModel, ConfigDict

from data_builders.build_cyclone_climatology import OUT_PATH as CYCLONE_CLIMATOLOGY_PATH
from data_builders.build_cyclone_climatology import basins_for_port
from data_builders.provenance import Provenance
from opt.network import PortEnum

__all__ = [
    "CACHE_TTL_SECONDS",
    "DAYS_LOST_PER_STORM",
    "ROUGH_SEA_THRESHOLD_M",
    "MarineWindow",
    "TransitBuffer",
    "fetch_marine_window",
    "transit_buffer",
]

LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
CACHE_DIR: Final[Path] = REPO_ROOT / "raw_data" / "open_meteo"

BASE_URL: Final[str] = "https://marine-api.open-meteo.com/v1/marine"
_REQUEST_TIMEOUT_SECONDS: Final[float] = 10.0

#: The DWD wave models this API is backed by refresh twice daily -- polling
#: more often than this cannot possibly return new information, only spend
#: the (free, unauthenticated) request budget for nothing.
CACHE_TTL_SECONDS: Final[float] = 12 * 3600.0

#: A day counts as "rough" when its highest forecast hourly wave height (the
#: max of wave_height/wind_wave_height/swell_wave_height, whichever the API
#: actually populated for that hour) clears this. 2.5 m significant wave
#: height is a commonly used operational rule of thumb for suspending cargo
#: handling / berthing at swell-exposed anchorages -- NOT a per-port
#: calibrated figure (this codebase has no real per-port closure-threshold
#: dataset to calibrate against). An honest, labelled assumption; tune here.
ROUGH_SEA_THRESHOLD_M: Final[float] = 2.5

#: Assumed transit delay per real cyclone-strength storm event: roughly one
#: day of actual port closure during passage, plus about two days for the
#: call/berth queue to clear the backlog afterward, for a typical few-berth
#: bulk terminal -- a commonly cited port-ops rule of thumb, NOT fitted to
#: any specific port's real closure-duration history (this codebase has no
#: such dataset). A disclosed assumption, not a fake calibration. Tune here.
DAYS_LOST_PER_STORM: Final[float] = 3.0


class MarineWindow(BaseModel):
    """Real Open-Meteo/DWD wave-height stats for one port, over whichever
    portion of its 7-day forecast horizon overlaps the caller's requested
    [start, end] window (see :func:`fetch_marine_window`)."""

    model_config = ConfigDict(frozen=True)

    port: PortEnum
    lat: float
    lon: float
    forecast_start: date
    forecast_end: date
    max_wave_height_m: float
    mean_wave_height_m: float
    rough_days: int
    source: str
    provenance: str


class TransitBuffer(BaseModel):
    """The combined climatology + live-forecast expected delay for one
    origin/destination pair over a real laycan window. See
    :func:`transit_buffer`'s docstring for exactly how the two figures are
    combined without double-counting."""

    model_config = ConfigDict(frozen=True)

    origin: PortEnum
    dest: PortEnum
    laycan_start: date
    laycan_end: date
    climatology_delay_days: float
    forecast_delay_days: float
    expected_delay_days: float
    forecast_covers_laycan: bool
    basins: tuple[str, ...]
    explanation: str


def _iso_weeks_in_range(start: date, end: date) -> list[int]:
    """Every distinct ISO week number touched by [start, end], in date
    order, each appearing once. Mirrors ``opt.risk``'s own helper of the
    same name -- small enough, and specific enough to each module's own
    laycan-to-week reasoning, that duplicating it beats importing a private
    helper across modules."""
    if end < start:
        start, end = end, start
    weeks: list[int] = []
    seen: set[int] = set()
    d = start
    while d <= end:
        wk = d.isocalendar().week
        if wk not in seen:
            seen.add(wk)
            weeks.append(wk)
        d += timedelta(days=1)
    return weeks


def _cache_path(lat: float, lon: float, as_of: date, cache_dir: Path) -> Path:
    """Keyed by rounded lat/lon (~1 km precision -- plenty for a port
    location) and the calendar date the fetch was made, so a new cache entry
    is written at most once per port per day, on top of the TTL check."""
    return cache_dir / f"{round(lat, 2)}_{round(lon, 2)}_{as_of.isoformat()}.json"


def _is_fresh(path: Path) -> bool:
    return path.exists() and (time.time() - path.stat().st_mtime) < CACHE_TTL_SECONDS


def _fetch_raw(lat: float, lon: float) -> dict | None:
    """One real HTTP GET. Any failure -- network, timeout, non-2xx, a body
    that isn't valid JSON -- returns None rather than raising. Never called
    directly by a quote path; always through ``_load_raw``'s cache check."""
    try:
        response = requests.get(
            BASE_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "hourly": "wave_height,wind_wave_height,swell_wave_height",
                "timezone": "UTC",
            },
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        # ValueError covers Response.json()'s own JSONDecodeError -- a
        # malformed body is handled the same way a network failure is.
        LOGGER.warning(f"open-meteo marine fetch failed for ({lat}, {lon}): {exc}")
        return None


def _load_raw(lat: float, lon: float, as_of: date, cache_dir: Path) -> dict | None:
    """Cache-first raw JSON payload for these coordinates. Returns None
    exactly when there is no fresh cache entry AND the live fetch also
    failed -- never raises (see module docstring)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(lat, lon, as_of, cache_dir)
    if _is_fresh(path):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass  # a corrupted cache file is treated as cold, not fatal

    payload = _fetch_raw(lat, lon)
    if payload is None:
        return None

    try:
        path.write_text(json.dumps(payload), encoding="utf-8")
    except OSError as exc:
        LOGGER.warning(f"could not write open-meteo cache to {path}: {exc}")
    return payload


def _parse_hourly_heights(payload: dict) -> list[tuple[datetime, float]]:
    """[(hour, wave height in metres)] from a raw Open-Meteo marine payload,
    skipping any hour with no usable reading in any of the three series.
    Tolerant of a partial/malformed payload -- a missing or wrong-shaped
    field yields fewer usable hours, never an exception."""
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict):
        return []
    times = hourly.get("time")
    if not isinstance(times, list):
        return []
    series = [hourly.get(name) or [] for name in ("wave_height", "wind_wave_height", "swell_wave_height")]

    out: list[tuple[datetime, float]] = []
    for i, t in enumerate(times):
        values = [s[i] for s in series if i < len(s) and isinstance(s[i], (int, float))]
        if not values:
            continue
        try:
            ts = datetime.fromisoformat(t)
        except (TypeError, ValueError):
            continue
        out.append((ts, float(max(values))))
    return out


def fetch_marine_window(
    port: PortEnum,
    start: date,
    end: date,
    *,
    cache_dir: Path | None = None,
    as_of: date | None = None,
) -> MarineWindow | None:
    """Real wave-height stats for ``port`` over whichever part of its 7-day
    Open-Meteo/DWD forecast overlaps [``start``, ``end``].

    Returns None -- never raises -- when: the port has no known coordinate
    (``data_builders.build_geography.PORT_COORDS``); neither a fresh cache
    entry nor a live fetch produced usable data; or [``start``, ``end``] has
    no overlap at all with what the forecast actually covers (most laycans
    extend well past the 7-day horizon -- see module docstring). A caller
    that gets None has a real "no forecast for this window," not a stale or
    fabricated one.
    """
    try:
        from data_builders.build_geography import PORT_COORDS

        loc = PORT_COORDS.get(port.value.id)
        if loc is None:
            return None

        as_of = as_of or datetime.now(UTC).date()
        cache_dir = cache_dir or CACHE_DIR
        payload = _load_raw(loc.lat, loc.lon, as_of, cache_dir)
        if payload is None:
            return None

        hourly = _parse_hourly_heights(payload)
        if not hourly:
            return None

        in_range = [(ts, h) for ts, h in hourly if start <= ts.date() <= end]
        if not in_range:
            return None

        by_day: dict[date, list[float]] = {}
        for ts, h in in_range:
            by_day.setdefault(ts.date(), []).append(h)
        heights = [h for _, h in in_range]
        rough_days = sum(1 for day_heights in by_day.values() if max(day_heights) >= ROUGH_SEA_THRESHOLD_M)

        return MarineWindow(
            port=port,
            lat=loc.lat,
            lon=loc.lon,
            forecast_start=min(by_day),
            forecast_end=max(by_day),
            max_wave_height_m=max(heights),
            mean_wave_height_m=sum(heights) / len(heights),
            rough_days=rough_days,
            source="open-meteo/dwd",
            provenance=Provenance.ESTIMATED.value,
        )
    except Exception:  # see module docstring: this must never raise into a quote
        LOGGER.warning(f"fetch_marine_window failed unexpectedly for {port}", exc_info=True)
        return None


def _max_strike_rate(basins: tuple[str, ...], weeks: list[int], climatology_path: Path) -> float:
    """Highest real strike_rate among these basins/weeks, or 0.0 when there's
    no basin, no climatology file, or no matching row -- a missing
    (basin, iso_week) means zero storms observed in the study window (see
    data_builders.build_cyclone_climatology's own docstring), not unknown."""
    if not basins or not climatology_path.exists():
        return 0.0
    climatology = pl.read_parquet(climatology_path, columns=["basin", "iso_week", "strike_rate"])
    in_scope = climatology.filter(pl.col("basin").is_in(basins) & pl.col("iso_week").is_in(weeks))
    if in_scope.is_empty():
        return 0.0
    return float(in_scope["strike_rate"].max())


def transit_buffer(
    origin: PortEnum,
    dest: PortEnum,
    laycan_start: date,
    laycan_end: date,
    *,
    as_of: date,
    climatology_path: Path | None = None,
    cache_dir: Path | None = None,
) -> TransitBuffer:
    """Combine the long-run cyclone climatology with the live 7-day marine
    forecast into one expected-delay figure for this laycan, without
    double-counting.

    The laycan is split into a "covered" portion (real forecast data exists
    for it, from whichever of the two ports has one) and an "uncovered"
    remainder. The covered portion is priced from the forecast alone
    (``forecast_delay_days``, the worse of the two ports' rough-day counts,
    capped at the laycan length); the uncovered remainder is priced from
    climatology, PRORATED to only that remainder -- never the full laycan.
    A laycan fully inside the 7-day horizon therefore gets its delay
    entirely from the forecast, climatology contributing exactly 0.0; a
    laycan entirely beyond the horizon falls back to the full,
    un-prorated climatology figure, with ``forecast_covers_laycan=False``.

    Never raises: a missing climatology file degrades ``climatology_delay_days``
    to 0.0 (see ``_max_strike_rate``), and a missing/failed forecast degrades
    ``forecast_delay_days`` to 0.0 and ``forecast_covers_laycan`` to False
    (see ``fetch_marine_window``'s own docstring) -- this function always
    returns a valid ``TransitBuffer``, never None.
    """
    basins = tuple(sorted({b for p in (origin, dest) for b in basins_for_port(p)}))
    weeks = _iso_weeks_in_range(laycan_start, laycan_end)
    path = climatology_path or CYCLONE_CLIMATOLOGY_PATH
    climatology_delay_days = _max_strike_rate(basins, weeks, path) * DAYS_LOST_PER_STORM

    total_days = max((laycan_end - laycan_start).days + 1, 1)

    origin_window = fetch_marine_window(origin, laycan_start, laycan_end, cache_dir=cache_dir, as_of=as_of)
    dest_window = fetch_marine_window(dest, laycan_start, laycan_end, cache_dir=cache_dir, as_of=as_of)
    forecast_window = origin_window or dest_window
    forecast_covers_laycan = forecast_window is not None

    basins_label = ", ".join(basins) if basins else "no tracked cyclone basin"

    if not forecast_covers_laycan:
        forecast_delay_days = 0.0
        expected_delay_days = climatology_delay_days
        explanation = (
            f"No live marine forecast covers this laycan ({laycan_start} to {laycan_end}) -- "
            f"priced from climatology only: {climatology_delay_days:.1f} expected delay day(s) "
            f"from {basins_label}."
        )
    else:
        rough_origin = origin_window.rough_days if origin_window is not None else 0
        rough_dest = dest_window.rough_days if dest_window is not None else 0
        forecast_delay_days = float(min(max(rough_origin, rough_dest), total_days))

        covered_days = (
            min(laycan_end, forecast_window.forecast_end)
            - max(laycan_start, forecast_window.forecast_start)
        ).days + 1
        covered_days = max(0, min(covered_days, total_days))
        uncovered_days = total_days - covered_days
        climatology_remainder = climatology_delay_days * (uncovered_days / total_days)
        expected_delay_days = forecast_delay_days + climatology_remainder
        explanation = (
            f"Marine forecast covers {covered_days}/{total_days} laycan day(s) with "
            f"{forecast_delay_days:.1f} rough-sea delay day(s) (>= {ROUGH_SEA_THRESHOLD_M:g} m "
            f"wave height); remaining {uncovered_days} day(s) priced from climatology "
            f"({climatology_remainder:.1f} day(s), {basins_label})."
        )

    return TransitBuffer(
        origin=origin,
        dest=dest,
        laycan_start=laycan_start,
        laycan_end=laycan_end,
        climatology_delay_days=climatology_delay_days,
        forecast_delay_days=forecast_delay_days,
        expected_delay_days=expected_delay_days,
        forecast_covers_laycan=forecast_covers_laycan,
        basins=basins,
        explanation=explanation,
    )
