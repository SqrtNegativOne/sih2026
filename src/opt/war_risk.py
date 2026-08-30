"""Joint War Committee Listed Areas, and the additional war-risk premium a
transit through one attracts.

What this is
------------
The Lloyd's Market Association / International Underwriting Association's
**Joint War Committee** publishes a public list of "Listed Areas" -- waters
of perceived enhanced risk for Hull War, Piracy, Terrorism and Related
Perils. A vessel entering one owes an *Additional War Risk Premium* (AWRP)
on top of its ordinary cover, conventionally quoted as a percentage of the
vessel's hull value per seven-day transit.

``LISTED_AREAS`` below encodes those areas as named regions with approximate
rectangular envelopes, ``listed_areas_on_route`` tests a real route against
them, and ``war_risk_premium_usd`` turns an exposure into a dollar figure.

What the premium number here is, and is not
-------------------------------------------
**It is not a market rate, and must never be presented as one.** Real AWRP is
negotiated per fixture between an owner, a broker and an underwriter, priced
on the specific vessel, flag, ownership, route, dates and the state of the
market on the day. Those rates are not published. The circular itself says
its list is advisory and that "application of this list on individual
contracts will be a matter for specific negotiation."

``DEFAULT_RATE_PCT_PER_7_DAYS`` is therefore a single, documented, tunable
**assumption** whose only job is to let the rest of the system carry an
order-of-magnitude line item instead of silently omitting a real cost. It is
exposed as a parameter on ``war_risk_premium_usd`` precisely so a user who
has a real quote can supply it and stop using ours. Every returned figure
carries ``Provenance.ESTIMATED`` and a ``basis`` string that says all of this
in the response itself, not just here.

Bounding boxes: what the approximation costs you
------------------------------------------------
The circular defines its areas as polygons with named boundary legs (and,
for Named Countries, as territorial waters within 12 nautical miles of the
coast). ``LISTED_AREAS`` approximates the *defined-water* areas as
latitude/longitude rectangles, hand-transcribed. Rectangles are deliberately
drawn to be **over-inclusive rather than under-inclusive**: a false positive
puts a disclosed, caveated line item in front of a user, while a false
negative silently tells them a war-risk area does not apply to their voyage.
Two consequences, stated rather than implied:

* A rectangle can cover water the real polygon excludes, most visibly at
  the corners.
* The **Named Countries** half of the circular -- 12 nm territorial waters
  around a list of named states -- is NOT modelled here at all. Encoding it
  honestly needs real coastline geometry, which this repo does not have.
  A route that only clips a named country's territorial sea without entering
  one of the defined-water boxes below will return no area. This is a real
  gap, disclosed, not a silent zero.

Provenance is ``DECLARED``: these areas are a real third-party publication's
determination, hand-transcribed here, not something this codebase derived.
Each entry carries its ``source_url`` and a ``last_reviewed`` date, and
``LISTED_AREAS_LAST_REVIEWED`` below is the date the whole table was last
checked against a live circular. **This table goes stale.** The Joint War
Committee amends it on no fixed schedule; a route returning no listed area
after that date means "no area on record as of the last review", never "the
Joint War Committee has confirmed this route is clear."

Not fetched at runtime: the circular is not scraped, by design (CLAUDE.md's
network policy, and the DO NOT on this module's own brief). Updating this
table is a deliberate human act with a new ``last_reviewed`` date.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final

from data_builders.provenance import Provenance
from opt.chokepoints import _haversine_nm
from opt.network import PortEnum

__all__ = [
    "DEFAULT_RATE_PCT_PER_7_DAYS",
    "LISTED_AREAS",
    "LISTED_AREAS_LAST_REVIEWED",
    "ListedArea",
    "WarRiskPremium",
    "listed_areas_on_polyline",
    "listed_areas_on_route",
    "war_risk_premium_usd",
]

#: The real circular this table was transcribed from.
JWC_SOURCE_URL: Final[str] = "https://lmalloyds.com/lma/jointwar/"

#: Date the whole table below was last checked against a live circular
#: (JWLA-033, issued 3 March 2026). See the module docstring: a nil result
#: after this date means "nothing on record as of this review", not "clear".
LISTED_AREAS_LAST_REVIEWED: Final[date] = date(2026, 8, 29)


@dataclass(frozen=True)
class ListedArea:
    """One Joint War Committee Listed Area, as an approximate rectangular
    envelope of the circular's own defined-water polygon."""

    area_id: str
    name: str
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float
    source_url: str
    last_reviewed: date
    note: str

    def contains(self, lon: float, lat: float) -> bool:
        return self.lat_min <= lat <= self.lat_max and self.lon_min <= lon <= self.lon_max


