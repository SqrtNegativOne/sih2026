from datetime import date

from opt.repositioning import recommend_repositioning
from opt.network import PortEnum, RouteFamily
from opt.types import (
    BasisEntry,
    ForecastFan,
    OptimizerInputs,
    Vessel,
    VesselClass,
)


def test_repositioning_logic():
    v = Vessel(
        vessel_id="V1",
        vessel_class=VesselClass.SUPRAMAX,
        current_port=PortEnum.PARADIP,
        status="idle",
        available_from=date(2025, 1, 1),
        available_until=None,
        dwt=55000,
        draft_m=12.0,
        speed_kn=12.0,  # 288 nm/day
        laden_fuel_consumption_tpd=30.0,
        ballast_fuel_consumption_tpd=25.0
    )
    
    # Base forecast for Supramax = 10,000 USD/day
    fans = [ForecastFan(vessel_class=VesselClass.SUPRAMAX, horizon_days=30, p10=8000, p50=10000, p90=12000)]
    
    inputs = OptimizerInputs(
        parcels=[], vessels=[v], tc_quotes={}, planning_horizon_days=30, contract_term_days=30,
        forecasts=fans,
        basis={RouteFamily.INDONESIA_EC_INDIA: BasisEntry(route_family=RouteFamily.INDONESIA_EC_INDIA, basis_mean=-0.2, basis_std=0.1)}, # 5 days ballast
        
        opex_usd_per_day=500.0,
    )
    
    candidates = [PortEnum.PARADIP, PortEnum.SINGAPORE]
    
    rec = recommend_repositioning(v, candidates, inputs, assumed_voyage_days=30)
    
    assert rec.current_port == PortEnum.PARADIP
    assert len(rec.options) == 2
    
    paradip_opt = next(o for o in rec.options if o.port == PortEnum.PARADIP)
    singapore_opt = next(o for o in rec.options if o.port == PortEnum.SINGAPORE)
    
    # Paradip: wait cost = 5 days * 500 = 2500. Ballast = 0.
    # Expected TCE = 10000 * 0.8 = 8000. 
    # Score = 8000 * 30 - 2500 = 240,000 - 2500 = 237,500
    assert paradip_opt.wait_cost_usd == 1750.0
    assert paradip_opt.ballast_cost_usd == 0.0
    assert paradip_opt.expected_tce_usd_per_day == 10000.0
    assert paradip_opt.score_usd == 298250.0
    
    # Singapore: wait = 0. Ballast = 5 days. 
    # Fuel cost = 5 * 30 * 500 = 75,000. Ballast penalty = 5 * 250 = 1250. Total = 76,250.
    # Expected TCE = 10000 * 1.1 = 11,000.
    # Score = 11000 * 30 - 76,250 = 330,000 - 76,250 = 253,750
    
    assert rec.options[0].port == PortEnum.PARADIP  # Paradip wins because Singapore ballast is too expensive now

