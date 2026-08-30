"""Black Box Real-Life Scenario Test

Uses the real static reference data (actual EC India ports from the problem statement).

Scenario:
- 2 Supramax vessels. V1 is in Paradip, V2 is in Richards Bay (deep queue).
- 1 Coal cargo: Indonesia (Muara Pantai) -> Paradip, tight laycan.
- Market: Expected to crash. TC quote is high. Optimizer should WAIT and route V1.
"""
from datetime import date

from opt import (
    CargoParcel,
    ForecastFan,
    OptimizerInputs,
    Vessel,
    format_recommendation_text,
    run_optimizer,
)
from opt.network import PortEnum, RouteFamily
from opt.types import BasisEntry, VesselClass


def main():
    print("Setting up Real-Life Scenario (EC India ports)...\n")
    
    v1 = Vessel(
        vessel_id="Vessel_Alpha", 
        vessel_class=VesselClass.SUPRAMAX, 
        current_port=PortEnum.PARADIP,
        status="idle", available_from=date(2025, 5, 1), available_until=None,
        dwt=55000, draft_m=12.0, loa_m=190.0, beam_m=32.0, speed_kn=12.0,
        laden_fuel_consumption_tpd=30.0, ballast_fuel_consumption_tpd=25.0
    )
                
    v2 = Vessel(
        vessel_id="Vessel_Bravo", 
        vessel_class=VesselClass.SUPRAMAX, 
        current_port=PortEnum.RICHARDS_BAY,
        status="idle", available_from=date(2025, 5, 1), available_until=None,
        dwt=55000, draft_m=12.0, loa_m=190.0, beam_m=32.0, speed_kn=12.0,
        laden_fuel_consumption_tpd=30.0, ballast_fuel_consumption_tpd=25.0
    )
                
    c1 = CargoParcel(
        parcel_id="Cargo_1_Coal", 
        origin_port=PortEnum.MUARA_PANTAI, 
        dest_port=PortEnum.PARADIP,
        commodity="Coal", volume_dwt=50000, laycan_start=date(2025, 5, 20), laycan_end=date(2025, 5, 27),
        route_family=RouteFamily.INDONESIA_EC_INDIA, revenue_usd=800_000.0
    )
                     
    fans = [
        ForecastFan(vessel_class=VesselClass.SUPRAMAX, horizon_days=7,  p10=6000, p50=8000, p90=10000),
        ForecastFan(vessel_class=VesselClass.SUPRAMAX, horizon_days=30, p10=5000, p50=7000, p90=9000),
        ForecastFan(vessel_class=VesselClass.SUPRAMAX, horizon_days=90, p10=4000, p50=6000, p90=8000),
    ]
    
    inputs = OptimizerInputs(
        vessels=[v1, v2],
        parcels=[c1],
        tc_quotes={VesselClass.SUPRAMAX: 12_000},
        planning_horizon_days=30,
        contract_term_days=30,
        forecasts=fans,
        basis={
            RouteFamily.INTRA_EC_INDIA:        BasisEntry(route_family=RouteFamily.INTRA_EC_INDIA,      basis_mean=-0.10, basis_std=0.1),
            RouteFamily.SOUTH_AFRICA_EC_INDIA: BasisEntry(route_family=RouteFamily.SOUTH_AFRICA_EC_INDIA,  basis_mean=0.00, basis_std=0.1),
            RouteFamily.SINGAPORE_EC_INDIA:    BasisEntry(route_family=RouteFamily.SINGAPORE_EC_INDIA,     basis_mean=0.20, basis_std=0.1),
            RouteFamily.INDONESIA_EC_INDIA:    BasisEntry(route_family=RouteFamily.INDONESIA_EC_INDIA,     basis_mean=-0.05, basis_std=0.1),
        },
        risk_tolerance=0.0
    )
    
    print("Running Black Box Optimizer...\n")
    rec = run_optimizer(inputs)
    
    print(format_recommendation_text(rec))

if __name__ == "__main__":
    main()
