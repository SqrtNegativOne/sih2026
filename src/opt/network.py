from __future__ import annotations
from enum import Enum
from typing import Optional
from pydantic import BaseModel, ConfigDict

class Port(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    max_loa_m: Optional[float] = None
    max_beam_m: Optional[float] = None
    max_draft_m: Optional[float] = None
    max_dwt: Optional[float] = None
    handling_rate_tph: Optional[float] = None
    expected_wait_days: float = 0.0
    bunker_price_usd: Optional[float] = None

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
    NEWCASTLE_AU = Port(
        id="Newcastle_AU",
        max_draft_m=14.5,
        max_dwt=85000.0,
        handling_rate_tph=3500.0,
        expected_wait_days=5.0,
        bunker_price_usd=590.0
    )
    GLADSTONE_AU = Port(
        id="Gladstone_AU",
        max_draft_m=15.0,
        max_dwt=150000.0,
        handling_rate_tph=4000.0,
        expected_wait_days=3.0,
        bunker_price_usd=585.0
    )
    RICHARDS_BAY = Port(
        id="Richards_Bay",
        max_draft_m=17.5,
        max_dwt=220000.0,
        handling_rate_tph=5000.0,
        expected_wait_days=7.0,
        bunker_price_usd=570.0
    )
    BEIRA = Port(
        id="Beira",
        max_draft_m=8.0,
        max_dwt=30000.0,
        handling_rate_tph=500.0,
        expected_wait_days=3.0,
        bunker_price_usd=640.0
    )
    MUARA_PANTAI = Port(
        id="Muara_Pantai",
        max_draft_m=13.0,
        max_dwt=80000.0,
        handling_rate_tph=2000.0,
        expected_wait_days=2.0,
        bunker_price_usd=600.0
    )
    BALIKPAPAN = Port(
        id="Balikpapan",
        max_draft_m=12.5,
        max_dwt=75000.0,
        handling_rate_tph=1800.0,
        expected_wait_days=1.5,
        bunker_price_usd=595.0
    )
    HAMPTON_ROADS = Port(
        id="Hampton_Roads",
        max_draft_m=14.0,
        max_dwt=100000.0,
        handling_rate_tph=2000.0,
        expected_wait_days=2.0,
        bunker_price_usd=650.0
    )
    SINGAPORE = Port(
        id="Singapore",
        max_draft_m=20.0,
        max_dwt=300000.0,
        expected_wait_days=0.5,
        bunker_price_usd=540.0
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
    INTRA_EC_INDIA = "intra_ec_india"