#: Transcribed by hand from JWLA-033 (3 March 2026). Provenance: DECLARED.
#: Each rectangle is an over-inclusive envelope of a real polygon -- see the
#: module docstring for exactly what that approximation costs.
LISTED_AREAS: Final[tuple[ListedArea, ...]] = (
    ListedArea(
        area_id="southern_red_sea",
        name="Southern Red Sea",
        # The circular lists the Red Sea south of latitude 18N. Envelope drawn
        # from that stated northern limit down to the Bab el-Mandeb narrows.
        lat_min=12.0, lat_max=18.0, lon_min=36.5, lon_max=43.5,
        source_url=JWC_SOURCE_URL, last_reviewed=LISTED_AREAS_LAST_REVIEWED,
        note="Northern limit (18N) is the circular's own stated boundary; east/west limits are the basin's coastlines.",
    ),
    ListedArea(
        area_id="gulf_of_aden",
        name="Gulf of Aden and Bab el-Mandeb approaches",
        lat_min=10.5, lat_max=15.5, lon_min=42.0, lon_max=52.0,
        source_url=JWC_SOURCE_URL, last_reviewed=LISTED_AREAS_LAST_REVIEWED,
        note="Part of the circular's combined Gulf of Aden / Southern Red Sea area; envelope spans Bab el-Mandeb to the Socotra approaches.",
    ),
    ListedArea(
        area_id="arabian_sea_indian_ocean",
        name="Arabian Sea / Indian Ocean listed waters",
        # The Indian Ocean leg of the combined area JWLA-033 amended. This is
        # the largest and least precise envelope in the table -- the real
        # polygon's southern and eastern legs are diagonal, and a rectangle
        # over-catches accordingly.
        lat_min=12.0, lat_max=26.0, lon_min=52.0, lon_max=65.0,
        source_url=JWC_SOURCE_URL, last_reviewed=LISTED_AREAS_LAST_REVIEWED,
        note="Least precise envelope in this table: the real polygon's southern and eastern boundary legs are diagonal, so a rectangle over-catches open ocean.",
    ),
    ListedArea(
        area_id="gulf_of_oman",
        name="Gulf of Oman and Strait of Hormuz",
        lat_min=22.5, lat_max=27.5, lon_min=55.0, lon_max=61.5,
        source_url=JWC_SOURCE_URL, last_reviewed=LISTED_AREAS_LAST_REVIEWED,
        note="Contains the Strait of Hormuz. Oman and Iran are separately Named Countries in the same circular.",
    ),
    ListedArea(
        area_id="persian_arabian_gulf",
        name="Persian or Arabian Gulf",
        lat_min=23.5, lat_max=30.5, lon_min=47.5, lon_max=56.5,
        source_url=JWC_SOURCE_URL, last_reviewed=LISTED_AREAS_LAST_REVIEWED,
        note="Bahrain, Kuwait and Qatar were added as Named Countries by JWLA-033; this envelope covers the Gulf's own listed waters.",
    ),
    ListedArea(
        area_id="black_sea_azov",
        name="Black Sea and Sea of Azov",
        # The circular's polygon runs from the Ukraine-Romania land border
        # through open-sea waypoints to the Russia-Georgia land border, i.e.
        # the northern and eastern Black Sea plus the Sea of Azov. It does NOT
        # extend south-west to Turkish waters -- which is why the Bosporus is
        # deliberately outside this envelope.
        lat_min=43.0, lat_max=47.5, lon_min=31.0, lon_max=41.0,
        source_url=JWC_SOURCE_URL, last_reviewed=LISTED_AREAS_LAST_REVIEWED,
        note="Northern/eastern Black Sea and Sea of Azov only. Deliberately excludes the Bosporus and Turkish waters, which the circular's own polygon does not reach.",
    ),
    ListedArea(
        area_id="cabo_delgado",
        name="Cabo Delgado, Mozambique",
        lat_min=-14.0, lat_max=-10.0, lon_min=39.5, lon_max=42.5,
        source_url=JWC_SOURCE_URL, last_reviewed=LISTED_AREAS_LAST_REVIEWED,
        note="Defined waters off northern Mozambique. Note Beira and Maputo are far to the south, outside this envelope.",
    ),
    ListedArea(
        area_id="gulf_of_guinea",
        name="Gulf of Guinea",
        lat_min=-1.0, lat_max=7.0, lon_min=-1.0, lon_max=9.0,
        source_url=JWC_SOURCE_URL, last_reviewed=LISTED_AREAS_LAST_REVIEWED,
        note="Defined waters off Nigeria and its neighbours; the circular's long-standing piracy listing.",
    ),
)

