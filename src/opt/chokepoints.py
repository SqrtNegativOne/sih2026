"""Which real chokepoints does THIS route actually cross?

The bug this fixes
-------------------
``opt.api``'s ``_DEFAULT_CHOKEPOINTS`` checks the same five chokepoints (Suez,
Bab el-Mandeb, Malacca, Hormuz, Cape of Good Hope) on every quote, regardless
of the route. A Newcastle -> Paradip voyage gets a Suez Canal risk flag it
will never transit, and a route through a chokepoint outside that fixed five
gets no flag at all. This module determines the real answer from the real
route geometry already on disk: ``opt.route_trace``'s own searoute polyline
(with its own documented great-circle fallback), tested against real
chokepoint locations.

Chokepoint geometry -- source and the radius judgement call
---------------------------------------------------------------
``CHOKEPOINT_NAMES`` (promoted from ``opt.risk``, which now imports it from
here) and ``CHOKEPOINT_GEOMETRY`` cover the same 28 PortWatch chokepoint ids
this codebase already harvests daily transit counts for
(``raw_data/portwatch/chokepoint<N>_daily_transits.csv``,
``opt.risk.chokepoint_disruption_alert``).

Coordinates come directly from IMF PortWatch's own "Chokepoints" feature
service -- the same organisation that defines these ids in the first place,
queried directly (not recalled from memory):
``https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/PortWatch_chokepoints_database/FeatureServer/0``
(``portid``/``portname``/``lat``/``lon`` fields), retrieved 2026-08-29. This is
a better source than a secondary reference (EIA, Wikipedia) precisely because
it is PortWatch's own definition of where "chokepoint6" *is* -- there is no
risk of this table disagreeing with what the transit-count CSVs already on
disk mean by that id.

Radius is a real judgement call, stated explicitly rather than left as 28
unexplained magic numbers: **radius_nm = max(30, half the chokepoint's real
publicly-documented transit-lane length in nm)**. The 30 nm floor is
deliberate, not an oversight -- ``opt.route_trace``'s searoute polylines are
coarse (tens of vertices over an intercontinental leg), so a circle sized to
a strait's literal width (a few nm for Bosporus or Dover) would routinely
miss a real transit just because the polyline's nearest vertex/segment
happened to pass a few nm wide of the strait's exact centreline -- better to
slightly over-catch a genuinely narrow strait than to silently miss it. For
the nine chokepoints with a single, commonly-cited length figure (canals and
the best-known straits), that real figure is used directly, cited per entry.
For the remaining 19 -- PortWatch-specific straits/passages/archipelagic
channels with no single canonical "length" figure -- the radius is a
disclosed size-class judgement (narrow strait ~30 nm, moderate ~40-60 nm,
wide archipelagic passage or an open-ocean cape-rounding waypoint like Cape
of Good Hope ~150 nm), not a fabricated precise number; each entry says so.
A circle is also a crude approximation of a long, thin corridor (most
visible for Malacca and Makassar, both hundreds of nm long) -- disclosed here
rather than silently implied to be more precise than it is.
"""
from __future__ import annotations

import functools
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from opt.network import PortEnum

__all__ = [
    "CHOKEPOINT_GEOMETRY",
    "CHOKEPOINT_NAMES",
    "ChokepointGeometry",
    "chokepoints_for_route",
    "chokepoints_on_route",
]

#: Matches opt.geography.EARTH_RADIUS_NM -- kept as its own local constant
#: (not imported) since this module's geometry is otherwise self-contained
#: and has no other reason to depend on opt.geography.
_EARTH_RADIUS_NM: Final[float] = 3440.065

