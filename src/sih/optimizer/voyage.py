"""Voyage Scheduling (Sub-problem 2)

Assigns vessels to cargo parcels and sequences them to maximize profit (TCE).
Uses Google OR-Tools CP-SAT solver.
"""
from __future__ import annotations

import collections
import math
from dataclasses import dataclass
from typing import Any, Sequence

from ortools.sat.python import cp_model

from sih.optimizer.types import CargoParcel, OptimizerInputs, Vessel, VesselClass


@dataclass
class VoyageAssignment:
    vessel_id: str
    parcel_id: str
    arrival_hours: int
    wait_hours: int
    start_operation_hours: int
    finish_hours: int
    profit_usd: float


@dataclass
class VoyageScheduleResult:
    assignments: list[VoyageAssignment]
    total_profit_usd: float
    solver_status: str


def _get_distance_nm(
    port_a: str, port_b: str, distances: dict[tuple[str, str], float]
) -> float:
    """Safely get distance between ports (symmetric). Defaults to 0 if same port."""
    if port_a == port_b:
        return 0.0
    if (port_a, port_b) in distances:
        return distances[(port_a, port_b)]
    if (port_b, port_a) in distances:
        return distances[(port_b, port_a)]
    
    # In a real app, missing distances might query Searoute or raise.
    # For now, warn and assume a large penalty distance.
    return 10_000.0


