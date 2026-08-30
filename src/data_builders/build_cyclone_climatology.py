"""Reduce the raw IBTrACS archive into a per-basin, per-ISO-week cyclone
strike climatology the optimizer can look up offline.

Not wired into the optimizer in this chunk -- data layer only. See
``data_builders.harvest_ibtracs`` for how the raw archive gets to disk.

Basin definition -- the important design decision
---------------------------------------------------
IBTrACS's own ``BASIN`` column ("NI", "SP", "SI", "WP", "EP", "NA", ...) is
too coarse: it groups the whole North Indian Ocean into one basin, which
mixes the Bay of Bengal (where this fleet's east-coast-India discharge ports
sit) with the Arabian Sea (where none of them do), and says nothing about
which stretch of the Australian, Mozambican, or Indonesian coast a storm
actually threatens. Instead, ``BASIN_BOUNDS`` below defines explicit lat/lon
bounding boxes named after the port groups this fleet actually trades on --
``src/ml/live_forecast.py``'s ``CONGESTION_ORIGIN_PORTS`` /
``CONGESTION_DEST_PORTS`` -- so the climatology can be joined straight onto a
real quote's origin/destination later (:func:`basins_for_port`).

These boxes are practitioner-standard geographic conventions (the India
Meteorological Department's Bay of Bengal / Arabian Sea split at ~77E; the
commonly used Mozambique Channel bounds between the African mainland and
Madagascar), not drawn from one single authoritative citation -- each has an
inline comment explaining its choice, and this is disclosed rather than
implied to be more precise than it is. The boxes are disjoint; a fix can
still map to zero basins (most of the planet is not one of these five named
lanes) or, if a future box is added carelessly, more than one -- callers get
a list, never a bare string.

Satellite-era cutoff
---------------------
Restricted to ``MIN_SEASON = 1980`` onward. Pre-satellite tracks
undercount storms -- IBTrACS itself documents that global coverage before
routine geostationary satellite imagery (mid-1960s onward, and not reliably
global until nearer 1980) is inconsistent by basin and era, so a climatology
built across the full 1842-present record would understate strike_rate for
the earlier decades it can't see uniformly. 1980 is the same cutoff commonly
used for "modern era" tropical cyclone climatology work for this reason.

Wind-speed limitation, disclosed rather than hidden
-----------------------------------------------------
IBTrACS carries per-agency wind columns (WMO_WIND, USA_WIND, TOKYO_WIND, ...);
a storm is often reported by only one or two agencies for a given fix. This
module uses ``coalesce(WMO_WIND, USA_WIND)`` -- the WMO-designated agency's
figure where present, the US agency's (JTWC/NHC) otherwise -- NOT every
agency's column. A storm reported only by, say, the Japan Meteorological
Agency (TOKYO_WIND) with neither WMO_WIND nor USA_WIND populated still counts
toward ``storm_count`` (it has a real fix in the box+week) but contributes no
value to ``mean_max_wind_kn``/``p90_max_wind_kn`` (both aggregates skip nulls).
This under-covers wind for storms reported only by regional agencies outside
Atlantic/Eastern-Pacific and the WMO-designated agency -- most relevant to
this fleet's basins, since West Pacific/Australian-region storms often carry
only BOM_WIND/TOKYO_WIND. Extending the coalesce to every regional column is
future scope, not attempted here.

Provenance
----------
Individual IBTrACS fixes are OBSERVED (a real best-track analysis of a real
storm). This table -- storm counts and wind statistics aggregated over a
basin/week bucket -- is MODEL_DERIVED: a computation over real inputs, never
inheriting IBTrACS's own OBSERVED label. See ``data_builders.provenance``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

import polars as pl

from data_builders.provenance import Provenance

if TYPE_CHECKING:
    # Type-only: opt.network is part of the opt PACKAGE, so a module-level
    # `from opt.network import PortEnum` forces Python to first run all of
    # opt/__init__.py -- which (via opt.api -> ... -> opt.types ->
    # opt.weather_window) imports THIS module back, for its OUT_PATH
    # constant, before OUT_PATH is defined -- a real circular-import crash,
    # not hypothetical (hit live: `pytest tests/data_builders -q` run in
    # isolation, so no earlier-collected test file had already primed
    # opt's import chain first). basins_for_port below never touches
    # PortEnum as a runtime value (only `port.value.id` on its parameter),
    # so guarding the import under TYPE_CHECKING keeps full static-type
    # coverage (mypy/pyright still resolve the annotation) while making
    # zero difference to what actually runs -- the standard fix for a
    # type-only import that would otherwise create a real cycle.
    from opt.network import PortEnum

LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
RAW_CSV_PATH: Final[Path] = REPO_ROOT / "raw_data" / "ibtracs" / "ibtracs.ALL.list.v04r01.csv"
OUT_PATH: Final[Path] = REPO_ROOT / "src" / "data" / "cyclone_climatology.parquet"

#: Satellite-era cutoff -- see this module's own docstring for why.
MIN_SEASON: Final[int] = 1980

#: This table's own provenance -- a computation over real (OBSERVED) IBTrACS
#: fixes, never inheriting that label. See data_builders.provenance.
CLIMATOLOGY_PROVENANCE: Final[Provenance] = Provenance.MODEL_DERIVED


@dataclass(frozen=True)
class BasinBox:
    """A named lat/lon bounding box and the reasoning behind its bounds."""

    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float
    note: str

    def contains(self, lat: float, lon: float) -> bool:
        return self.lat_min <= lat <= self.lat_max and self.lon_min <= lon <= self.lon_max


#: Named after src/ml/live_forecast.py's CONGESTION_ORIGIN_PORTS /
#: CONGESTION_DEST_PORTS groupings -- see this module's own docstring for why
#: IBTrACS's own coarse BASIN column isn't used instead.
BASIN_BOUNDS: Final[dict[str, BasinBox]] = {
    "BAY_OF_BENGAL": BasinBox(
        lat_min=5.0, lat_max=23.0, lon_min=77.0, lon_max=95.0,
        note=(
            "IMD's operational Bay of Bengal sub-basin (north Indian Ocean east of "
            "~77E). Covers every CONGESTION_DEST_PORTS discharge port with margin: "
            "Paradip 20.28N/86.65E, Vizag 17.66N/83.23E, Gopalpur 19.29N/84.96E, "
            "Dhamra 20.83N/86.96E, Haldia 22.06N/88.10E, Kolkata ~22.57N/88.36E."
        ),
    ),
    "ARABIAN_SEA": BasinBox(
        lat_min=5.0, lat_max=25.0, lon_min=60.0, lon_max=76.9,
        note=(
            "IMD's Arabian Sea sub-basin (north Indian Ocean west of ~77E), mirroring "
            "the Bay of Bengal split above. lon_max stops at 76.9, not 77.0, so this "
            "box and BAY_OF_BENGAL's lon_min=77.0 don't share a boundary value -- both "
            "are half-open in effect (BasinBox.contains is inclusive on both ends), so "
            "an exact match on the shared coordinate would otherwise double-count. No "
            "port in opt.network.PortEnum currently sits in this box -- India's west "
            "coast is not a served lane in this optimizer yet; kept here as a "
            "disclosed gap, not silently omitted."
        ),
    ),
    "NE_AUSTRALIA": BasinBox(
        lat_min=-35.0, lat_max=-18.0, lon_min=147.0, lon_max=154.0,
        note=(
            "The eastern-Australia coal-export coast this fleet actually trades on: "
            "Newcastle NSW (-32.89, 151.76) down to Hay Point / Abbot Point, QLD "
            "(~-21S). Named NE_AUSTRALIA to match CONGESTION_ORIGIN_PORTS's own "
            "NEWCASTLE_AU/HAY_POINT_AU grouping, even though Newcastle itself sits in "
            "the country's south-east rather than its north-east."
        ),
    ),
    "MOZAMBIQUE_CHANNEL": BasinBox(
        lat_min=-26.0, lat_max=-10.0, lon_min=34.0, lon_max=50.0,
        note=(
            "Standard Mozambique Channel bounds, between the African mainland and "
            "Madagascar. Covers Beira (-19.81, 34.84) and the Maputo/Nacala origin "
            "labels in CONGESTION_ORIGIN_PORTS. Richards Bay, South Africa "
            "(-28.79, 32.04) deliberately falls outside (south of lat_min) -- it is a "
            "different cyclone regime (open South-West Indian Ocean), not the Channel."
        ),
    ),
    "SE_ASIA": BasinBox(
        lat_min=-3.0, lat_max=3.0, lon_min=114.0, lon_max=119.0,
        note=(
            "East Kalimantan coal coast (Makassar Strait) -- Balikpapan "
            "(-1.20, 116.80) and the Samarinda/Muara_Pantai proxy (-0.55, 117.19), "
            "matching CONGESTION_ORIGIN_PORTS's BALIKPAPAN_ID/SAMARINDA_ID."
        ),
    ),
}


def basins_for_coords(lat: float, lon: float) -> list[str]:
    """Every named basin whose box contains this point, in BASIN_BOUNDS's
    definition order. Usually zero or one (the boxes are disjoint); a list,
    never a bare string, so an overlapping future box degrades gracefully."""
    return [name for name, box in BASIN_BOUNDS.items() if box.contains(lat, lon)]


def basins_for_port(port: PortEnum) -> list[str]:
    """Same as :func:`basins_for_coords`, looked up from a real PortEnum via
    the same coordinate table opt.geography's distance matrix was built from
    (data_builders.build_geography.PORT_COORDS). Empty list -- not an error --
    for a port with no PORT_COORDS entry or one outside every named basin."""
    from data_builders.build_geography import PORT_COORDS

    loc = PORT_COORDS.get(port.value.id)
    if loc is None:
        return []
    return basins_for_coords(loc.lat, loc.lon)


#: Only the columns this reduction actually needs -- selected at read time
#: rather than after loading, since the real file carries ~170 columns across
#: every reporting agency and is hundreds of MB.
_NEEDED_COLUMNS: Final[list[str]] = ["SID", "SEASON", "ISO_TIME", "LAT", "LON", "WMO_WIND", "USA_WIND"]

_OUTPUT_SCHEMA: Final[list[str]] = [
    "basin", "iso_week", "storm_count", "years_covered",
    "strike_rate", "mean_max_wind_kn", "p90_max_wind_kn",
]


def _load_fixes(csv_path: Path) -> pl.DataFrame:
    """Read the raw IBTrACS ALL-list CSV, restricted to the columns and
    satellite-era seasons this reduction needs.

    ``skip_rows_after_header=1`` drops IBTrACS's own units row (e.g.
    "Year","BASIN",...,"deg","deg",... immediately under the header) -- a
    real row that is not a real fix, the same quirk
    ``data_builders.harvest_ibtracs._count_data_rows`` accounts for.
    """
    df = pl.read_csv(
        csv_path,
        columns=_NEEDED_COLUMNS,
        skip_rows_after_header=1,
        null_values=[""],
        schema_overrides={
            "SID": pl.Utf8, "SEASON": pl.Int32, "ISO_TIME": pl.Utf8,
            "LAT": pl.Float64, "LON": pl.Float64,
            "WMO_WIND": pl.Float64, "USA_WIND": pl.Float64,
        },
    )
    df = df.filter(pl.col("SEASON") >= MIN_SEASON)
    df = df.with_columns(
        pl.col("ISO_TIME").str.to_datetime(strict=False).alias("_ts"),
        pl.coalesce(["WMO_WIND", "USA_WIND"]).alias("_wind_kn"),
    )
    df = df.drop_nulls(subset=["_ts", "LAT", "LON"])
    df = df.with_columns(pl.col("_ts").dt.week().alias("iso_week"))
    return df


def build_climatology(csv_path: Path = RAW_CSV_PATH) -> pl.DataFrame:
    """Reduce the raw archive to one row per (basin, iso_week).

    A (basin, iso_week) with zero storms in the study window produces no
    row at all (a sparse table) -- a caller looking up a basin/week absent
    from the output should treat it as strike_rate 0.0, not an error; see
    this module's own docstring for why storm_count can be nonzero while a
    wind figure is null (a storm reported only by a regional agency this
    reduction doesn't coalesce).
    """
    fixes = _load_fixes(csv_path)
    years_covered = fixes["SEASON"].n_unique()
    if years_covered == 0:
        raise ValueError(f"No fixes at or after season {MIN_SEASON} in {csv_path} -- nothing to build.")

    weekly_frames: list[pl.DataFrame] = []
    for basin, box in BASIN_BOUNDS.items():
        in_box = fixes.filter(
            (pl.col("LAT") >= box.lat_min) & (pl.col("LAT") <= box.lat_max)
            & (pl.col("LON") >= box.lon_min) & (pl.col("LON") <= box.lon_max)
        )
        if in_box.is_empty():
            continue

        # Per-storm max wind within THIS basin+week bucket (not the storm's
        # whole-lifetime max) -- "how strong was this storm typically while
        # it was here, this time of year," which is what a per-bucket row
        # can honestly answer.
        per_storm = in_box.group_by(["iso_week", "SID"]).agg(
            pl.col("_wind_kn").max().alias("_storm_max_wind")
        )
        per_week = per_storm.group_by("iso_week").agg(
            pl.len().alias("storm_count"),
            pl.col("_storm_max_wind").mean().alias("mean_max_wind_kn"),
            pl.col("_storm_max_wind").quantile(0.9).alias("p90_max_wind_kn"),
        )
        per_week = per_week.with_columns(
            pl.lit(basin).alias("basin"),
            pl.lit(years_covered, dtype=pl.Int64).alias("years_covered"),
        )
        weekly_frames.append(per_week)

    if not weekly_frames:
        raise ValueError(f"No fix in {csv_path} fell inside any BASIN_BOUNDS box -- nothing to build.")

    result = pl.concat(weekly_frames, how="vertical")
    result = result.with_columns(
        (pl.col("storm_count") / pl.col("years_covered")).alias("strike_rate")
    )
    result = result.select(_OUTPUT_SCHEMA).sort(["basin", "iso_week"])
    return result


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    LOGGER.info(f"reducing {RAW_CSV_PATH} (seasons >= {MIN_SEASON}) ...")
    climatology = build_climatology()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    climatology.write_parquet(OUT_PATH)
    LOGGER.info(f"wrote {OUT_PATH} ({climatology.height} basin/week rows)")


if __name__ == "__main__":
    main()
