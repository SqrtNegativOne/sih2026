from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict


class Port(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    max_loa_m: float | None = None
    max_beam_m: float | None = None
    max_draft_m: float | None = None
    max_dwt: float | None = None
    handling_rate_tph: float | None = None
    expected_wait_days: float = 0.0
    bunker_price_usd: float | None = None

class Route(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    origin: Port
    destination: Port
    distance_nm: float

class PortEnum(Enum):
    PARADIP = Port(
        id="Paradip",
        max_loa_m=225.0,
        max_beam_m=32.2,
        max_draft_m=14.3,
        max_dwt=75000.0,
        handling_rate_tph=1200.0,
        expected_wait_days=3.5,
        bunker_price_usd=610.0
    )
    VIZAG = Port(
        id="Vizag",
        max_loa_m=300.0,
        max_beam_m=45.0,
        max_draft_m=17.0,
        max_dwt=180000.0,
        handling_rate_tph=2500.0,
        expected_wait_days=2.0,
        bunker_price_usd=620.0
    )
    GANGAVARAM = Port(
        id="Gangavaram",
        max_loa_m=300.0,
        max_beam_m=50.0,
        max_draft_m=16.5,
        max_dwt=200000.0,
        handling_rate_tph=3000.0,
        expected_wait_days=1.5,
        bunker_price_usd=620.0
    )
    GOPALPUR = Port(
        id="Gopalpur",
        max_loa_m=185.0,
        max_beam_m=28.0,
        max_draft_m=10.7,
        max_dwt=35000.0,
        handling_rate_tph=600.0,
        expected_wait_days=4.0,
        bunker_price_usd=640.0
    )
    DHAMRA = Port(
        id="Dhamra",
        max_loa_m=300.0,
        max_beam_m=46.0,
        max_draft_m=17.0,
        max_dwt=200000.0,
        handling_rate_tph=2800.0,
        expected_wait_days=2.5,
        bunker_price_usd=630.0
    )
    SAGAR_SANDHEADS = Port(
        id="Sagar_Sandheads",
        max_loa_m=225.0,
        max_beam_m=32.0,
        max_draft_m=9.5,
        max_dwt=40000.0,
        handling_rate_tph=700.0,
        expected_wait_days=5.0,
        bunker_price_usd=650.0
    )
    HALDIA = Port(
        id="Haldia",
        max_loa_m=215.0,
        max_beam_m=32.0,
        max_draft_m=8.5,
        max_dwt=40000.0,
        handling_rate_tph=800.0,
        expected_wait_days=4.5,
        bunker_price_usd=615.0
    )
    # LOA/beam for the 8 origin ports below were previously None (unset) --
    # _vessel_can_call's LOA/beam check has been vacuous for every loading
    # port since it was added, even though the problem statement explicitly
    # asks for "similar data for the loading ports in Australia, the US,
    # Mozambique, and Indonesia." Real, sourced figures (port authority /
    # terminal operator specifications, cited per port below); existing
    # draft/dwt values are left untouched -- some (Newcastle, Muara_Pantai)
    # look conservative next to what was found while sourcing this, but
    # that's a separate, unverified discrepancy, not something to silently
    # change while adding a different field.
    NEWCASTLE_AU = Port(
        id="Newcastle_AU",
        # F-14 fix: the previous max_dwt/max_draft_m here (85,000 dwt /
        # 14.5m) were not real published limits -- confirmed live against
        # two independent sources (findaport.com's own port listing, and a
        # cross-check search on Port Authority NSW's published channel
        # data) that Newcastle -- the "Newcastlemax" class's own namesake
        # port, and the world's largest coal export port -- publishes a
        # real bulk-vessel maximum of 232,000 dwt, LOA 300m, beam 50.0m,
        # draft 16.2m. That old, too-shallow figure was one of two
        # compounding causes (the other being opt.voyage's DWT-before-draft
        # check order, also fixed under F-14) that made Capesize vessels
        # structurally infeasible from every one of the five problem-
        # statement-named origin countries -- verified live before this
        # fix. LOA/beam were already correct.
        max_loa_m=300.0,
        max_beam_m=50.0,
        max_draft_m=16.2,
        max_dwt=232000.0,
        handling_rate_tph=3500.0,
        expected_wait_days=5.0,
        bunker_price_usd=590.0
    )
    GLADSTONE_AU = Port(
        id="Gladstone_AU",
        # Port of Gladstone overall max (RG Tanna coal terminal operates
        # within this, with a more restrictive 14.7m draft).
        # F-14 fix: max_dwt corrected from 150,000 to 180,000, confirmed
        # live -- RG Tanna Coal Terminal's own two shiploaders are
        # published as handling Capesize vessels up to 180,000 dwt.
        # LOA/beam/draft left unchanged (already real, and the existing
        # comment's own RG Tanna-specific draft caveat still applies).
        max_loa_m=315.0,
        max_beam_m=55.0,
        max_draft_m=15.0,
        max_dwt=180000.0,
        handling_rate_tph=4000.0,
        expected_wait_days=3.0,
        bunker_price_usd=585.0
    )
    RICHARDS_BAY = Port(
        id="Richards_Bay",
        # RBCT Capesize berths 301-306 (350m length); beam is RBCT's own
        # published Capesize beam tolerance.
        max_loa_m=350.0,
        max_beam_m=47.5,
        max_draft_m=17.5,
        max_dwt=220000.0,
        handling_rate_tph=5000.0,
        expected_wait_days=7.0,
        bunker_price_usd=570.0
    )
    BEIRA = Port(
        id="Beira",
        # Port of Beira's published max LOA/beam for bulk carriers.
        max_loa_m=200.0,
        max_beam_m=34.0,
        max_draft_m=8.0,
        max_dwt=30000.0,
        handling_rate_tph=500.0,
        expected_wait_days=3.0,
        bunker_price_usd=640.0
    )
    MUARA_PANTAI = Port(
        id="Muara_Pantai",
        # LOA is the real recorded maximum at this open-water anchorage
        # (Berau Coal transshipment, Global Energy Monitor); no published
        # beam figure exists, so 45.0m (standard Capesize beam) is used as
        # the consistent inference from that same real LOA/dwt scale, not an
        # independently sourced number like the LOA figure is.
        max_loa_m=289.0,
        max_beam_m=45.0,
        max_draft_m=13.0,
        max_dwt=80000.0,
        handling_rate_tph=2000.0,
        expected_wait_days=2.0,
        bunker_price_usd=600.0
    )
    BALIKPAPAN = Port(
        id="Balikpapan",
        # Balikpapan Coal Terminal's own published vessel specification.
        max_loa_m=250.0,
        max_beam_m=43.0,
        max_draft_m=12.5,
        max_dwt=75000.0,
        handling_rate_tph=1800.0,
        expected_wait_days=1.5,
        bunker_price_usd=595.0
    )
    HAMPTON_ROADS = Port(
        id="Hampton_Roads",
        # Beam from Lamberts Point Terminal's published ship-loader spec
        # (175ft). No single published max LOA for the Hampton Roads coal
        # terminals; 290.0m is the standard Capesize LOA consistent with
        # Dominion Terminal's published 178,000 dwt capacity at this beam.
        # F-14 fix: max_dwt was set to 100,000 despite this comment already
        # citing the real 178,000 dwt figure -- a real data-entry
        # inconsistency, now corrected to match the comment's own source.
        # Draft (45ft channel / 13.7m, confirmed live against Dominion
        # Terminal's own published depth) is already close to the existing
        # 14.0m figure, left unchanged.
        max_loa_m=290.0,
        max_beam_m=53.3,
        max_draft_m=14.0,
        max_dwt=178000.0,
        handling_rate_tph=2000.0,
        expected_wait_days=2.0,
        bunker_price_usd=650.0
    )
    SINGAPORE = Port(
        id="Singapore",
        # No single published port-wide max LOA/beam for bulk carriers (a
        # multi-terminal hub); sized generously above Capesize (like the
        # port's own existing 300,000 dwt / 20.0m draft figures already are)
        # rather than pinned to one specific smaller terminal.
        max_loa_m=340.0,
        max_beam_m=60.0,
        max_draft_m=20.0,
        max_dwt=300000.0,
        expected_wait_days=0.5,
        bunker_price_usd=540.0
    )
    VOSTOCHNY_RU = Port(
        id="Vostochny_RU",
        # Closes the Russia gap the PS opening names ("Australia, the US,
        # Mozambique, Russia and Indonesia") but the detailed description and
        # the pre-P1 network both dropped -- see docs/01_problem_statement.md:8
        # and P1's port-network audit. Figures are PPK-3's modern Capesize
        # coal berths at Vostochny (Nakhodka Bay), sourced from two
        # independent operator/agency pages (NHK Maritime Services' own
        # Vostochny port page; Credo-Trans's port overview), both converging
        # on LOA 300m / draft up to 16.0m; DWT ranges 170,000-190,000t across
        # the two sources, so 170,000 (the more conservative, more frequently
        # cited figure) is used here. Neither is a primary port-authority
        # document -- weaker sourcing than the register-backed Indian ports,
        # disclosed as such in berth_truth.port_master rather than presented
        # as equally strong.
        max_loa_m=300.0,
        max_beam_m=45.0,
        max_draft_m=16.0,
        max_dwt=170000.0,
        expected_wait_days=4.0,
        bunker_price_usd=580.0
    )

class RouteEnum(Enum):
    HALDIA_SAGAR_SANDHEADS = Route(
        id="HALDIA_SAGAR_SANDHEADS",
        origin=PortEnum.HALDIA.value,
        destination=PortEnum.SAGAR_SANDHEADS.value,
        distance_nm=60.0
    )
    HALDIA_PARADIP = Route(
        id="HALDIA_PARADIP",
        origin=PortEnum.HALDIA.value,
        destination=PortEnum.PARADIP.value,
        distance_nm=180.0
    )
    HALDIA_DHAMRA = Route(
        id="HALDIA_DHAMRA",
        origin=PortEnum.HALDIA.value,
        destination=PortEnum.DHAMRA.value,
        distance_nm=220.0
    )
    HALDIA_GOPALPUR = Route(
        id="HALDIA_GOPALPUR",
        origin=PortEnum.HALDIA.value,
        destination=PortEnum.GOPALPUR.value,
        distance_nm=350.0
    )
    HALDIA_VIZAG = Route(
        id="HALDIA_VIZAG",
        origin=PortEnum.HALDIA.value,
        destination=PortEnum.VIZAG.value,
        distance_nm=510.0
    )
    HALDIA_GANGAVARAM = Route(
        id="HALDIA_GANGAVARAM",
        origin=PortEnum.HALDIA.value,
        destination=PortEnum.GANGAVARAM.value,
        distance_nm=550.0
    )
    SAGAR_SANDHEADS_PARADIP = Route(
        id="SAGAR_SANDHEADS_PARADIP",
        origin=PortEnum.SAGAR_SANDHEADS.value,
        destination=PortEnum.PARADIP.value,
        distance_nm=130.0
    )
    SAGAR_SANDHEADS_DHAMRA = Route(
        id="SAGAR_SANDHEADS_DHAMRA",
        origin=PortEnum.SAGAR_SANDHEADS.value,
        destination=PortEnum.DHAMRA.value,
        distance_nm=175.0
    )
    SAGAR_SANDHEADS_GOPALPUR = Route(
        id="SAGAR_SANDHEADS_GOPALPUR",
        origin=PortEnum.SAGAR_SANDHEADS.value,
        destination=PortEnum.GOPALPUR.value,
        distance_nm=310.0
    )
    SAGAR_SANDHEADS_VIZAG = Route(
        id="SAGAR_SANDHEADS_VIZAG",
        origin=PortEnum.SAGAR_SANDHEADS.value,
        destination=PortEnum.VIZAG.value,
        distance_nm=470.0
    )
    SAGAR_SANDHEADS_GANGAVARAM = Route(
        id="SAGAR_SANDHEADS_GANGAVARAM",
        origin=PortEnum.SAGAR_SANDHEADS.value,
        destination=PortEnum.GANGAVARAM.value,
        distance_nm=510.0
    )
    PARADIP_DHAMRA = Route(
        id="PARADIP_DHAMRA",
        origin=PortEnum.PARADIP.value,
        destination=PortEnum.DHAMRA.value,
        distance_nm=100.0
    )
    PARADIP_GOPALPUR = Route(
        id="PARADIP_GOPALPUR",
        origin=PortEnum.PARADIP.value,
        destination=PortEnum.GOPALPUR.value,
        distance_nm=200.0
    )
    PARADIP_VIZAG = Route(
        id="PARADIP_VIZAG",
        origin=PortEnum.PARADIP.value,
        destination=PortEnum.VIZAG.value,
        distance_nm=360.0
    )
    PARADIP_GANGAVARAM = Route(
        id="PARADIP_GANGAVARAM",
        origin=PortEnum.PARADIP.value,
        destination=PortEnum.GANGAVARAM.value,
        distance_nm=400.0
    )
    DHAMRA_GOPALPUR = Route(
        id="DHAMRA_GOPALPUR",
        origin=PortEnum.DHAMRA.value,
        destination=PortEnum.GOPALPUR.value,
        distance_nm=150.0
    )
    DHAMRA_VIZAG = Route(
        id="DHAMRA_VIZAG",
        origin=PortEnum.DHAMRA.value,
        destination=PortEnum.VIZAG.value,
        distance_nm=310.0
    )
    DHAMRA_GANGAVARAM = Route(
        id="DHAMRA_GANGAVARAM",
        origin=PortEnum.DHAMRA.value,
        destination=PortEnum.GANGAVARAM.value,
        distance_nm=350.0
    )
    GOPALPUR_VIZAG = Route(
        id="GOPALPUR_VIZAG",
        origin=PortEnum.GOPALPUR.value,
        destination=PortEnum.VIZAG.value,
        distance_nm=175.0
    )
    GOPALPUR_GANGAVARAM = Route(
        id="GOPALPUR_GANGAVARAM",
        origin=PortEnum.GOPALPUR.value,
        destination=PortEnum.GANGAVARAM.value,
        distance_nm=215.0
    )
    VIZAG_GANGAVARAM = Route(
        id="VIZAG_GANGAVARAM",
        origin=PortEnum.VIZAG.value,
        destination=PortEnum.GANGAVARAM.value,
        distance_nm=40.0
    )
    NEWCASTLE_AU_PARADIP = Route(
        id="NEWCASTLE_AU_PARADIP",
        origin=PortEnum.NEWCASTLE_AU.value,
        destination=PortEnum.PARADIP.value,
        distance_nm=5600.0
    )
    NEWCASTLE_AU_VIZAG = Route(
        id="NEWCASTLE_AU_VIZAG",
        origin=PortEnum.NEWCASTLE_AU.value,
        destination=PortEnum.VIZAG.value,
        distance_nm=5700.0
    )
    NEWCASTLE_AU_HALDIA = Route(
        id="NEWCASTLE_AU_HALDIA",
        origin=PortEnum.NEWCASTLE_AU.value,
        destination=PortEnum.HALDIA.value,
        distance_nm=5750.0
    )
    GLADSTONE_AU_PARADIP = Route(
        id="GLADSTONE_AU_PARADIP",
        origin=PortEnum.GLADSTONE_AU.value,
        destination=PortEnum.PARADIP.value,
        distance_nm=5200.0
    )
    GLADSTONE_AU_VIZAG = Route(
        id="GLADSTONE_AU_VIZAG",
        origin=PortEnum.GLADSTONE_AU.value,
        destination=PortEnum.VIZAG.value,
        distance_nm=5300.0
    )
    GLADSTONE_AU_HALDIA = Route(
        id="GLADSTONE_AU_HALDIA",
        origin=PortEnum.GLADSTONE_AU.value,
        destination=PortEnum.HALDIA.value,
        distance_nm=5350.0
    )
    MUARA_PANTAI_PARADIP = Route(
        id="MUARA_PANTAI_PARADIP",
        origin=PortEnum.MUARA_PANTAI.value,
        destination=PortEnum.PARADIP.value,
        distance_nm=2800.0
    )
    MUARA_PANTAI_VIZAG = Route(
        id="MUARA_PANTAI_VIZAG",
        origin=PortEnum.MUARA_PANTAI.value,
        destination=PortEnum.VIZAG.value,
        distance_nm=2900.0
    )
    MUARA_PANTAI_HALDIA = Route(
        id="MUARA_PANTAI_HALDIA",
        origin=PortEnum.MUARA_PANTAI.value,
        destination=PortEnum.HALDIA.value,
        distance_nm=2950.0
    )
    BALIKPAPAN_PARADIP = Route(
        id="BALIKPAPAN_PARADIP",
        origin=PortEnum.BALIKPAPAN.value,
        destination=PortEnum.PARADIP.value,
        distance_nm=2700.0
    )
    BALIKPAPAN_VIZAG = Route(
        id="BALIKPAPAN_VIZAG",
        origin=PortEnum.BALIKPAPAN.value,
        destination=PortEnum.VIZAG.value,
        distance_nm=2800.0
    )
    BALIKPAPAN_HALDIA = Route(
        id="BALIKPAPAN_HALDIA",
        origin=PortEnum.BALIKPAPAN.value,
        destination=PortEnum.HALDIA.value,
        distance_nm=2850.0
    )
    RICHARDS_BAY_PARADIP = Route(
        id="RICHARDS_BAY_PARADIP",
        origin=PortEnum.RICHARDS_BAY.value,
        destination=PortEnum.PARADIP.value,
        distance_nm=8200.0
    )
    RICHARDS_BAY_VIZAG = Route(
        id="RICHARDS_BAY_VIZAG",
        origin=PortEnum.RICHARDS_BAY.value,
        destination=PortEnum.VIZAG.value,
        distance_nm=8250.0
    )
    RICHARDS_BAY_HALDIA = Route(
        id="RICHARDS_BAY_HALDIA",
        origin=PortEnum.RICHARDS_BAY.value,
        destination=PortEnum.HALDIA.value,
        distance_nm=8350.0
    )
    BEIRA_PARADIP = Route(
        id="BEIRA_PARADIP",
        origin=PortEnum.BEIRA.value,
        destination=PortEnum.PARADIP.value,
        distance_nm=7800.0
    )
    BEIRA_VIZAG = Route(
        id="BEIRA_VIZAG",
        origin=PortEnum.BEIRA.value,
        destination=PortEnum.VIZAG.value,
        distance_nm=7850.0
    )
    BEIRA_HALDIA = Route(
        id="BEIRA_HALDIA",
        origin=PortEnum.BEIRA.value,
        destination=PortEnum.HALDIA.value,
        distance_nm=7950.0
    )
    HAMPTON_ROADS_PARADIP = Route(
        id="HAMPTON_ROADS_PARADIP",
        origin=PortEnum.HAMPTON_ROADS.value,
        destination=PortEnum.PARADIP.value,
        distance_nm=12500.0
    )
    HAMPTON_ROADS_VIZAG = Route(
        id="HAMPTON_ROADS_VIZAG",
        origin=PortEnum.HAMPTON_ROADS.value,
        destination=PortEnum.VIZAG.value,
        distance_nm=12550.0
    )
    HAMPTON_ROADS_HALDIA = Route(
        id="HAMPTON_ROADS_HALDIA",
        origin=PortEnum.HAMPTON_ROADS.value,
        destination=PortEnum.HALDIA.value,
        distance_nm=12600.0
    )
    SINGAPORE_PARADIP = Route(
        id="SINGAPORE_PARADIP",
        origin=PortEnum.SINGAPORE.value,
        destination=PortEnum.PARADIP.value,
        distance_nm=1600.0
    )
    SINGAPORE_VIZAG = Route(
        id="SINGAPORE_VIZAG",
        origin=PortEnum.SINGAPORE.value,
        destination=PortEnum.VIZAG.value,
        distance_nm=1700.0
    )
    SINGAPORE_HALDIA = Route(
        id="SINGAPORE_HALDIA",
        origin=PortEnum.SINGAPORE.value,
        destination=PortEnum.HALDIA.value,
        distance_nm=1750.0
    )
    SINGAPORE_GOPALPUR = Route(
        id="SINGAPORE_GOPALPUR",
        origin=PortEnum.SINGAPORE.value,
        destination=PortEnum.GOPALPUR.value,
        distance_nm=1600.0
    )
    SINGAPORE_GANGAVARAM = Route(
        id="SINGAPORE_GANGAVARAM",
        origin=PortEnum.SINGAPORE.value,
        destination=PortEnum.GANGAVARAM.value,
        distance_nm=1680.0
    )
BLENDED_BUNKER_USD_PER_TONNE = 610.0
class RouteFamily(str, Enum):
    INDONESIA_EC_INDIA = "indonesia_ec_india"
    AUSTRALIA_EC_INDIA = "australia_ec_india"
    SOUTH_AFRICA_EC_INDIA = "south_africa_ec_india"
    MOZAMBIQUE_EC_INDIA = "mozambique_ec_india"
    US_EC_INDIA = "us_ec_india"
    SINGAPORE_EC_INDIA = "singapore_ec_india"
    RUSSIA_EC_INDIA = "russia_ec_india"
    INTRA_EC_INDIA = "intra_ec_india"

#: Route family for a cargo move, keyed by its ORIGIN port. Every RouteFamily
#: value above is anchored to EC-India as the destination side, so the origin
#: alone determines the family -- including INTRA_EC_INDIA for moves that
#: start at one of the seven EC-India ports themselves (coastal
#: repositioning). Real region groupings matching each port's actual
#: country/coast, not invented.
ORIGIN_PORT_ROUTE_FAMILY: dict[PortEnum, RouteFamily] = {
    PortEnum.NEWCASTLE_AU: RouteFamily.AUSTRALIA_EC_INDIA,
    PortEnum.GLADSTONE_AU: RouteFamily.AUSTRALIA_EC_INDIA,
    PortEnum.RICHARDS_BAY: RouteFamily.SOUTH_AFRICA_EC_INDIA,
    PortEnum.BEIRA: RouteFamily.MOZAMBIQUE_EC_INDIA,
    PortEnum.MUARA_PANTAI: RouteFamily.INDONESIA_EC_INDIA,
    PortEnum.BALIKPAPAN: RouteFamily.INDONESIA_EC_INDIA,
    PortEnum.HAMPTON_ROADS: RouteFamily.US_EC_INDIA,
    PortEnum.SINGAPORE: RouteFamily.SINGAPORE_EC_INDIA,
    PortEnum.VOSTOCHNY_RU: RouteFamily.RUSSIA_EC_INDIA,
    PortEnum.PARADIP: RouteFamily.INTRA_EC_INDIA,
    PortEnum.VIZAG: RouteFamily.INTRA_EC_INDIA,
    PortEnum.GANGAVARAM: RouteFamily.INTRA_EC_INDIA,
    PortEnum.GOPALPUR: RouteFamily.INTRA_EC_INDIA,
    PortEnum.DHAMRA: RouteFamily.INTRA_EC_INDIA,
    PortEnum.SAGAR_SANDHEADS: RouteFamily.INTRA_EC_INDIA,
    PortEnum.HALDIA: RouteFamily.INTRA_EC_INDIA,
}


def route_family_for_origin(origin: PortEnum) -> RouteFamily:
    """Derive the route family for a cargo move starting at ``origin``.

    Every RouteFamily here is anchored to EC-India as the destination side,
    so the origin alone determines the family -- including INTRA_EC_INDIA for
    moves that start at an EC-India port itself (coastal repositioning).
    """
    if origin not in ORIGIN_PORT_ROUTE_FAMILY:
        raise ValueError(f"No route family mapping for origin port {origin!r}.")
    return ORIGIN_PORT_ROUTE_FAMILY[origin]


#: PortEnum -> tonnage.basins label. None where no real PortWatch coverage
#: exists at all (checked directly against the real P1/P2 harvest, not
#: assumed). Lives here, not in opt.repositioning (its original, sole user),
#: because opt.congestion's dynamic wait-day estimator needs the exact same
#: real mapping too, and opt.congestion already depends on opt.repositioning
#: for other reasons -- putting it in either of them would make the other
#: import back, a cycle. One source of truth in this shared, dependency-free
#: reference module instead of two copies quietly drifting apart.
PORT_TO_TONNAGE_LABEL: dict[PortEnum, str | None] = {
    PortEnum.PARADIP: "Paradip",
    PortEnum.VIZAG: "Visakhapatnam",
    PortEnum.GANGAVARAM: None,  # never resolved in PortWatch at all (PULL_NOTES.md)
    PortEnum.GOPALPUR: "Gopalpur",
    PortEnum.DHAMRA: "Dhamra",
    PortEnum.SAGAR_SANDHEADS: None,  # pilot station/anchorage, not a real PortWatch port
    PortEnum.HALDIA: "Haldia",
    PortEnum.NEWCASTLE_AU: "Newcastle_AU",
    PortEnum.GLADSTONE_AU: None,
    PortEnum.RICHARDS_BAY: "Richards_Bay_ZA",
    PortEnum.BEIRA: "Beira_MZ",
    PortEnum.MUARA_PANTAI: "Samarinda_ID",  # documented proxy, see data_builders.build_geography
    PortEnum.BALIKPAPAN: "Balikpapan_ID",
    PortEnum.HAMPTON_ROADS: None,
    PortEnum.SINGAPORE: None,
    # Not a proximity guess: raw_data/portwatch/Vostochny_RU_daily_portcalls.csv's own
    # rows self-declare portname="Vostochnyy", country="Russian Federation", ISO3="RUS"
    # -- confirmed by reading the file directly -- so identity is established by the
    # source's own metadata, not by nearness (contrast MUARA_PANTAI above).
    PortEnum.VOSTOCHNY_RU: "Vostochny_RU",
}