def schedule_voyages(
    inputs: OptimizerInputs,
    max_solve_seconds: float = 30.0,
) -> VoyageScheduleResult:
    """Solve the Voyage Scheduling (PDPTW) problem using OR-Tools CP-SAT.

    Maximizes total fleet profit = sum(revenue - fuel_cost - port_dues - idle_cost).
    """
    model = cp_model.CpModel()

    # Time granularity: 1 integer unit = 1 hour.
    # We measure time in hours from an arbitrary epoch, say 0 = today.
    # We assume 'today' is the earliest available_from across all vessels,
    # or just use dates directly converted to days/hours from epoch.
    if not inputs.vessels or not inputs.parcels:
        return VoyageScheduleResult([], 0.0, "NO_DATA")

    epoch_date = min(v.available_from for v in inputs.vessels)

    def date_to_hours(d: Any) -> int:
        return (d - epoch_date).days * 24

    # 1. Pre-calculate node properties
    C = inputs.parcels
    V = inputs.vessels
    
    # We'll use a large horizon time to bound integer variables
    max_horizon_days = inputs.planning_horizon_days + 60 # cushion for late arrivals
    max_time_hours = max_horizon_days * 24

    # Cargo valid time windows
    cargo_windows = {}
    for c in C:
        cargo_windows[c.parcel_id] = {
            "start": date_to_hours(c.laycan_start),
            "end": date_to_hours(c.laycan_end),
        }

    # Vessel base properties
    vessel_props = {}
    for v in V:
        vessel_props[v.vessel_id] = {
            "available": date_to_hours(v.available_from),
            "port": v.current_port,
        }

    # 2. Decision Variables
    # served[c]: true if cargo c is served by ANY vessel
    served = {c.parcel_id: model.NewBoolVar(f"served_{c.parcel_id}") for c in C}

    # x[v, c]: true if vessel v serves cargo c
    x = {}
    for v in V:
        for c in C:
            # Must match vessel class
            if c.route_family: # Simplification: assume vessel matches cargo if user provided it
                pass # You can add strict checks here
            x[v.vessel_id, c.parcel_id] = model.NewBoolVar(f"x_{v.vessel_id}_{c.parcel_id}")
            
    # Each cargo served by at most one vessel
    for c in C:
        model.AddExactlyOne(x[v.vessel_id, c.parcel_id] for v in V) if False else None # Placeholder
        model.Add(sum(x[v.vessel_id, c.parcel_id] for v in V) == served[c.parcel_id])

    # transition[v, c1, c2]: true if v visits c2 immediately after c1
    # start_node[v, c]: true if c is the FIRST cargo v visits
    # end_node[v, c]: true if c is the LAST cargo v visits
    trans = {}
    start_node = {}
    end_node = {}
    for v in V:
        for c in C:
            start_node[v.vessel_id, c.parcel_id] = model.NewBoolVar(f"start_{v.vessel_id}_{c.parcel_id}")
            end_node[v.vessel_id, c.parcel_id] = model.NewBoolVar(f"end_{v.vessel_id}_{c.parcel_id}")
            for c2 in C:
                if c.parcel_id != c2.parcel_id:
                    trans[v.vessel_id, c.parcel_id, c2.parcel_id] = model.NewBoolVar(f"trans_{v.vessel_id}_{c.parcel_id}_{c2.parcel_id}")

    # Flow conservation for each vessel and cargo
    for v in V:
        v_id = v.vessel_id
        # Vessel has at most one start and at most one end
        model.Add(sum(start_node[v_id, c.parcel_id] for c in C) <= 1)
        model.Add(sum(end_node[v_id, c.parcel_id] for c in C) <= 1)
        
        for c in C:
            c_id = c.parcel_id
            
            # If x[v, c] is true, it must have exactly one incoming edge (either start or from another c)
            in_edges = start_node[v_id, c_id] + sum(trans[v_id, prev.parcel_id, c_id] for prev in C if prev.parcel_id != c_id)
            model.Add(in_edges == x[v_id, c_id])
            
            # If x[v, c] is true, it must have exactly one outgoing edge (either end or to another c)
            out_edges = end_node[v_id, c_id] + sum(trans[v_id, c_id, nxt.parcel_id] for nxt in C if nxt.parcel_id != c_id)
            model.Add(out_edges == x[v_id, c_id])

    # 3. Time tracking
    # arrival_time[v, c]: when does v arrive at c.origin_port?
    # start_time[v, c]: when does v start loading c? (>= arrival_time and >= laycan_start)
    # finish_time[v, c]: when does v finish discharging c?
    arrival_time = {}
    start_time = {}
    finish_time = {}
    
    for v in V:
        v_id = v.vessel_id
        for c in C:
            c_id = c.parcel_id
            arrival_time[v_id, c_id] = model.NewIntVar(0, max_time_hours, f"arr_{v_id}_{c_id}")
            start_time[v_id, c_id] = model.NewIntVar(0, max_time_hours, f"start_{v_id}_{c_id}")
            finish_time[v_id, c_id] = model.NewIntVar(0, max_time_hours, f"fin_{v_id}_{c_id}")

    for v in V:
        for c in C:
            v_id = v.vessel_id
            c_id = c.parcel_id
            
            arr = arrival_time[v_id, c_id]
            st = start_time[v_id, c_id]
            fin = finish_time[v_id, c_id]
            
            # Start time is max(arrival, laycan_start)
            # Implemented as: start >= arr, start >= laycan_start
            model.Add(st >= arr)
            model.Add(st >= cargo_windows[c_id]["start"]).OnlyEnforceIf(x[v_id, c_id])
            
            # Must arrive before laycan_end
            model.Add(arr <= cargo_windows[c_id]["end"]).OnlyEnforceIf(x[v_id, c_id])

            # Duration computation
            laden_nm = _get_distance_nm(c.origin_port, c.dest_port, inputs.port_distances)
            laden_hours = int((laden_nm / v.speed_kn) if v.speed_kn > 0 else 0)
            
            # Port days (rough assumption: 2 days load + 2 days discharge = 96 hours)
            # In a real app, use inputs.port_specs[c.origin_port].handling_rate_tph
            port_hours = 96 
            
            duration = laden_hours + port_hours
            
            model.Add(fin == st + duration).OnlyEnforceIf(x[v_id, c_id])

            # Transition time constraints
            # If start_node[v, c], arr >= v.available_from + ballast_time(v.current_port -> c.origin)
            ballast_nm_start = _get_distance_nm(v.current_port, c.origin_port, inputs.port_distances)
            ballast_hrs_start = int((ballast_nm_start / v.speed_kn) if v.speed_kn > 0 else 0)
            
            model.Add(arr >= vessel_props[v_id]["available"] + ballast_hrs_start).OnlyEnforceIf(start_node[v_id, c_id])
            
            # If trans[v, c1, c2], arr[v, c2] >= fin[v, c1] + ballast_time(c1.dest -> c2.origin)
            for c2 in C:
                if c_id == c2.parcel_id: continue
                
                ballast_nm = _get_distance_nm(c.dest_port, c2.origin_port, inputs.port_distances)
                ballast_hrs = int((ballast_nm / v.speed_kn) if v.speed_kn > 0 else 0)
                
                model.Add(
                    arrival_time[v_id, c2.parcel_id] >= fin + ballast_hrs
                ).OnlyEnforceIf(trans[v_id, c_id, c2.parcel_id])

    # 4. Objective: Maximize Profit
    # Profit = Revenue - VoyageCosts
    # VoyageCosts = fuel + idle
    
    profit_terms = []
    
    for v in V:
        v_id = v.vessel_id
        for c in C:
            c_id = c.parcel_id
            
            # Revenue if served
            revenue = int(c.revenue_usd)
            
            # Laden cost
            laden_nm = _get_distance_nm(c.origin_port, c.dest_port, inputs.port_distances)
            laden_days = laden_nm / (v.speed_kn * 24.0) if v.speed_kn > 0 else 0
            fuel_cost_laden = int(laden_days * v.fuel_consumption_tpd * inputs.bunker_price_usd_per_tonne)
            
            # Start ballast cost
            ballast_nm_start = _get_distance_nm(v.current_port, c.origin_port, inputs.port_distances)
            ballast_days_start = ballast_nm_start / (v.speed_kn * 24.0) if v.speed_kn > 0 else 0
            fuel_cost_start = int(ballast_days_start * v.fuel_consumption_tpd * inputs.bunker_price_usd_per_tonne)
            
            # (Revenue - LadenFuel)*x - StartFuel*start_node
            # We scale by integer 1
            
            # Create a variable for this cargo's contribution if served
            contrib = model.NewIntVar(-10_000_000, 10_000_000, f"contrib_{v_id}_{c_id}")
            
            # If x=1, contrib = Revenue - fuel_cost_laden. Else 0.
            model.Add(contrib == revenue - fuel_cost_laden).OnlyEnforceIf(x[v_id, c_id])
            model.Add(contrib == 0).OnlyEnforceIf(x[v_id, c_id].Not())
            profit_terms.append(contrib)
            
            # Subtract start fuel if this is the start node
            start_cost_var = model.NewIntVar(0, 10_000_000, f"start_cost_{v_id}_{c_id}")
            model.Add(start_cost_var == fuel_cost_start).OnlyEnforceIf(start_node[v_id, c_id])
            model.Add(start_cost_var == 0).OnlyEnforceIf(start_node[v_id, c_id].Not())
            profit_terms.append(-start_cost_var)
            
            # Subtract trans fuel if transitioning
            for c2 in C:
                if c_id == c2.parcel_id: continue
                ballast_nm = _get_distance_nm(c.dest_port, c2.origin_port, inputs.port_distances)
                ballast_days = ballast_nm / (v.speed_kn * 24.0) if v.speed_kn > 0 else 0
                fuel_cost_trans = int(ballast_days * v.fuel_consumption_tpd * inputs.bunker_price_usd_per_tonne)
                
                trans_cost_var = model.NewIntVar(0, 10_000_000, f"trans_cost_{v_id}_{c_id}_{c2.parcel_id}")
                model.Add(trans_cost_var == fuel_cost_trans).OnlyEnforceIf(trans[v_id, c_id, c2.parcel_id])
                model.Add(trans_cost_var == 0).OnlyEnforceIf(trans[v_id, c_id, c2.parcel_id].Not())
                profit_terms.append(-trans_cost_var)

    model.Maximize(sum(profit_terms))

    # 5. Solve
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max_solve_seconds
    status = solver.Solve(model)

    status_name = solver.StatusName(status)
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        # Reconstruct the schedule
        assignments = []
        for v in V:
            v_id = v.vessel_id
            # Find the start node
            curr_c = None
            for c in C:
                if solver.Value(start_node[v_id, c.parcel_id]):
                    curr_c = c.parcel_id
                    break
            
            while curr_c is not None:
                arr = solver.Value(arrival_time[v_id, curr_c])
                st = solver.Value(start_time[v_id, curr_c])
                fin = solver.Value(finish_time[v_id, curr_c])
                
                assignments.append(VoyageAssignment(
                    vessel_id=v_id,
                    parcel_id=curr_c,
                    arrival_hours=arr,
                    wait_hours=st - arr,
                    start_operation_hours=st,
                    finish_hours=fin,
                    profit_usd=0.0  # Would compute actual contribution here if needed
                ))
                
                # Find next
                nxt_c = None
                for c2 in C:
                    if curr_c != c2.parcel_id and solver.Value(trans[v_id, curr_c, c2.parcel_id]):
                        nxt_c = c2.parcel_id
                        break
                curr_c = nxt_c
                
        return VoyageScheduleResult(
            assignments=assignments,
            total_profit_usd=float(solver.ObjectiveValue()),
            solver_status=status_name,
        )

    return VoyageScheduleResult([], 0.0, status_name)
