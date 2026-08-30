"""Build the sea-distance matrix between every pair of ports we can call at.

Why this replaces the hardcoded table
-------------------------------------
``opt.network.RouteEnum`` carried 47 hand-entered distances for 15 ports. Fifteen
ports have 105 unordered pairs, so 55% of them were missing -- and both callers
answered a miss with a silent constant::

    opt/voyage.py          return 10_000.0
    opt/repositioning.py   return  5_000.0

That number is not cosmetic. It sets transit time, fuel burn, laycan feasibility and
the sign of the objective, so more than half the fleet/cargo combinations were being
priced against a fabricated distance -- and the two engines fabricated *different*
ones, so the scheduler and the repositioner disagreed about the same leg.

Approach
--------
Distances come from ``searoute``, which runs Dijkstra over the Eurostat marine network
graph and therefore routes around land and through the canals rather than taking a
great-circle shortcut. Every pair is computed once and cached to
``src/data/port_distances.parquet``; ``opt.geography`` reads that and raises on a miss
instead of inventing a number.

Coordinate provenance
---------------------
Positions are taken from the IMF PortWatch ports database wherever the port exists
there, keyed by the same ``portid`` used for the congestion pull, so the geography and
the traffic data describe the same place. Three ports are not in PortWatch and carry
documented approximations -- see ``PORT_COORDS`` below. Their ``source`` field says so,
and :func:`compare_with_legacy` reports how far each approximation moves a distance
relative to the hand-entered table it replaces.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Final

import polars as pl

LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
DATA_OUT: Final[Path] = REPO_ROOT / "src" / "data"
OUT_PATH: Final[Path] = DATA_OUT / "port_distances.parquet"


@dataclass(frozen=True)
class PortLocation:
    """A callable port with a position and a record of where the position came from."""

    port_id: str
    lat: float
    lon: float
    source: str

    @property
    def is_approximate(self) -> bool:
        return self.source.startswith("approx:")


#: Keys are ``opt.network.Port.id`` values so the matrix joins straight onto PortEnum.
#:
#: ``portwatch:<portid>`` entries were fetched from the IMF PortWatch ports database
#: (see raw_data/portwatch/port_coords.csv) and are authoritative.
#:
#: ``approx:`` entries are not in PortWatch and are documented individually. PortWatch
#: has no Gangavaram or Sandheads entry at all -- confirmed by name and bounding-box
#: search, and already noted in raw_data/portwatch/PULL_NOTES.md.
PORT_COORDS: Final[dict[str, PortLocation]] = {
    # --- East Coast India discharge ports (the seven named in the problem statement)
    "Paradip": PortLocation("Paradip", 20.2805, 86.6489, "portwatch:port883"),
    "Vizag": PortLocation("Vizag", 17.6555, 83.2314, "portwatch:port1367"),
    # Gangavaram is a separate deep-draft port ~15 km south of Visakhapatnam. PortWatch
    # folds its traffic into Visakhapatnam, so the position is taken from public port
    # references rather than the database.
    "Gangavaram": PortLocation("Gangavaram", 17.6167, 83.2333, "approx:public-reference"),
    "Gopalpur": PortLocation("Gopalpur", 19.2911, 84.9574, "portwatch:port2299"),
    "Dhamra": PortLocation("Dhamra", 20.8277, 86.9595, "portwatch:port290"),
    # Sandheads is the pilot station and lightering anchorage seaward of the Hooghly,
    # not a berth. Position is the anchorage area; it is where Capesize parcels are
    # lightered for Haldia, which is why it appears in the problem statement at all.
    "Sagar_Sandheads": PortLocation(
        "Sagar_Sandheads", 21.2000, 88.1500, "approx:hooghly-pilot-station"
    ),
    "Haldia": PortLocation("Haldia", 22.0578, 88.1042, "portwatch:port442"),
    # --- Origin load ports
    "Newcastle_AU": PortLocation("Newcastle_AU", -32.8945, 151.7577, "portwatch:port816"),
    "Gladstone_AU": PortLocation("Gladstone_AU", -23.8191, 151.2155, "portwatch:port398"),
    "Richards_Bay": PortLocation("Richards_Bay", -28.7924, 32.0394, "portwatch:port1099"),
    "Beira": PortLocation("Beira", -19.8111, 34.8398, "portwatch:port137"),
    # Muara Pantai is a coal transhipment anchorage in the Mahakam delta, East
    # Kalimantan, with no PortWatch entry. Samarinda (port1137) is the PortWatch port
    # serving the same coal complex and is used as the position proxy, so distances
    # and congestion refer to one consistent location.
    "Muara_Pantai": PortLocation(
        "Muara_Pantai", -0.5513, 117.1922, "approx:samarinda-proxy:port1137"
    ),
    "Balikpapan": PortLocation("Balikpapan", -1.2025, 116.7960, "portwatch:port102"),
    # Hampton Roads is the harbour complex; Norfolk carries the Lamberts Point coal
    # terminal and is the standard US East Coast coal-export reference point.
    "Hampton_Roads": PortLocation("Hampton_Roads", 36.8496, -76.3305, "portwatch:port826"),
    "Singapore": PortLocation("Singapore", 1.2720, 103.7075, "portwatch:port1201"),
    # Vostochny closes the network's missing-Russia gap (P1 port-network
    # audit). No PortWatch coordinate entry exists for it (checked directly
    # against raw_data/portwatch/port_coords.csv -- no match), so the
    # position is a public reference (Wikipedia, citing Vrangel/Nakhodka Bay
    # coordinates), same sourcing tier as Gangavaram's entry above.
    "Vostochny_RU": PortLocation("Vostochny_RU", 42.7414639, 133.0797972, "approx:public-reference"),
}


class RouteNotFoundError(RuntimeError):
    """Raised when the marine network graph cannot connect two ports."""


def _setup_logging() -> None:
    """Configure root logging once for CLI runs."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


