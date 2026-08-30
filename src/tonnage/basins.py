"""Basin partition and inter-basin transit-time model.

The 129-port Tonnage Field harvest (``raw_data/portwatch/port_index_extended.csv``,
built in P1) tags every port with one of three basins -- ``pacific``, ``atlantic``,
``indian_ocean``. This is deliberately coarse rather than matched to Signal Ocean's
finer sub-regions (their weekly ballaster-count snapshots use tags like "SE Africa" or
"NOPAC"): it is the standard shipbroker three-way split, and it is what the *majority*
of the Signal weekly archive's own route codes use (Atlantic vs Pacific round voyages,
e.g. "P1A_82 transatlantic" vs "P2A_82 Pacific"). See ``tonnage.validate`` for how the
few finer-grained ballaster snapshots get mapped onto these three basins for comparison.

This module does not invent a basin partition -- it loads the one already assigned in
P1 -- and it does not invent transit distances. Basin-to-basin transit time comes from
``data_builders.build_geography.sea_distance_nm``, the same searoute-backed, tested sea
routing used by the optimizer's own distance matrix, applied to a *representative real
port* per basin rather than a synthetic centroid.
"""
from __future__ import annotations

import csv
import functools
import statistics
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final

from data_builders.build_geography import PortLocation, great_circle_nm, sea_distance_nm

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
PORT_INDEX_PATH: Final[Path] = REPO_ROOT / "raw_data" / "portwatch" / "port_index_extended.csv"


class Basin(str, Enum):
    PACIFIC = "pacific"
    ATLANTIC = "atlantic"
    INDIAN_OCEAN = "indian_ocean"


@dataclass(frozen=True)
class PortInfo:
    label: str
    portid: str
    portname: str
    country: str
    iso3: str
    lat: float
    lon: float
    role: str  # "coal", "iron_ore", "grain", "bauxite", "import_hub"
    basin: Basin

    def to_location(self) -> PortLocation:
        return PortLocation(self.portid, self.lat, self.lon, "tonnage_field")


class PortIndexMissingError(RuntimeError):
    """Raised when the P1 harvest's port index has not been built yet."""


#: The 14 ports pulled by the *original*, pre-P1 harvest (see ``PULL_NOTES.md``) --
#: ``_port_candidates.py`` deliberately excludes them from the P1 candidate list, so
#: they never got a basin/role tag in ``port_index_extended.csv`` even though their
#: daily port-call CSVs are real and already on disk. Six of these are the actual
#: EC-India discharge ports the problem statement names (Paradip, Visakhapatnam,
#: Gopalpur, Dhamra, Haldia, Kolkata) -- the Tonnage Field's port universe is
#: incomplete, in exactly the place that matters most, without them.
#:
#: Coordinates are the real values from ``raw_data/portwatch/port_coords.csv``
#: (the original harvest's own coordinate pull). Role and basin are assigned here
#: using the same real-trade-knowledge basis as ``_port_candidates.CANDIDATES``.
_ORIGINAL_HARVEST_PORTS: Final[tuple[PortInfo, ...]] = (
    PortInfo("Paradip", "port883", "Paradip", "India", "IND", 20.28045307, 86.64892168, "import_hub", Basin.INDIAN_OCEAN),
    PortInfo("Visakhapatnam", "port1367", "Visakhapatnam", "India", "IND", 17.65545047, 83.23140922, "import_hub", Basin.INDIAN_OCEAN),
    PortInfo("Gopalpur", "port2299", "Gopalpur", "India", "IND", 19.29112738, 84.95741871, "import_hub", Basin.INDIAN_OCEAN),
    PortInfo("Dhamra", "port290", "Dhamra Port", "India", "IND", 20.82773698, 86.9595015, "import_hub", Basin.INDIAN_OCEAN),
    PortInfo("Haldia", "port442", "Haldia", "India", "IND", 22.0577906, 88.10419444, "import_hub", Basin.INDIAN_OCEAN),
    PortInfo("Kolkata", "port207", "Kolkata (Syama Prasad Mookerje Port)", "India", "IND", 22.53574401, 88.29958111, "import_hub", Basin.INDIAN_OCEAN),
    PortInfo("Newcastle_AU", "port816", "Newcastle", "Australia", "AUS", -32.89445588, 151.7577471, "coal", Basin.PACIFIC),
    PortInfo("Hay_Point_AU", "port458", "Hay Point", "Australia", "AUS", -21.28124079, 149.287454, "coal", Basin.PACIFIC),
    PortInfo("Balikpapan_ID", "port102", "Balikpapan", "Indonesia", "IDN", -1.202534486, 116.79598, "coal", Basin.PACIFIC),
    PortInfo("Samarinda_ID", "port1137", "Samarinda", "Indonesia", "IDN", -0.551281498, 117.1922256, "coal", Basin.PACIFIC),
    PortInfo("Richards_Bay_ZA", "port1099", "Richards Bay", "South Africa", "ZAF", -28.79239882, 32.03937272, "coal", Basin.INDIAN_OCEAN),
    PortInfo("Beira_MZ", "port137", "Beira", "Mozambique", "MOZ", -19.8110704, 34.83983895, "coal", Basin.INDIAN_OCEAN),
    PortInfo("Nacala_MZ", "port784", "Nacala", "Mozambique", "MOZ", -14.53535059, 40.65118404, "coal", Basin.INDIAN_OCEAN),
    PortInfo("Maputo_MZ", "port702", "Maputo", "Mozambique", "MOZ", -25.95563087, 32.52103798, "mixed", Basin.INDIAN_OCEAN),
)


