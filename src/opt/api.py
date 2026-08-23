"""Unified Black-Box API for the Optimizer.

Orchestrates the 4 sub-engines into a single function call that returns
the exact 5 outputs defined in the product specification.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from opt.ceiling import lock_or_wait
from opt.monte_carlo import estimate_savings_distribution
from opt.repositioning import recommend_repositioning
from opt.types import (
    OptimizerInputs, 
    VesselClass, 
    OptimizerRecommendation,
    RejectedOption,
    RepositioningAction,
    ReviewTrigger
)
from opt.network import PortEnum
from opt.voyage import schedule_voyages


def run_optimizer(inputs: OptimizerInputs, target_class: VesselClass) -> OptimizerRecommendation:
    """The unified Black-Box optimizer function."""
    
    # --- 1. Lock/Wait & Ceiling ---
    tc_quote = inputs.tc_quotes.get(target_class, 0.0)
    route_basis = None
    if inputs.parcels and inputs.parcels[0].route_family in inputs.basis:
        route_basis = inputs.basis[inputs.parcels[0].route_family]
        
    lw_result = lock_or_wait(
        forecasts=inputs.forecasts,
        vessel_class=target_class,
        contract_term_days=inputs.contract_term_days,
        today_quote_usd_per_day=tc_quote,
        risk_tolerance=inputs.risk_tolerance,
        basis=route_basis
    )
    
    start_day = None
    end_day = None
    min_p50 = None
    if lw_result.action == "WAIT":
        from opt.monte_carlo import _interpolate_fan_for_day
        class_fans = [f for f in inputs.forecasts if f.vessel_class == target_class]
        
        best_day = 1
        min_p50 = float('inf')
        for d in range(1, inputs.planning_horizon_days + 1):
            _, p50, _ = _interpolate_fan_for_day(class_fans, d)
            if p50 < min_p50:
                min_p50 = p50
                best_day = d
                
        start_day = max(1, best_day - 3)
        end_day = min(inputs.planning_horizon_days, best_day + 3)

    # --- 2. Voyage Scheduling ---
    vs_result = schedule_voyages(inputs, max_solve_seconds=5.0)
    
    rejected_opts = [
        RejectedOption(vessel_id=v_id, parcel_id=c_id, reason=reason)
        for v_id, c_id, reason in vs_result.infeasible_pairs
    ]

    # --- 3. Repositioning ---
    assigned_vessels = {a.vessel_id for a in vs_result.assignments}
    idle_vessels = [v for v in inputs.vessels if v.vessel_id not in assigned_vessels]
    
    candidates = list(PortEnum)
    repo_actions = []
    for v in idle_vessels:
        repo_rec = recommend_repositioning(v, candidates, inputs)
        repo_actions.append(RepositioningAction(
            vessel_id=v.vessel_id,
            current_port=v.current_port,
            recommended_port=repo_rec.recommended_port,
            is_staying=repo_rec.recommended_port == v.current_port
        ))

    # --- 4. Monte Carlo Savings ---
    mc_dist = estimate_savings_distribution(
        forecasts=inputs.forecasts,
        vessel_class=target_class,
        contract_term_days=inputs.contract_term_days,
        today_quote_usd_per_day=tc_quote,
        num_simulations=2000
    )
    
    # --- 5. Review Trigger ---
    review_trigger = ReviewTrigger(
        schedule="WEEKLY",
        conditions=["BDI jumps >5%"]
    )

    return OptimizerRecommendation(
        target_vessel_class=target_class,
        lock_action=lw_result.action,
        ceiling_usd_per_day=lw_result.ceiling_usd_per_day,
        tc_quote_usd_per_day=tc_quote,
        contract_term_days=inputs.contract_term_days,
        optimal_entry_window_start_day=start_day,
        optimal_entry_window_end_day=end_day,
        optimal_entry_window_p50_usd=min_p50,
        voyage_assignments=vs_result.assignments,
        rejected_options=rejected_opts,
        total_voyage_profit_usd=vs_result.total_profit_usd,
        repositioning_actions=repo_actions,
        expected_savings_usd_per_day=mc_dist.expected_p50_savings,
        p10_savings_usd_per_day=mc_dist.worst_case_p10_savings,
        review_trigger=review_trigger
    )


def format_recommendation_text(rec: OptimizerRecommendation) -> str:
    """Formats the structured recommendation back into human-readable text."""
    
    # 1. Lock/Wait
    if rec.lock_action == "LOCK":
        lw_text = f"Sign a {rec.contract_term_days}-day {rec.target_vessel_class.value} TC now at ${rec.tc_quote_usd_per_day:,.0f}/day (Ceiling is ${rec.ceiling_usd_per_day:,.0f}/day)."
    else:
        lw_text = (
            f"WAIT. Do not sign at ${rec.tc_quote_usd_per_day:,.0f}/day. Walk away above ${rec.ceiling_usd_per_day:,.0f}/day.\n"
            f"Optimal entry window: Day {rec.optimal_entry_window_start_day} to {rec.optimal_entry_window_end_day} "
            f"(P50 trough at ~${rec.optimal_entry_window_p50_usd:,.0f}/day)."
        )

    # 2. Voyage Schedule
    if not rec.voyage_assignments:
        vs_text = "No profitable voyages found in the current pool."
        if rec.rejected_options:
            vs_text += "\n\nRejected options:\n" + "\n".join(
                [f"  - Vessel {opt.vessel_id} -> Parcel {opt.parcel_id}: {opt.reason}" for opt in rec.rejected_options]
            )
    else:
        v_schedules = {}
        for a in rec.voyage_assignments:
            if a.vessel_id not in v_schedules:
                v_schedules[a.vessel_id] = []
            v_schedules[a.vessel_id].append(a)
            
        vs_lines = []
        for v_id, assigns in v_schedules.items():
            assigns.sort(key=lambda x: x.start_operation_hours)
            seq = " -> ".join([f"parcel {a.parcel_id} to {a.dest_port.name} (Hr {a.start_operation_hours})" for a in assigns])
            vs_lines.append(f"Vessel {v_id} routes: {seq}")
            
        if rec.rejected_options:
            vs_lines.append("\nRejected options:")
            for opt in rec.rejected_options:
                vs_lines.append(f"  - Vessel {opt.vessel_id} -> Parcel {opt.parcel_id}: {opt.reason}")
                
        vs_text = "\n".join(vs_lines)

    # 3. Repositioning
    repo_lines = []
    for action in rec.repositioning_actions:
        if action.is_staying:
            repo_lines.append(f"Vessel {action.vessel_id}: Stay at {action.current_port.name} (best available local market).")
        else:
            repo_lines.append(f"Vessel {action.vessel_id}: Don't idle at {action.current_port.name}. Sail empty toward {action.recommended_port.name}.")
    repo_text = "\n".join(repo_lines) if repo_lines else "All vessels are scheduled; no immediate repositioning needed."

    # 4. Savings
    if rec.lock_action == "LOCK":
        sav_text = f"+${rec.expected_savings_usd_per_day:,.0f}/day expected vs always-spot; even in bad runs +${rec.p10_savings_usd_per_day:,.0f}/day (P10)."
    else:
        sav_text = f"Waiting avoids an expected loss of ${-rec.expected_savings_usd_per_day:,.0f}/day."

    # 5. Review Trigger
    review_text = f"Re-solve {rec.review_trigger.schedule.lower()}, or immediately if " + " or ".join(rec.review_trigger.conditions) + "."

    return f"--- OPTIMIZER RECOMMENDATION ---\n\n{lw_text}\n\n{vs_text}\n\n{repo_text}\n\n{sav_text}\n\n{review_text}"