#: Assumption, not a market rate -- read the module docstring before quoting
#: this anywhere. Real AWRP is negotiated per fixture and is not published;
#: publicly reported figures for listed-area transits have ranged from well
#: under 0.1% to several percent of hull value depending on the area and the
#: moment. This single mid-range placeholder exists so the arithmetic can run
#: at all, and is a parameter on war_risk_premium_usd so a user with a real
#: quote can replace it.
DEFAULT_RATE_PCT_PER_7_DAYS: Final[float] = 0.4

#: AWRP is conventionally charged per seven-day period in the area, with one
#: period as the minimum -- a two-day transit is not billed as two sevenths.
_DAYS_PER_PERIOD: Final[float] = 7.0

#: Step used to walk each route segment when testing it against a rectangle.
#: Every envelope in LISTED_AREAS is at least ~150 nm on its shortest side,
#: so a 25 nm step cannot pass through one without landing inside it.
_SAMPLE_STEP_NM: Final[float] = 25.0


@dataclass(frozen=True)
class WarRiskPremium:
    """An estimated additional war-risk premium. ``rate_pct_per_7_days`` is an
    assumption unless the caller supplied their own -- ``basis`` says which."""

    premium_usd: float
    hull_value_usd: float
    areas: tuple[str, ...]
    transit_days: float
    periods_charged: int
    rate_pct_per_7_days: float
    rate_is_caller_supplied: bool
    provenance: Provenance
    basis: str


def _densify(a: tuple[float, float], b: tuple[float, float]) -> list[tuple[float, float]]:
    """Sample points along the segment ``a -> b`` at ``_SAMPLE_STEP_NM``.

    Reuses ``opt.chokepoints._haversine_nm`` for the real great-circle length
    rather than restating any spherical maths; the interpolation between the
    two endpoints is linear in lon/lat, which is a documented approximation
    that is well inside tolerance at a 25 nm step against envelopes an order
    of magnitude larger.
    """
    length_nm = _haversine_nm(a, b)
    steps = max(1, math.ceil(length_nm / _SAMPLE_STEP_NM))
    return [
        (a[0] + (b[0] - a[0]) * (i / steps), a[1] + (b[1] - a[1]) * (i / steps))
        for i in range(steps + 1)
    ]


