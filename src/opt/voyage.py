"""Voyage Scheduling (Sub-problem 2)

Assigns vessels to cargo parcels and sequences them to maximize profit (TCE).
Uses Google OR-Tools CP-SAT solver.

Costs factored into the objective:
  - Laden fuel (origin -> destination)
  - Ballast fuel (current port -> first cargo; between cargoes)
  - Ballast time penalty ($/day for deadheading)
  - Inter-cargo idle gap penalty ($/day for sitting around between voyages)
  - Port queue wait penalty ($/day for congestion wait at laycan start)

Constraints enforced:
  - Each cargo served by exactly one vessel (or dropped if unprofitable)
  - Vessel DWT and draft compatibility with origin AND destination port
  - Laycan time windows (arrive before end, can't load before start)
  - Physical sequencing (finish c1 + ballast time <= arrive c2)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ortools.sat.python import cp_model

from opt.types import OptimizerInputs, Vessel, CargoParcel, VoyageAssignment
from opt.network import PortEnum, RouteEnum, BLENDED_BUNKER_USD_PER_TONNE


@dataclass
class VoyageScheduleResult:
    assignments: list[VoyageAssignment]
    total_profit_usd: float
    solver_status: str
    infeasible_pairs: list[tuple[str, str, str]]  # (vessel_id, parcel_id, reason) ruled out by port constraints


def _get_distance_nm(
    port_a: PortEnum, port_b: PortEnum
) -> float:
    if port_a == port_b:
        return 0.0
    for r in RouteEnum:
        if (r.value.origin.id == port_a.value.id and r.value.destination.id == port_b.value.id) or \
           (r.value.origin.id == port_b.value.id and r.value.destination.id == port_a.value.id):
            return r.value.distance_nm
    return 10_000.0


def _vessel_can_call(
    vessel: Vessel, port: PortEnum
) -> tuple[bool, str | None]:
    spec = port.value
    if spec.max_dwt is not None and vessel.dwt > spec.max_dwt:
        return False, f"Vessel DWT ({vessel.dwt}) exceeds port max ({spec.max_dwt}) at {port.name}"
    if spec.max_draft_m is not None and vessel.draft_m > spec.max_draft_m:
        return False, f"Vessel draft ({vessel.draft_m}m) exceeds port max ({spec.max_draft_m}m) at {port.name}"
    return True, None


def schedule_voyages(
    inputs: OptimizerInputs,
    max_solve_seconds: float = 30.0,
) -> VoyageScheduleResult:
    """Solve the Voyage Scheduling (PDPTW) problem using OR-Tools CP-SAT.

    Maximizes total fleet profit = revenue - fuel - ballast_penalty - idle_penalty.
    """
    model = cp_model.CpModel()

    if not inputs.vessels or not inputs.parcels:
        return VoyageScheduleResult([], 0.0, "NO_DATA", [])

    epoch_date = min(v.available_from for v in inputs.vessels)

    def date_to_hours(d: Any) -> int:
        from datetime import datetime
        if isinstance(d, datetime):
            d = d.date()
        e = epoch_date
        if isinstance(e, datetime):
            e = e.date()
        return (d - e).days * 24

    C: list[CargoParcel] = []
    parent_map: dict[str, str] = {} # sub_id -> parent_id
    group_map: dict[str, list[str]] = {} # parent_id -> list of sub_ids
    
    for p in inputs.parcels:
        dests = [p.dest_port]
        if p.alternative_dest_ports:
            for dp in p.alternative_dest_ports:
                if dp not in dests:
                    dests.append(dp)
        
        group_map[p.parcel_id] = []
        for d in dests:
            sub_id = f"{p.parcel_id}__{d.name}"
            sub_p = p.model_copy(update={"parcel_id": sub_id, "dest_port": d})
            C.append(sub_p)
            parent_map[sub_id] = p.parcel_id
            group_map[p.parcel_id].append(sub_id)

    V = inputs.vessels

    max_time_hours = (inputs.planning_horizon_days + 60) * 24

    # Pre-compute windows
    cargo_windows = {
        c.parcel_id: {
            "start": date_to_hours(c.laycan_start),
            "end": date_to_hours(c.laycan_end),
        }
        for c in C
    }

    vessel_props = {
        v.vessel_id: {
            "available": date_to_hours(v.available_from),
            "port": v.current_port,
        }
        for v in V
    }

    # ---------------------------------------------------------------------------
    # Port compatibility pre-filter
    # ---------------------------------------------------------------------------
    # Build a set of (v_id, c_id) pairs that are physically impossible.
    infeasible_pairs: list[tuple[str, str, str]] = []
    feasible: dict[tuple[str, str], bool] = {}
    for v in V:
        for c in C:
            ok_orig, reason_orig = _vessel_can_call(v, c.origin_port)
            ok_dest, reason_dest = _vessel_can_call(v, c.dest_port)
            ok = ok_orig and ok_dest
            feasible[v.vessel_id, c.parcel_id] = ok
            if not ok:
                reason = reason_orig if not ok_orig else reason_dest
                infeasible_pairs.append((v.vessel_id, c.parcel_id, reason)) # type: ignore

    # ---------------------------------------------------------------------------
    # Decision variables
    # ---------------------------------------------------------------------------
    served_group = {p.parcel_id: model.NewBoolVar(f"served_grp_{p.parcel_id}") for p in inputs.parcels}

    x: dict[tuple[str, str], Any] = {}
    for v in V:
        for c in C:
            x[v.vessel_id, c.parcel_id] = model.NewBoolVar(f"x_{v.vessel_id}_{c.parcel_id}")

    # Infeasible pairs are permanently 0
    for v_id, c_id, _ in infeasible_pairs:
        model.Add(x[v_id, c_id] == 0)

    # Each parent cargo served by at most one vessel at exactly one port
    for p in inputs.parcels:
        model.Add(
            sum(x[v.vessel_id, sub_id] for v in V for sub_id in group_map[p.parcel_id]) 
            == served_group[p.parcel_id]
        )

    # Routing flow variables
    trans: dict[tuple[str, str, str], Any] = {}
    start_node: dict[tuple[str, str], Any] = {}
    end_node: dict[tuple[str, str], Any] = {}
    for v in V:
        for c in C:
            v_id = v.vessel_id
            c_id = c.parcel_id
            start_node[v_id, c_id] = model.NewBoolVar(f"snode_{v_id}_{c_id}")
            end_node[v_id, c_id] = model.NewBoolVar(f"enode_{v_id}_{c_id}")
            for c2 in C:
                if parent_map[c_id] != parent_map[c2.parcel_id]:
                    trans[v_id, c_id, c2.parcel_id] = model.NewBoolVar(f"tr_{v_id}_{c_id}_{c2.parcel_id}")

    # Flow conservation
    for v in V:
        v_id = v.vessel_id
        model.Add(sum(start_node[v_id, c.parcel_id] for c in C) <= 1)
        model.Add(sum(end_node[v_id, c.parcel_id] for c in C) <= 1)

        for c in C:
            c_id = c.parcel_id
            in_edges = start_node[v_id, c_id] + sum(
                trans[v_id, p.parcel_id, c_id] for p in C if parent_map[p.parcel_id] != parent_map[c_id]
            )
            model.Add(in_edges == x[v_id, c_id])

            out_edges = end_node[v_id, c_id] + sum(
                trans[v_id, c_id, n.parcel_id] for n in C if parent_map[n.parcel_id] != parent_map[c_id]
            )
            model.Add(out_edges == x[v_id, c_id])

    # ---------------------------------------------------------------------------
    # Time variables — first pass: declare
    # ---------------------------------------------------------------------------
    arrival_time: dict[tuple[str, str], Any] = {}
    start_time: dict[tuple[str, str], Any] = {}
    finish_time: dict[tuple[str, str], Any] = {}
    actual_laden_hours_var: dict[tuple[str, str], Any] = {}
    actual_port_hours_var: dict[tuple[str, str], Any] = {}

    for v in V:
        for c in C:
            v_id, c_id = v.vessel_id, c.parcel_id
            arrival_time[v_id, c_id] = model.NewIntVar(0, max_time_hours, f"arr_{v_id}_{c_id}")
            start_time[v_id, c_id] = model.NewIntVar(0, max_time_hours, f"st_{v_id}_{c_id}")
            finish_time[v_id, c_id] = model.NewIntVar(0, max_time_hours, f"fin_{v_id}_{c_id}")
            actual_laden_hours_var[v_id, c_id] = model.NewIntVar(0, max_time_hours, f"act_laden_{v_id}_{c_id}")
            actual_port_hours_var[v_id, c_id] = model.NewIntVar(0, max_time_hours, f"act_port_{v_id}_{c_id}")

    # ---------------------------------------------------------------------------
    # Time variables — second pass: constrain
    # ---------------------------------------------------------------------------
    # Pre-compute durations (hours) — deterministic given vessel + cargo
    laden_hours_map: dict[tuple[str, str], int] = {}
    port_hours_map: dict[tuple[str, str], int] = {}
    for v in V:
        for c in C:
            laden_nm = _get_distance_nm(c.origin_port, c.dest_port)
            laden_h = int(laden_nm / v.speed_kn) if v.speed_kn > 0 else 0
            laden_hours_map[v.vessel_id, c.parcel_id] = laden_h

            # Port time from handling rate if available, else 4 days default
            origin_spec = c.origin_port.value
            dest_spec = c.dest_port.value
            if origin_spec and origin_spec.handling_rate_tph:
                load_h = int(c.volume_dwt / origin_spec.handling_rate_tph)
            else:
                load_h = 48  # 2-day default
            if dest_spec and dest_spec.handling_rate_tph:
                disch_h = int(c.volume_dwt / dest_spec.handling_rate_tph)
            else:
                disch_h = 48
            port_hours_map[v.vessel_id, c.parcel_id] = load_h + disch_h

    for v in V:
        for c in C:
            v_id, c_id = v.vessel_id, c.parcel_id
            arr = arrival_time[v_id, c_id]
            st = start_time[v_id, c_id]
            fin = finish_time[v_id, c_id]
            not_x = x[v_id, c_id].Not()

            # Pin to 0 when not assigned (prevents vacuous constraint satisfaction)
            model.Add(arr == 0).OnlyEnforceIf(not_x)
            model.Add(st == 0).OnlyEnforceIf(not_x)
            model.Add(fin == 0).OnlyEnforceIf(not_x)

            # Temporal ordering
            model.Add(st >= arr)
            model.Add(st >= cargo_windows[c_id]["start"]).OnlyEnforceIf(x[v_id, c_id])
            model.Add(arr <= cargo_windows[c_id]["end"]).OnlyEnforceIf(x[v_id, c_id])

            base_laden = laden_hours_map[v_id, c_id]
            base_port = port_hours_map[v_id, c_id]
            
            # 1. Weather Events (Laden Transit Delay)
            delay_vars = []
            route = None
            from opt.network import RouteEnum
            for r in RouteEnum:
                if (r.value.origin.id == c.origin_port.value.id and r.value.destination.id == c.dest_port.value.id) or \
                   (r.value.origin.id == c.dest_port.value.id and r.value.destination.id == c.origin_port.value.id):
                    route = r
                    break
            
            if route:
                weather_events_on_route = [w for w in inputs.weather_events if w.route == route]
                for w in weather_events_on_route:
                    w_start_h = date_to_hours(w.start_time)
                    w_end_h = date_to_hours(w.end_time)
                    
                    hits_weather = model.NewBoolVar(f"weather_{v_id}_{c_id}_{w.event_id}")
                    
                    after_w_start = model.NewBoolVar(f"after_w_start_{v_id}_{c_id}_{w.event_id}")
                    before_w_end = model.NewBoolVar(f"before_w_end_{v_id}_{c_id}_{w.event_id}")
                    
                    model.Add(st >= w_start_h).OnlyEnforceIf(after_w_start)
                    model.Add(st < w_start_h).OnlyEnforceIf(after_w_start.Not())
                    
                    model.Add(st <= w_end_h).OnlyEnforceIf(before_w_end)
                    model.Add(st > w_end_h).OnlyEnforceIf(before_w_end.Not())
                    
                    model.AddBoolAnd([after_w_start, before_w_end]).OnlyEnforceIf(hits_weather)
                    model.AddBoolOr([after_w_start.Not(), before_w_end.Not()]).OnlyEnforceIf(hits_weather.Not())
                    
                    delay_var = model.NewIntVar(0, max_time_hours, f"delay_{v_id}_{c_id}_{w.event_id}")
                    model.Add(delay_var == w.delay_hours).OnlyEnforceIf(hits_weather)
                    model.Add(delay_var == 0).OnlyEnforceIf(hits_weather.Not())
                    delay_vars.append(delay_var)
            
            actual_laden_var = actual_laden_hours_var[v_id, c_id]
            model.Add(actual_laden_var == base_laden + sum(delay_vars)).OnlyEnforceIf(x[v_id, c_id])
            model.Add(actual_laden_var == 0).OnlyEnforceIf(not_x)

            # 2. Port Logistics (Congestion Wait & Handling Rate)
            port_events_at_origin = [e for e in inputs.port_events if e.port == c.origin_port]
            extra_port_vars = []

            for e in port_events_at_origin:
                e_start_h = date_to_hours(e.start_time)
                e_end_h = date_to_hours(e.end_time)
                
                is_congested = model.NewBoolVar(f"congest_{v_id}_{c_id}_{e.status_id}")
                
                after_e_start = model.NewBoolVar(f"after_e_start_{v_id}_{c_id}_{e.status_id}")
                before_e_end = model.NewBoolVar(f"before_e_end_{v_id}_{c_id}_{e.status_id}")
                
                model.Add(arr >= e_start_h).OnlyEnforceIf(after_e_start)
                model.Add(arr < e_start_h).OnlyEnforceIf(after_e_start.Not())
                
                model.Add(arr <= e_end_h).OnlyEnforceIf(before_e_end)
                model.Add(arr > e_end_h).OnlyEnforceIf(before_e_end.Not())
                
                model.AddBoolAnd([after_e_start, before_e_end]).OnlyEnforceIf(is_congested)
                model.AddBoolOr([after_e_start.Not(), before_e_end.Not()]).OnlyEnforceIf(is_congested.Not())
                
                model.Add(st - arr >= e.additional_wait_hours).OnlyEnforceIf(is_congested)
                
                if e.handling_rate_multiplier > 0 and e.handling_rate_multiplier < 1.0:
                    extra_hours = int(base_port / e.handling_rate_multiplier) - base_port
                    if extra_hours > 0:
                        extra_p_var = model.NewIntVar(0, max_time_hours, f"extrap_{v_id}_{c_id}_{e.status_id}")
                        model.Add(extra_p_var == extra_hours).OnlyEnforceIf(is_congested)
                        model.Add(extra_p_var == 0).OnlyEnforceIf(is_congested.Not())
                        extra_port_vars.append(extra_p_var)
            
            actual_port_var = actual_port_hours_var[v_id, c_id]
            model.Add(actual_port_var == base_port + sum(extra_port_vars)).OnlyEnforceIf(x[v_id, c_id])
            model.Add(actual_port_var == 0).OnlyEnforceIf(not_x)

            model.Add(fin == st + actual_laden_var + actual_port_var).OnlyEnforceIf(x[v_id, c_id])

            # Ballast from vessel home to first cargo
            ballast_nm = _get_distance_nm(v.current_port, c.origin_port)
            ballast_h = int(ballast_nm / v.speed_kn) if v.speed_kn > 0 else 0
            model.Add(arr >= vessel_props[v_id]["available"] + ballast_h).OnlyEnforceIf(
                start_node[v_id, c_id]
            )

            # Ballast between cargoes
            for c2 in C:
                if parent_map[c_id] == parent_map[c2.parcel_id]:
                    continue
                b_nm = _get_distance_nm(c.dest_port, c2.origin_port)
                b_h = int(b_nm / v.speed_kn) if v.speed_kn > 0 else 0
                model.Add(
                    arrival_time[v_id, c2.parcel_id] >= fin + b_h
                ).OnlyEnforceIf(trans[v_id, c_id, c2.parcel_id])

    # ---------------------------------------------------------------------------
    # Objective: Maximize Profit
    # ---------------------------------------------------------------------------
    # Penalty rates (convert $/day -> $/hour, integer-safe scaling)
    # We work in integer cents to avoid float precision issues.
    SCALE = 100  # 1 unit = $0.01
    opex_per_hour = int(inputs.opex_usd_per_day / 24 * SCALE)
    bunker_per_tonne = BLENDED_BUNKER_USD_PER_TONNE

    profit_terms: list[Any] = []

    for v in V:
        v_id = v.vessel_id
        for c in C:
            c_id = c.parcel_id

            # --- Revenue ---
            revenue_scaled = int(c.revenue_usd * SCALE)
            contrib = model.NewIntVar(-500_000_000, 500_000_000, f"contrib_{v_id}_{c_id}")
            model.Add(contrib == revenue_scaled).OnlyEnforceIf(x[v_id, c_id])
            model.Add(contrib == 0).OnlyEnforceIf(x[v_id, c_id].Not())
            profit_terms.append(contrib)

            # --- Laden fuel cost ---
            laden_nm = _get_distance_nm(c.origin_port, c.dest_port)
            laden_days = laden_nm / (v.speed_kn * 24.0) if v.speed_kn > 0 else 0
            laden_fuel_cost_scaled = int(laden_days * v.fuel_consumption_tpd * bunker_per_tonne * SCALE)

            laden_cost_var = model.NewIntVar(0, 500_000_000, f"laden_cost_{v_id}_{c_id}")
            model.Add(laden_cost_var == laden_fuel_cost_scaled).OnlyEnforceIf(x[v_id, c_id])
            model.Add(laden_cost_var == 0).OnlyEnforceIf(x[v_id, c_id].Not())
            profit_terms.append(-laden_cost_var)

            # --- Ballast fuel from vessel home to first cargo ---
            b_nm_start = _get_distance_nm(v.current_port, c.origin_port)
            b_days_start = b_nm_start / (v.speed_kn * 24.0) if v.speed_kn > 0 else 0
            b_fuel_start_scaled = int(b_days_start * v.fuel_consumption_tpd * bunker_per_tonne * SCALE)

            start_cost_var = model.NewIntVar(0, 500_000_000, f"start_cost_{v_id}_{c_id}")
            model.Add(start_cost_var == b_fuel_start_scaled).OnlyEnforceIf(
                start_node[v_id, c_id]
            )
            model.Add(start_cost_var == 0).OnlyEnforceIf(start_node[v_id, c_id].Not())
            profit_terms.append(-start_cost_var)

            # --- Port queue wait penalty at this cargo's load port ---
            origin_spec = c.origin_port.value
            wait_h_expected = int((origin_spec.expected_wait_days if origin_spec else 0) * 24)
            wait_penalty_scaled = wait_h_expected * opex_per_hour

            wait_cost_var = model.NewIntVar(0, 100_000_000, f"wait_cost_{v_id}_{c_id}")
            model.Add(wait_cost_var == wait_penalty_scaled).OnlyEnforceIf(x[v_id, c_id])
            model.Add(wait_cost_var == 0).OnlyEnforceIf(x[v_id, c_id].Not())
            profit_terms.append(-wait_cost_var)

            # --- Early arrival wait penalty (arriving before laycan) ---
            early_arr_gap = model.NewIntVar(0, max_time_hours, f"early_arr_{v_id}_{c_id}")
            model.Add(early_arr_gap == st - arr).OnlyEnforceIf(x[v_id, c_id])
            model.Add(early_arr_gap == 0).OnlyEnforceIf(x[v_id, c_id].Not())
            early_arr_cost = model.NewIntVar(0, 500_000_000, f"early_arr_cost_{v_id}_{c_id}")
            model.Add(early_arr_cost == early_arr_gap * opex_per_hour).OnlyEnforceIf(x[v_id, c_id])
            model.Add(early_arr_cost == 0).OnlyEnforceIf(x[v_id, c_id].Not())
            profit_terms.append(-early_arr_cost)

            # --- Ballast fuel + inter-cargo idle gap for transitions ---
            for c2 in C:
                if parent_map[c_id] == parent_map[c2.parcel_id]:
                    continue
                b_nm = _get_distance_nm(c.dest_port, c2.origin_port)
                b_days = b_nm / (v.speed_kn * 24.0) if v.speed_kn > 0 else 0
                b_fuel_scaled = int(b_days * v.fuel_consumption_tpd * bunker_per_tonne * SCALE)
                b_hours_int = int(b_days * 24)

                trans_cost_var = model.NewIntVar(0, 500_000_000, f"tr_cost_{v_id}_{c_id}_{c2.parcel_id}")
                model.Add(trans_cost_var == b_fuel_scaled).OnlyEnforceIf(
                    trans[v_id, c_id, c2.parcel_id]
                )
                model.Add(trans_cost_var == 0).OnlyEnforceIf(trans[v_id, c_id, c2.parcel_id].Not())
                profit_terms.append(-trans_cost_var)

                # Inter-cargo idle gap: time vessel sits doing nothing between
                # discharging c1 and departing for c2 = arrival(c2) - finish(c1) - ballast_hours
                # We create a slack variable for this and penalise it.
                idle_gap = model.NewIntVar(0, max_time_hours, f"idle_{v_id}_{c_id}_{c2.parcel_id}")
                # idle_gap = arrival_time[c2] - finish_time[c1] - b_hours (clamped >= 0)
                # When trans=1: arrival_time[c2] >= finish[c1] + b_hours (from constraint above),
                # so idle_gap >= 0 always.
                model.Add(
                    idle_gap == arrival_time[v_id, c2.parcel_id] - finish_time[v_id, c_id] - b_hours_int
                ).OnlyEnforceIf(trans[v_id, c_id, c2.parcel_id])
                model.Add(idle_gap == 0).OnlyEnforceIf(trans[v_id, c_id, c2.parcel_id].Not())

                idle_cost_var = model.NewIntVar(0, 500_000_000, f"idle_cost_{v_id}_{c_id}_{c2.parcel_id}")
                model.Add(idle_cost_var == idle_gap * opex_per_hour).OnlyEnforceIf(
                    trans[v_id, c_id, c2.parcel_id]
                )
                model.Add(idle_cost_var == 0).OnlyEnforceIf(trans[v_id, c_id, c2.parcel_id].Not())
                profit_terms.append(-idle_cost_var)

    model.Maximize(sum(profit_terms))

    # ---------------------------------------------------------------------------
    # Solve
    # ---------------------------------------------------------------------------
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max_solve_seconds
    status = solver.Solve(model)
    status_name = solver.StatusName(status)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return VoyageScheduleResult([], 0.0, status_name, infeasible_pairs)

    # Reconstruct schedule
    assignments: list[VoyageAssignment] = []
    for v in V:
        v_id = v.vessel_id
        curr_c_id: str | None = next(
            (c.parcel_id for c in C if solver.Value(start_node[v_id, c.parcel_id])), None
        )
        prev_fin = 0
        while curr_c_id is not None:
            arr = solver.Value(arrival_time[v_id, curr_c_id])
            st = solver.Value(start_time[v_id, curr_c_id])
            fin = solver.Value(finish_time[v_id, curr_c_id])

            # Ballast hours to get here
            if not assignments or assignments[-1].vessel_id != v_id:
                # First cargo for this vessel
                b_nm = _get_distance_nm(v.current_port, next(c.origin_port for c in C if c.parcel_id == curr_c_id))
                b_h = int(b_nm / v.speed_kn) if v.speed_kn > 0 else 0
                inter_gap_h = 0
            else:
                prev_fin = assignments[-1].finish_hours
                b_nm = _get_distance_nm(
                    assignments[-1].dest_port,
                    next(c.origin_port for c in C if c.parcel_id == curr_c_id)
                )
                b_h = int(b_nm / v.speed_kn) if v.speed_kn > 0 else 0
                inter_gap_h = arr - prev_fin - b_h

            assignments.append(VoyageAssignment(
                vessel_id=v_id,
                parcel_id=parent_map[curr_c_id],
                dest_port=next(c.dest_port for c in C if c.parcel_id == curr_c_id),
                arrival_hours=arr,
                wait_hours=max(0, st - arr),
                start_operation_hours=st,
                finish_hours=fin,
                ballast_hours=b_h,
                inter_cargo_gap_hours=max(0, inter_gap_h),
                profit_usd=0.0
            ))

            curr_c_id = next(
                (c2.parcel_id for c2 in C if parent_map[c2.parcel_id] != parent_map[curr_c_id] and solver.Value(trans[v_id, curr_c_id, c2.parcel_id])),
                None
            )

    return VoyageScheduleResult(
        assignments=assignments,
        total_profit_usd=float(solver.ObjectiveValue()) / SCALE,
        solver_status=status_name,
        infeasible_pairs=infeasible_pairs,
    )