#: Earth radius in nautical miles, for the great-circle floor.
EARTH_RADIUS_NM: Final[float] = 3440.065

#: Multiplier applied to the great-circle distance when the marine graph collapses two
#: ports onto one node. Coastal legs follow the shoreline rather than cutting across
#: it; 1.25 reproduces the East Coast India legs to within about 10% of published
#: figures and is deliberately conservative, since understating a leg understates fuel.
COASTAL_DETOUR_FACTOR: Final[float] = 1.25


def great_circle_nm(origin: PortLocation, destination: PortLocation) -> float:
    """Great-circle distance in nautical miles.

    A sea route can never be shorter than this, which makes it both a sanity bound on
    the routing graph and a usable floor when the graph is too coarse to resolve a leg.
    """
    import math

    lat1, lon1, lat2, lon2 = map(
        math.radians, (origin.lat, origin.lon, destination.lat, destination.lon)
    )
    hav = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_NM * math.asin(math.sqrt(hav))


def sea_distance_nm(
    origin: PortLocation, destination: PortLocation
) -> tuple[float, str]:
    """Shortest navigable sea distance in nautical miles, and the method used.

    ``searoute`` takes GeoJSON-order coordinates, i.e. ``[lon, lat]``. Getting that
    backwards silently returns a plausible-looking number for the wrong place, so the
    swap is done here once rather than at each call site.

    The Eurostat marine network is built for global shipping statistics and its nodes
    are coarser than the East Coast India port spacing. Ports within roughly 100 nm
    collapse onto a single node and the graph then reports 0 nm -- Dhamra to Paradip,
    Gangavaram to Visakhapatnam and Paradip to Sandheads all did. A returned distance
    shorter than the great circle is physically impossible and is the tell, so in that
    case we fall back to a geodesic estimate with a coastal detour allowance and say
    so in the ``method`` field rather than shipping a zero.
    """
    import searoute as sr

    floor = great_circle_nm(origin, destination)
    route = sr.searoute(
        [origin.lon, origin.lat],
        [destination.lon, destination.lat],
        units="naut",
    )
    length = route.get("properties", {}).get("length")
    if length is None:
        raise RouteNotFoundError(
            f"No sea route found between {origin.port_id} and {destination.port_id}."
        )
    routed = float(length)
    if routed < floor:
        return floor * COASTAL_DETOUR_FACTOR, "geodesic_fallback"
    return routed, "searoute"


def build_distance_matrix(
    coords: dict[str, PortLocation] | None = None,
) -> pl.DataFrame:
    """Compute every unordered port pair. Returns one row per pair (plus self-pairs).

    Distances are symmetric, so only ``origin < destination`` is stored; the runtime
    lookup in ``opt.geography`` normalises the key order.
    """
    ports = coords if coords is not None else PORT_COORDS
    rows: list[dict[str, object]] = []

    for port_id in sorted(ports):
        rows.append(
            {
                "origin": port_id,
                "destination": port_id,
                "distance_nm": 0.0,
                "great_circle_nm": 0.0,
                "method": "self",
                "approximate": ports[port_id].is_approximate,
            }
        )

    failures: list[tuple[str, str, str]] = []
    for a_id, b_id in combinations(sorted(ports), 2):
        a, b = ports[a_id], ports[b_id]
        try:
            dist, method = sea_distance_nm(a, b)
        except Exception as exc:  # noqa: BLE001 - record and continue, report at end
            failures.append((a_id, b_id, str(exc)))
            continue
        rows.append(
            {
                "origin": a_id,
                "destination": b_id,
                "distance_nm": dist,
                "great_circle_nm": great_circle_nm(a, b),
                "method": method,
                "approximate": a.is_approximate or b.is_approximate,
            }
        )

    if failures:
        # Never paper over a missing leg with a default -- that is the bug being fixed.
        detail = "\n".join(f"  {a} <-> {b}: {msg}" for a, b, msg in failures)
        raise RouteNotFoundError(
            f"{len(failures)} port pair(s) could not be routed:\n{detail}"
        )

    return pl.DataFrame(rows).sort(["origin", "destination"])


