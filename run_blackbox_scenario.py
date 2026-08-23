"""Black Box Real-Life Scenario Test

This script sets up a realistic scenario to verify if the optimizer acts
intelligently when treated as a pure black box.

Scenario:
- 2 Supramax vessels. V1 is in Paradip (congested), V2 is in Singapore.
- 2 Cargoes available.
    - C1: Paradip -> Haldia (High revenue, tight laycan).
    - C2: Singapore -> China (Normal revenue).
- Market: Expected to crash (ML Fan points down).
- Quote: TC quote is very high right now.
"""
from datetime import date

from opt.api import run_optimizer
from opt.types import (
    BasisEntry,
    CargoParcel,
    ForecastFan,
    OptimizerInputs,
    PortSpec,
    Vessel,
    VesselClass,
)


def main():
    print("Setting up Real-Life Scenario...\n")
    
    # 1. Vessels
    v1 = Vessel(vessel_id="Vessel_Alpha", vessel_class=VesselClass.SUPRAMAX, current_port="Paradip",
                status="idle", available_from=date(2025, 5, 1), available_until=None,
                dwt=55000, draft_m=12.0, speed_kn=12.0, fuel_consumption_tpd=30.0)
                
    v2 = Vessel(vessel_id="Vessel_Bravo", vessel_class=VesselClass.SUPRAMAX, current_port="Richards_Bay",
                status="idle", available_from=date(2025, 5, 1), available_until=None,
                dwt=55000, draft_m=12.0, speed_kn=12.0, fuel_consumption_tpd=30.0)
                
    # 2. Cargoes
    c1 = CargoParcel(parcel_id="Cargo_1_Coal", origin_port="Paradip", dest_port="Haldia",
                     commodity="Coal", volume_dwt=50000, laycan_start=date(2025, 5, 2), laycan_end=date(2025, 5, 10),
                     route_family="india_coast", revenue_usd=300_000.0) # Highly profitable
                     
    # 3. Market Forecast (Market is crashing)
    fans = [
        ForecastFan(VesselClass.SUPRAMAX, 7,  p10=6000, p50=8000, p90=10000),
        ForecastFan(VesselClass.SUPRAMAX, 30, p10=5000, p50=7000, p90=9000),
        ForecastFan(VesselClass.SUPRAMAX, 90, p10=4000, p50=6000, p90=8000),
    ]
    
    # 4. Inputs
    inputs = OptimizerInputs(
        vessels=[v1, v2],
        parcels=[c1],
        tc_quotes={VesselClass.SUPRAMAX: 12_000}, # Quote is 12k, but market expects 7k!
        planning_horizon_days=30,
        contract_term_days=30,
        forecasts=fans,
        basis={
            "Paradip": BasisEntry("Paradip", -0.1, 0.1),
            "Singapore": BasisEntry("Singapore", +0.2, 0.1),
            "Richards_Bay": BasisEntry("Richards_Bay", +0.0, 0.1),
        },
        port_specs={
            "Paradip": PortSpec("Paradip", expected_wait_days=4.0),
            "Haldia": PortSpec("Haldia", expected_wait_days=1.0),
            "Singapore": PortSpec("Singapore", expected_wait_days=0.0),
            "Richards_Bay": PortSpec("Richards_Bay", expected_wait_days=8.0), # Huge queue
        },
        port_distances={
            ("Paradip", "Haldia"): 120.0,
            ("Richards_Bay", "Singapore"): 4500.0, # Long ballast
        },
        bunker_price_usd_per_tonne=500.0,
        risk_tolerance=0.0
    )
    
    print("Running Black Box Optimizer...\n")
    rec = run_optimizer(inputs, target_class=VesselClass.SUPRAMAX)
    
    print("="*80)
    print("OPTIMIZER OUTPUTS")
    print("="*80)
    print(f"1. Lock/wait:    {rec.lock_wait_text}")
    print(f"2. Schedule:     {rec.voyage_schedule_text}")
    print(f"3. Reposition:   {rec.repositioning_text}")
    print(f"4. Savings:      {rec.savings_text}")
    print(f"5. Review date:  {rec.review_trigger_text}")
    print("="*80)

if __name__ == "__main__":
    main()