#: Promoted from opt.risk._CHOKEPOINT_NAMES (opt.risk now imports this
#: constant from here instead of defining its own copy) -- the 28 PortWatch
#: chokepoint ids this codebase harvests daily transit data for.
CHOKEPOINT_NAMES: Final[dict[str, str]] = {
    "chokepoint1": "Suez Canal", "chokepoint2": "Panama Canal", "chokepoint3": "Bosporus Strait",
    "chokepoint4": "Bab el-Mandeb Strait", "chokepoint5": "Malacca Strait", "chokepoint6": "Strait of Hormuz",
    "chokepoint7": "Cape of Good Hope", "chokepoint8": "Gibraltar Strait", "chokepoint9": "Dover Strait",
    "chokepoint10": "Oresund Strait", "chokepoint11": "Taiwan Strait", "chokepoint12": "Korea Strait",
    "chokepoint13": "Tsugaru Strait", "chokepoint14": "Luzon Strait", "chokepoint15": "Lombok Strait",
    "chokepoint16": "Ombai Strait", "chokepoint17": "Bohai Strait", "chokepoint18": "Torres Strait",
    "chokepoint19": "Sunda Strait", "chokepoint20": "Makassar Strait", "chokepoint21": "Magellan Strait",
    "chokepoint22": "Yucatan Channel", "chokepoint23": "Windward Passage", "chokepoint24": "Mona Passage",
    "chokepoint25": "Balabac Strait", "chokepoint26": "Bering Strait", "chokepoint27": "Mindoro Strait",
    "chokepoint28": "Kerch Strait",
}


@dataclass(frozen=True)
class ChokepointGeometry:
    """A chokepoint's real centre (from IMF PortWatch's own chokepoints
    service) and the judgement-call radius that approximates its transit
    lane -- see this module's own docstring for the exact rule."""

    lat: float
    lon: float
    radius_nm: float
    source: str