def compare_with_legacy(matrix: pl.DataFrame) -> pl.DataFrame:
    """Compare computed distances against the hand-entered RouteEnum table.

    This is the safety check on replacing Ark's table: legs where the two agree
    confirm both the coordinates and the routing, and legs where they diverge are
    exactly the ones worth a human glance before the numbers drive money.
    """
    from opt.network import RouteEnum

    legacy: list[dict[str, object]] = []
    for route in RouteEnum:
        legacy.append(
            {
                "origin": route.value.origin.id,
                "destination": route.value.destination.id,
                "legacy_nm": float(route.value.distance_nm),
            }
        )
    legacy_df = pl.DataFrame(legacy)

    # Normalise both directions so the join matches regardless of stored orientation.
    normalised = legacy_df.with_columns(
        pl.min_horizontal("origin", "destination").alias("a"),
        pl.max_horizontal("origin", "destination").alias("b"),
    ).select("a", "b", "legacy_nm")

    computed = matrix.select(
        pl.min_horizontal("origin", "destination").alias("a"),
        pl.max_horizontal("origin", "destination").alias("b"),
        pl.col("distance_nm").alias("computed_nm"),
        "great_circle_nm",
        "method",
        "approximate",
    )

    return (
        normalised.join(computed, on=["a", "b"], how="inner")
        .with_columns(
            (pl.col("computed_nm") - pl.col("legacy_nm")).alias("delta_nm"),
            (
                (pl.col("computed_nm") - pl.col("legacy_nm")).abs()
                / pl.col("legacy_nm").clip(lower_bound=1.0)
                * 100.0
            ).alias("pct_diff"),
            # A hand-entered distance below the great circle, or wildly above it, is
            # not a difference of opinion about routing -- it is an error. The African
            # legs in the original table sit near twice their great-circle distance.
            (pl.col("legacy_nm") / pl.col("great_circle_nm").clip(lower_bound=1.0))
            .alias("legacy_over_great_circle"),
        )
        .sort("pct_diff", descending=True)
    )


def audit_legacy_table(comparison: pl.DataFrame) -> pl.DataFrame:
    """Legacy legs that cannot be reconciled with geometry.

    A sea route runs between roughly 1.0x and 1.6x its great-circle distance; canal
    and cape routings push the ceiling higher but not to 2x on an open-ocean leg.
    Anything outside that band was entered wrong.
    """
    return comparison.filter(
        (pl.col("legacy_over_great_circle") < 0.98)
        | (pl.col("legacy_over_great_circle") > 1.9)
    ).sort("legacy_over_great_circle", descending=True)


def main() -> None:
    """Build the distance matrix, report against the legacy table, and save it."""
    _setup_logging()

    n_ports = len(PORT_COORDS)
    n_pairs = n_ports * (n_ports - 1) // 2
    approx = [p.port_id for p in PORT_COORDS.values() if p.is_approximate]
    LOGGER.info(f"routing {n_pairs} pairs across {n_ports} ports")
    if approx:
        LOGGER.info(f"approximate positions: {', '.join(sorted(approx))}")

    matrix = build_distance_matrix()
    DATA_OUT.mkdir(parents=True, exist_ok=True)
    matrix.write_parquet(OUT_PATH)
    LOGGER.info(f"wrote {OUT_PATH} ({matrix.height} rows)")

    by_method = matrix.group_by("method").agg(pl.len().alias("n")).sort("method")
    LOGGER.info(f"routing methods: {dict(by_method.iter_rows())}")

    comparison = compare_with_legacy(matrix)
    covered = comparison.height
    LOGGER.info(
        f"legacy table covered {covered} of {n_pairs} pairs "
        f"({100 * covered / n_pairs:.0f}%); the rest were resolving to a constant"
    )

    bad = audit_legacy_table(comparison)
    if not bad.is_empty():
        with pl.Config(tbl_rows=40, tbl_width_chars=140, tbl_formatting="ASCII_MARKDOWN"):
            LOGGER.warning(
                f"{bad.height} hand-entered leg(s) are geometrically impossible "
                f"(ratio to great-circle outside 0.98-1.9):\n"
                f"{bad.select('a', 'b', 'legacy_nm', 'computed_nm', 'great_circle_nm', 'legacy_over_great_circle')}"
            )


if __name__ == "__main__":
    main()