def listed_areas_on_polyline(polyline: Sequence[tuple[float, float]]) -> tuple[str, ...]:
    """Listed-area ids this real polyline enters, in the order it meets them.

    ``polyline`` is (lon, lat) pairs in searoute order -- the same shape
    ``opt.chokepoints.chokepoints_on_route`` takes. A polyline with fewer
    than two points has no segments and returns ``()``.
    """
    if len(polyline) < 2:
        return ()

    first_hit: dict[str, int] = {}
    for seg_idx in range(len(polyline) - 1):
        for lon, lat in _densify(polyline[seg_idx], polyline[seg_idx + 1]):
            for area in LISTED_AREAS:
                if area.area_id not in first_hit and area.contains(lon, lat):
                    first_hit[area.area_id] = seg_idx
    return tuple(area_id for area_id, _ in sorted(first_hit.items(), key=lambda kv: kv[1]))


def listed_areas_on_route(origin: PortEnum, dest: PortEnum) -> tuple[str, ...]:
    """Listed-area ids the real route between these two ports enters.

    The polyline comes from the same place every other route inspection in
    this codebase gets one (``opt.route_trace``'s own cached ``_leg``:
    searoute, with its documented great-circle fallback), so this can never
    disagree with the route drawn on the map or the chokepoints reported
    beside it.
    """
    from opt.route_trace import _leg

    leg = _leg(origin.value.id, dest.value.id)
    return listed_areas_on_polyline(leg.polyline)


def war_risk_premium_usd(
    hull_value_usd: float | None,
    areas: Sequence[str],
    transit_days: float,
    *,
    rate_pct_per_7_days: float | None = None,
) -> WarRiskPremium | None:
    """Estimated additional war-risk premium for a transit through ``areas``.

    Returns ``None`` -- never a fabricated figure -- when ``hull_value_usd``
    is unknown, which is the normal case: a vessel's insured hull value is a
    real commercial fact this system cannot infer. Also returns ``None`` when
    the route enters no listed area, since there is then no premium to owe.

    ``rate_pct_per_7_days`` defaults to ``DEFAULT_RATE_PCT_PER_7_DAYS``, an
    assumption and not a market rate (see the module docstring). Pass your
    own real quoted rate to replace it; the returned ``basis`` records which
    of the two was used.

    A transit is charged in whole seven-day periods with a minimum of one,
    matching how AWRP is conventionally billed. Multiple listed areas on one
    voyage are charged **once**, not additively per area -- a deliberate
    simplification, disclosed here and in ``basis``, since real cover for a
    multi-area transit is negotiated as a whole rather than summed.
    """
    if hull_value_usd is None or hull_value_usd <= 0:
        return None
    if not areas:
        return None
    if transit_days <= 0:
        raise ValueError(f"transit_days must be positive, got {transit_days}")

    rate = rate_pct_per_7_days if rate_pct_per_7_days is not None else DEFAULT_RATE_PCT_PER_7_DAYS
    caller_supplied = rate_pct_per_7_days is not None
    periods = max(1, math.ceil(transit_days / _DAYS_PER_PERIOD))
    premium = hull_value_usd * (rate / 100.0) * periods

    if caller_supplied:
        basis = (
            f"{rate:.3f}% of hull value per 7-day period x {periods} period(s), at the rate "
            f"YOU supplied. Areas entered: {', '.join(areas)}. Multiple areas charged once, "
            "not per area."
        )
    else:
        basis = (
            f"ESTIMATE, NOT A MARKET QUOTE: {rate:.3f}% of hull value per 7-day period x "
            f"{periods} period(s), using this system's own documented placeholder rate. Real "
            "additional war-risk premium is negotiated per fixture and is not published -- "
            "supply your own rate to replace this. Areas entered: "
            f"{', '.join(areas)}. Multiple areas charged once, not per area."
        )

    return WarRiskPremium(
        premium_usd=premium,
        hull_value_usd=hull_value_usd,
        areas=tuple(areas),
        transit_days=transit_days,
        periods_charged=periods,
        rate_pct_per_7_days=rate,
        rate_is_caller_supplied=caller_supplied,
        provenance=Provenance.ESTIMATED,
        basis=basis,
    )
