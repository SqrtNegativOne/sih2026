import pytest
from datetime import date
from opt.api import run_optimizer
from opt.types import OptimizerInputs, VesselClass, ForecastFan, Vessel, RouteFamily
from opt.network import PortEnum

def test_optimal_entry_window_output():
    v = Vessel(
        vessel_id="V1",
        vessel_class=VesselClass.SUPRAMAX,
        current_port=PortEnum.PARADIP,
        status="idle",
        available_from=date(2025, 1, 1),
        dwt=55_000,
        draft_m=12.0,
        speed_kn=12.0,
        fuel_consumption_tpd=30.0,
    )
    
    fan7 = ForecastFan(vessel_class=VesselClass.SUPRAMAX, horizon_days=7, p10=7000, p50=8000, p90=9000)
    fan30 = ForecastFan(vessel_class=VesselClass.SUPRAMAX, horizon_days=30, p10=3000, p50=4000, p90=5000)
    
    inputs = OptimizerInputs(
        vessels=[v],
        parcels=[],
        tc_quotes={VesselClass.SUPRAMAX: 12000.0},
        planning_horizon_days=30,
        contract_term_days=30,
        forecasts=[fan7, fan30],
        basis={}
    )
    
    res = run_optimizer(inputs, target_class=VesselClass.SUPRAMAX)
    
    # Trough will be at day 30 since we interpolate between 8000 at day 7 and 4000 at day 30
    assert "Optimal entry window: Day 27 to 30 (P50 trough at ~$4,000/day)" in res.lock_wait_text