#: lat/lon: IMF PortWatch Chokepoints feature service (see module docstring),
#: queried 2026-08-29 -- exact, not estimated. radius_nm: see module
#: docstring's stated rule; the comment on each entry says which real length
#: figure (canals/major straits) or size-class judgement (everything else)
#: it comes from.
CHOKEPOINT_GEOMETRY: Final[dict[str, ChokepointGeometry]] = {
    "chokepoint1": ChokepointGeometry(  # Suez Canal: ~193 km / 104 nm (Suez Canal Authority) -> half = 52
        30.593346, 32.436882, 52.0, "half of ~193 km published canal length"
    ),
    "chokepoint2": ChokepointGeometry(  # Panama Canal: ~82 km / 44 nm -> half = 22, floored to 30
        9.120512, -79.767238, 30.0, "half of ~82 km published canal length, floored at the 30 nm minimum"
    ),
    "chokepoint3": ChokepointGeometry(  # Bosporus Strait: ~31 km / 17 nm -> floored to 30
        41.169282, 29.091501, 30.0, "~31 km published strait length; well under the 30 nm minimum"
    ),
    "chokepoint4": ChokepointGeometry(  # Bab el-Mandeb: ~20 nm at the narrows (Perim Island channels) -> floored
        12.788597, 43.349545, 30.0, "~20 nm narrows (EIA World Oil Transit Chokepoints); floored at 30 nm"
    ),
    "chokepoint5": ChokepointGeometry(  # Malacca Strait: ~805 km / 435 nm published length -> half ~= 217, rounded
        1.516955, 102.665106, 220.0, "half of ~805 km published strait length -- deliberately large, see docstring"
    ),
    "chokepoint6": ChokepointGeometry(  # Strait of Hormuz: ~90 nm published length (EIA) -> half = 45
        26.296853, 56.859848, 45.0, "half of ~90 nm published strait length (EIA World Oil Transit Chokepoints)"
    ),
    "chokepoint7": ChokepointGeometry(  # Cape of Good Hope: not a strait -- an open-ocean routing waypoint
        -34.927286, 20.882737, 150.0, "judgement call: an open-ocean cape-rounding waypoint, not a narrow strait"
    ),
    "chokepoint8": ChokepointGeometry(  # Strait of Gibraltar: ~60 km / 32 nm -> half = 16, floored to 30
        35.942274, -5.754896, 30.0, "half of ~60 km published strait length, floored at the 30 nm minimum"
    ),
    "chokepoint9": ChokepointGeometry(  # Strait of Dover: ~34 km / 18 nm -> floored to 30
        51.030224, 1.505840, 30.0, "~34 km published strait length; well under the 30 nm minimum"
    ),
    "chokepoint10": ChokepointGeometry(  # Oresund Strait: ~118 km / 64 nm -> half = 32
        55.507840, 12.850795, 32.0, "half of ~118 km published strait length"
    ),
    "chokepoint11": ChokepointGeometry(  # Taiwan Strait: size-class judgement -- a wide strait
        24.723510, 119.831364, 60.0, "size-class judgement: a wide strait (no single canonical length figure)"
    ),
    "chokepoint12": ChokepointGeometry(  # Korea Strait: size-class judgement -- moderate, two channels
        34.130768, 129.209206, 50.0, "size-class judgement: moderate strait, split into two channels by Tsushima"
    ),
    "chokepoint13": ChokepointGeometry(  # Tsugaru Strait: size-class judgement -- narrow-moderate
        41.328036, 140.353274, 35.0, "size-class judgement: narrow-moderate strait"
    ),
    "chokepoint14": ChokepointGeometry(  # Luzon Strait: size-class judgement -- wide (includes Bashi Channel)
        20.488891, 121.352295, 75.0, "size-class judgement: wide strait/channel complex between Taiwan and Luzon"
    ),
    "chokepoint15": ChokepointGeometry(  # Lombok Strait: size-class judgement -- narrow
        -8.419145, 115.801433, 30.0, "size-class judgement: narrow strait"
    ),
    "chokepoint16": ChokepointGeometry(  # Ombai Strait: size-class judgement -- narrow-moderate
        -8.398541, 125.090981, 35.0, "size-class judgement: narrow-moderate strait"
    ),
    "chokepoint17": ChokepointGeometry(  # Bohai Strait: size-class judgement -- narrow-moderate, island-broken
        38.373044, 120.900049, 40.0, "size-class judgement: narrow-moderate, main channel broken by islands"
    ),
    "chokepoint18": ChokepointGeometry(  # Torres Strait: size-class judgement -- wide, reef-strewn
        -9.862534, 142.247529, 50.0, "size-class judgement: wide, shallow, reef-strewn strait"
    ),
    "chokepoint19": ChokepointGeometry(  # Sunda Strait: size-class judgement -- narrow
        -5.966841, 105.775220, 30.0, "size-class judgement: narrow strait"
    ),
    "chokepoint20": ChokepointGeometry(  # Makassar Strait: size-class judgement -- very wide/long
        0.352281, 119.257065, 150.0, "size-class judgement: very wide and long -- see docstring's Malacca note"
    ),
    "chokepoint21": ChokepointGeometry(  # Magellan Strait: ~570 km / 310 nm published length -> half = 155
        -52.640340, -69.594832, 155.0, "half of ~570 km published strait length"
    ),
    "chokepoint22": ChokepointGeometry(  # Yucatan Channel: size-class judgement -- moderate-wide
        21.815254, -85.647279, 60.0, "size-class judgement: moderate-wide channel"
    ),
    "chokepoint23": ChokepointGeometry(  # Windward Passage: size-class judgement -- moderate
        19.986187, -73.697540, 40.0, "size-class judgement: moderate passage"
    ),
    "chokepoint24": ChokepointGeometry(  # Mona Passage: size-class judgement -- moderate
        18.448736, -67.711354, 45.0, "size-class judgement: moderate passage"
    ),
    "chokepoint25": ChokepointGeometry(  # Balabac Strait: size-class judgement -- narrow
        7.413615, 117.114622, 30.0, "size-class judgement: narrow strait"
    ),
    "chokepoint26": ChokepointGeometry(  # Bering Strait: size-class judgement -- moderate
        65.966489, -165.549829, 42.0, "size-class judgement: moderate strait"
    ),
    "chokepoint27": ChokepointGeometry(  # Mindoro Strait: size-class judgement -- moderate
        12.468315, 120.403428, 45.0, "size-class judgement: moderate strait"
    ),
    "chokepoint28": ChokepointGeometry(  # Kerch Strait: narrow but long transit lane -> floored to 30
        45.266763, 36.543902, 30.0, "narrow strait with a ~40 km transit lane; near the 30 nm minimum"
    ),
}


