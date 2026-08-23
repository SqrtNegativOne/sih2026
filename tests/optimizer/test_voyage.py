import pytest
from datetime import date
from sih.optimizer.types import Vessel, CargoParcel, VesselClass, OptimizerInputs, PortSpec
from sih.optimizer.voyage import schedule_voyages

def test_simple_voyage_schedule():
    # 1 Vessel, 2 Cargoes. 
    # Vessel is at Port A.
    # Cargo 1: A -> B, laycan day 5-10
    # Cargo 2: B -> C, laycan day 15-20
    # Distance A->B is small, B->C is small.
    # Vessel can do both sequentially.
    
    v = Vessel(
        vessel_id="V1",
        vessel_class=VesselClass.SUPRAMAX,
        current_port="PortA",
        status="idle",
        available_from=date(2025, 1, 1),
        available_until=None,
        dwt=55000,
        draft_m=12.0,
        speed_kn=12.0,  # 288 nm/day
        fuel_consumption_tpd=30.0
    )
    
    c1 = CargoParcel(
        parcel_id="C1",
        origin_port="PortA",
        dest_port="PortB",
        commodity="Coal",
        volume_dwt=50000,
        laycan_start=date(2025, 1, 5),
        laycan_end=date(2025, 1, 10),
        route_family="",
        revenue_usd=500_000.0
    )
    
    c2 = CargoParcel(
        parcel_id="C2",
        origin_port="PortB",
        dest_port="PortC",
        commodity="Grain",
        volume_dwt=50000,
        laycan_start=date(2025, 1, 15),
        laycan_end=date(2025, 1, 20),
        route_family="",
        revenue_usd=400_000.0
    )
    
    inputs = OptimizerInputs(
        parcels=[c1, c2],
        vessels=[v],
        tc_quotes={VesselClass.SUPRAMAX: 15000},
        planning_horizon_days=30,
        contract_term_days=30,
        forecasts=[],
        basis={},
        port_specs={},
        port_distances={
            ("PortA", "PortB"): 288.0, # 1 day laden
            ("PortB", "PortC"): 576.0, # 2 days laden
        },
        bunker_price_usd_per_tonne=500.0
    )
    
    result = schedule_voyages(inputs, max_solve_seconds=2.0)
    
    assert result.solver_status in ("OPTIMAL", "FEASIBLE")
    assert len(result.assignments) == 2
    
    # Should do C1 then C2
    c1_assign = next(a for a in result.assignments if a.parcel_id == "C1")
    c2_assign = next(a for a in result.assignments if a.parcel_id == "C2")
    
    # C1 starts on or after day 4 (Jan 5 is 4 days after Jan 1). 
    # 4 days * 24 = 96 hours
    assert c1_assign.start_operation_hours >= 96
    
    # C2 must start after C1 finishes
    assert c2_assign.start_operation_hours >= c1_assign.finish_hours
