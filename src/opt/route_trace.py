"""Route-exploration trace: every routing the solver actually walked.

The optimizer prices several candidate routings on the way to a recommendation --
one per vessel class in the fleet-mix frontier (some feasible, some ruled out),
one per candidate port an idle vessel was scored against, and the laden legs of
any voyage it scheduled. Individually these are already computed inside
``opt.fleetmix`` / ``opt.repositioning`` / ``opt.voyage``; this module collects
them into one flat list of ``SolverRoute`` with a real port-to-port polyline per
leg, so a frontend can draw "here is what the solver considered, and why it
picked what it picked" on a map.

Polylines come from ``searoute`` (a real marine-network shortest path) where it
resolves, and a great-circle interpolation otherwise -- flagged per leg so the
map can render the approximation differently.
"""
from __future__ import annotations

import functools
import itertools
import math

from opt.geography import UnknownPortPairError
from opt.geography import distance_nm as geo_distance_nm
from opt.network import PortEnum
from opt.types import OptimizerRecommendation, RouteLeg, SolverRoute

__all__ = ["build_route_exploration"]

#: Cap on repositioning alternates traced per idle vessel -- the full candidate
#: set is every PortEnum, but the map only needs the contenders.
_MAX_REPOSITION_ALTERNATES = 6


def _lonlat(port: PortEnum) -> tuple[float, float]:
    """(lon, lat) for a port, from the same coordinate table the sea-distance
    matrix was built from."""
    from data_builders.build_geography import PORT_COORDS

    loc = PORT_COORDS[port.value.id]
    return (loc.lon, loc.lat)


def _great_circle_polyline(
    a: tuple[float, float], b: tuple[float, float], n: int = 24
) -> tuple[tuple[float, float], ...]:
    """``n``-segment great-circle path between two (lon, lat) points."""
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    hav = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    d = 2 * math.asin(math.sqrt(hav))
    if d == 0:
        return (a, b)
    pts: list[tuple[float, float]] = []
    for i in range(n + 1):
        f = i / n
        sa = math.sin((1 - f) * d) / math.sin(d)
        sb = math.sin(f * d) / math.sin(d)
        x = sa * math.cos(lat1) * math.cos(lon1) + sb * math.cos(lat2) * math.cos(lon2)
        y = sa * math.cos(lat1) * math.sin(lon1) + sb * math.cos(lat2) * math.sin(lon2)
        z = sa * math.sin(lat1) + sb * math.sin(lat2)
        lat = math.atan2(z, math.hypot(x, y))
        lon = math.atan2(y, x)
        pts.append((math.degrees(lon), math.degrees(lat)))
    return tuple(pts)


@functools.lru_cache(maxsize=256)
def _leg(from_id: str, to_id: str) -> RouteLeg:
    from_port = PortEnum[_CODE_BY_ID[from_id]]
    to_port = PortEnum[_CODE_BY_ID[to_id]]
    a = _lonlat(from_port)
    b = _lonlat(to_port)

    polyline: tuple[tuple[float, float], ...]
    fallback = False
    try:
        import searoute as sr

        feat = sr.searoute(list(a), list(b))
        coords = feat["geometry"]["coordinates"]
        polyline = tuple((float(x), float(y)) for x, y in coords)
        if len(polyline) < 2:
            raise ValueError("degenerate searoute result")
    except Exception:  # noqa: BLE001 -- searoute raises assorted errors offline / for odd pairs; any failure just means fall back to a great circle
        polyline = _great_circle_polyline(a, b)
        fallback = True

    try:
        dist = geo_distance_nm(from_id, to_id)
    except UnknownPortPairError:
        dist = _rough_nm(a, b)
        fallback = True

    return RouteLeg(
        from_port=from_port,
        to_port=to_port,
        distance_nm=dist,
        is_great_circle_fallback=fallback,
        polyline=polyline,
    )


def _rough_nm(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    hav = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * 3440.065 * math.asin(math.sqrt(hav))


_CODE_BY_ID: dict[str, str] = {p.value.id: p.name for p in PortEnum}


def _legs(*ports: PortEnum) -> tuple[RouteLeg, ...]:
    return tuple(_leg(a.value.id, b.value.id) for a, b in itertools.pairwise(ports))


def build_route_exploration(rec: OptimizerRecommendation) -> tuple[SolverRoute, ...]:
    """Flatten a recommendation's fleet-mix, repositioning, and voyage routings
    into one list of drawable ``SolverRoute``."""
    routes: list[SolverRoute] = []
    origin, dest = rec.origin_port, rec.dest_port

    # --- Fleet-mix: one route per class configuration, chosen / considered /
    # rejected, direct or via a transshipment hub.
    if rec.fleet_mix is not None:
        for i, c in enumerate(rec.fleet_mix.configurations):
            if c.requires_transshipment and c.transshipment_hub is not None:
                legs = _legs(origin, c.transshipment_hub, dest)
                label = f"{c.vessel_class.value} x{c.n_vessels} via {c.transshipment_hub.value.id}"
            else:
                legs = _legs(origin, dest)
                label = f"{c.vessel_class.value} x{c.n_vessels}"
            routes.append(
                SolverRoute(
                    id=f"fleet-{c.vessel_class.value}",
                    kind="fleet_mix",
                    label=label,
                    vessel_class=c.vessel_class,
                    status="chosen" if i == 0 else "considered",
                    reason=None,
                    metric_label="cost p50",
                    metric_usd=c.cost_p50_usd,
                    legs=legs,
                )
            )
        for c in rec.fleet_mix.rejected_configurations:
            routes.append(
                SolverRoute(
                    id=f"fleet-rej-{c.vessel_class.value}",
                    kind="fleet_mix",
                    label=f"{c.vessel_class.value} x{c.n_vessels}",
                    vessel_class=c.vessel_class,
                    status="rejected",
                    reason=c.infeasible_reason,
                    metric_label=None,
                    metric_usd=None,
                    legs=_legs(origin, dest),
                )
            )

    # --- Repositioning: candidate ballast legs for each idle vessel.
    current_by_vessel = {a.vessel_id: a.current_port for a in rec.repositioning_actions}
    per_vessel: dict[str, list] = {}
    for opt in rec.repositioning_options:
        per_vessel.setdefault(opt.vessel_id, []).append(opt)
    for vessel_id, opts in per_vessel.items():
        cur = current_by_vessel.get(vessel_id)
        if cur is None:
            continue
        movers = [o for o in opts if not o.is_current_location]
        movers.sort(key=lambda o: (not o.is_recommended, -o.score_usd))
        for o in movers[:_MAX_REPOSITION_ALTERNATES]:
            routes.append(
                SolverRoute(
                    id=f"repo-{vessel_id}-{o.port.value.id}",
                    kind="repositioning",
                    label=f"{vessel_id}: ballast to {o.port.value.id}",
                    vessel_class=None,
                    status="chosen" if o.is_recommended else "considered",
                    reason=None,
                    metric_label="score",
                    metric_usd=o.score_usd,
                    legs=_legs(cur, o.port),
                )
            )

    # --- Voyage assignments: the laden legs the scheduler actually booked.
    for a in rec.voyage_assignments:
        routes.append(
            SolverRoute(
                id=f"voyage-{a.vessel_id}-{a.parcel_id}",
                kind="voyage_assignment",
                label=f"{a.vessel_id} carries {a.parcel_id}",
                vessel_class=rec.target_vessel_class,
                status="chosen",
                reason=None,
                metric_label="profit",
                metric_usd=a.profit_usd,
                legs=_legs(origin, a.dest_port),
            )
        )

    return tuple(routes)