def _haversine_nm(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in nm between two (lon, lat) points."""
    lon1, lat1 = math.radians(a[0]), math.radians(a[1])
    lon2, lat2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * _EARTH_RADIUS_NM * math.asin(math.sqrt(min(1.0, h)))


def _bearing_rad(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Initial great-circle bearing (radians) from (lon,lat) a to b."""
    lon1, lat1 = math.radians(a[0]), math.radians(a[1])
    lon2, lat2 = math.radians(b[0]), math.radians(b[1])
    dlon = lon2 - lon1
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return math.atan2(y, x)


def _point_to_segment_distance_nm(
    p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]
) -> float:
    """Great-circle distance in nm from point ``p`` to the segment ``a -> b``
    -- not just to its endpoints. Standard spherical cross-track/along-track
    formulas (e.g. the ones at movable-type.co.uk's latitude/longitude
    reference): project ``p`` onto the great circle through ``a``/``b``: if
    the projected point falls within the segment, the distance is the
    cross-track distance; otherwise it's the distance to whichever endpoint
    is nearer. This is the whole reason ``chokepoints_on_route`` tests every
    SEGMENT, not just the polyline's vertices -- searoute's polylines are
    coarse (tens of vertices over an intercontinental leg), so a strait that
    falls between two widely-spaced vertices would be invisible to a
    vertex-only test even though the real transit passes right through it.
    """
    dist_ab = _haversine_nm(a, b)
    if dist_ab < 1e-9:
        return _haversine_nm(p, a)
    dist_ap = _haversine_nm(a, p)
    if dist_ap < 1e-9:
        return 0.0

    theta_ap = _bearing_rad(a, p)
    theta_ab = _bearing_rad(a, b)
    delta_ap = dist_ap / _EARTH_RADIUS_NM

    cross_track_nm = math.asin(
        max(-1.0, min(1.0, math.sin(delta_ap) * math.sin(theta_ap - theta_ab)))
    ) * _EARTH_RADIUS_NM

    cos_dxt = math.cos(cross_track_nm / _EARTH_RADIUS_NM)
    if abs(cos_dxt) < 1e-12:
        along_track_nm = dist_ap
    else:
        ratio = max(-1.0, min(1.0, math.cos(delta_ap) / cos_dxt))
        along_track_nm = math.acos(ratio) * _EARTH_RADIUS_NM

    if along_track_nm < 0.0:
        return _haversine_nm(p, a)
    if along_track_nm > dist_ab:
        return _haversine_nm(p, b)
    return abs(cross_track_nm)


def chokepoints_on_route(
    polyline: Sequence[tuple[float, float]],
    *,
    margin_nm: float = 0.0,
) -> tuple[str, ...]:
    """The PortWatch chokepoint ids whose circle this real polyline passes
    through, in the order the route encounters them.

    ``polyline`` is (lon, lat) pairs in searoute order -- the same shape as
    ``opt.types.RouteLeg.polyline`` / ``opt.route_trace``'s own internal
    representation. Every SEGMENT is tested against every chokepoint's
    circle (point-to-segment, not point-to-vertex -- see
    ``_point_to_segment_distance_nm``'s own docstring for why that matters).
    A polyline with fewer than two points has no segments and returns ``()``.
    """
    if len(polyline) < 2:
        return ()

    first_hit_segment: dict[str, int] = {}
    for seg_idx in range(len(polyline) - 1):
        a, b = polyline[seg_idx], polyline[seg_idx + 1]
        for cp_id, geom in CHOKEPOINT_GEOMETRY.items():
            if cp_id in first_hit_segment:
                continue
            center = (geom.lon, geom.lat)
            if _point_to_segment_distance_nm(center, a, b) <= geom.radius_nm + margin_nm:
                first_hit_segment[cp_id] = seg_idx

    return tuple(cp_id for cp_id, _ in sorted(first_hit_segment.items(), key=lambda kv: kv[1]))


@functools.lru_cache(maxsize=256)
def chokepoints_for_route(origin: PortEnum, dest: PortEnum) -> tuple[str, ...]:
    """Real chokepoints for the real route between these two ports -- the
    polyline is fetched the same way ``opt.route_trace`` builds one for the
    route-inspection map (searoute, falling back to a great circle,
    ``opt.route_trace``'s own already-cached ``_leg``), then tested via
    ``chokepoints_on_route``. Cached: the set of real port pairs a quote can
    name is small and bounded, and this is on the hot path of every
    ``run_optimizer`` call.
    """
    from opt.route_trace import _leg

    leg = _leg(origin.value.id, dest.value.id)
    return chokepoints_on_route(leg.polyline)