@functools.lru_cache(maxsize=1)
def load_port_index(path: Path | None = None) -> tuple[PortInfo, ...]:
    """Every resolved port with a basin and commodity role: P1's 129 plus the
    original harvest's 14.

    Cached: this file is read-only for the lifetime of a process and is looked up
    per-port throughout the tonnage pipeline.
    """
    p = path or PORT_INDEX_PATH
    if not p.exists():
        raise PortIndexMissingError(
            f"{p} not found. Build it with:\n    python -m data_builders.harvest_portwatch"
        )
    out: list[PortInfo] = list(_ORIGINAL_HARVEST_PORTS)
    with p.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["resolved"] != "yes":
                continue
            out.append(
                PortInfo(
                    label=row["label"],
                    portid=row["portid"],
                    portname=row["portname"],
                    country=row["country"],
                    iso3=row["iso3"],
                    lat=float(row["lat"]),
                    lon=float(row["lon"]),
                    role=row["role"],
                    basin=Basin(row["basin"]),
                )
            )
    return tuple(out)


def ports_in_basin(basin: Basin, path: Path | None = None) -> tuple[PortInfo, ...]:
    return tuple(p for p in load_port_index(path) if p.basin == basin)


def port_csv_path(label: str) -> Path:
    """Path to a port's daily dry-bulk port-call CSV, by label."""
    return REPO_ROOT / "raw_data" / "portwatch" / f"{label}_daily_portcalls.csv"


def port_by_label(label: str, path: Path | None = None) -> PortInfo:
    for p in load_port_index(path):
        if p.label == label:
            return p
    raise KeyError(f"No resolved port with label {label!r} in the Tonnage Field port index.")


@functools.cache
def basin_representative_port(basin: Basin, path_str: str = "") -> PortInfo:
    """The basin's own real port closest to the mean position of all its ports.

    Not a synthetic centroid -- centroids of a scattered coastline routinely land on
    land or in the wrong sub-region. This picks an actual harvested port, so it is
    always a valid, resolvable routing endpoint.
    """
    path = Path(path_str) if path_str else None
    ports = ports_in_basin(basin, path)
    if not ports:
        raise PortIndexMissingError(f"No ports resolved for basin {basin!r}.")
    mean_lat = statistics.fmean(p.lat for p in ports)
    mean_lon = statistics.fmean(p.lon for p in ports)
    centroid = PortLocation("centroid", mean_lat, mean_lon, "synthetic")
    return min(ports, key=lambda p: great_circle_nm(p.to_location(), centroid))


@functools.cache
def _basin_pair_distance_nm(basin_a: Basin, basin_b: Basin, path_str: str = "") -> float:
    """Representative sea distance in nm between two basins (or within one)."""
    path = Path(path_str) if path_str else None
    a = basin_representative_port(basin_a, path_str)
    if basin_a == basin_b:
        # Intra-basin transit is still a real voyage, not zero -- and using the single
        # farthest port overstates it for a basin as geographically large as "pacific"
        # (spans US West Coast to Australia to NE Asia). The median distance from the
        # representative hub to every other port in the basin is a real, data-derived
        # "typical repositioning leg" rather than a worst case.
        ports = [p for p in ports_in_basin(basin_a, path) if p.portid != a.portid]
        if not ports:
            return 0.0
        legs = [sea_distance_nm(a.to_location(), p.to_location())[0] for p in ports]
        return statistics.median(legs)
    b = basin_representative_port(basin_b, path_str)
    nm, _method = sea_distance_nm(a.to_location(), b.to_location())
    return nm


def basin_transit_hours(basin_a: Basin, basin_b: Basin, speed_kn: float, path: Path | None = None) -> float:
    """Hours to sail between the representative ports of two basins at ``speed_kn``.

    Symmetric by construction (the underlying distance lookup is unordered).
    """
    if speed_kn <= 0:
        raise ValueError(f"speed_kn must be positive, got {speed_kn}")
    path_str = str(path) if path else ""
    nm = _basin_pair_distance_nm(basin_a, basin_b, path_str)
    return nm / speed_kn


def clear_cache() -> None:
    """Drop cached lookups. Call after rebuilding the port index within a live process."""
    load_port_index.cache_clear()
    basin_representative_port.cache_clear()
    _basin_pair_distance_nm.cache_clear()
