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
from opt.types import OptimizerInputs, VesselClass
from opt.network import PortEnum
from opt.voyage import schedule_voyages


@dataclass
class OptimizerRecommendation:
    # 1. Lock/wait + ceiling
    lock_action: Literal["LOCK", "WAIT"]
    ceiling_usd_per_day: float
    lock_wait_text: str
    
    # 2. Voyage schedule
    voyage_schedule_text: str
    total_voyage_profit_usd: float
    
    # 3. Repositioning
    repositioning_text: str
    
    # 4. Savings, as a range
    savings_text: str
    expected_savings_usd_per_day: float
    p10_savings_usd_per_day: float
    
    # 5. Review date
    review_trigger_text: str


def run_optimizer(inputs: OptimizerInputs, target_class: VesselClass) -> OptimizerRecommendation:
    """The unified Black-Box optimizer function."""
    
    # --- 1. Lock/Wait & Ceiling ---
    tc_quote = inputs.tc_quotes.get(target_class, 0.0)
    # Use the route family of the first parcel if available to inform the TC vs Spot decision
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
    
    if lw_result.action == "LOCK":
        lw_text = f"Sign a {inputs.contract_term_days}-day {target_class.value} TC now at ${tc_quote:,.0f}/day (Ceiling is ${lw_result.ceiling_usd_per_day:,.0f}/day)."
    else:
        lw_text = f"WAIT. Do not sign at ${tc_quote:,.0f}/day. Walk away above ${lw_result.ceiling_usd_per_day:,.0f}/day."

    # --- 2. Voyage Scheduling ---
    vs_result = schedule_voyages(inputs, max_solve_seconds=5.0)
    
    if not vs_result.assignments:
        vs_text = "No profitable voyages found in the current pool."
    else:
        # Group by vessel for text output
        v_schedules = {}
        for a in vs_result.assignments:
            if a.vessel_id not in v_schedules:
                v_schedules[a.vessel_id] = []
            v_schedules[a.vessel_id].append(a)
            
        vs_lines = []
        for v_id, assigns in v_schedules.items():
            # Sort by start time
            assigns.sort(key=lambda x: x.start_operation_hours)
            seq = " -> ".join([f"parcel {a.parcel_id} (Hr {a.start_operation_hours})" for a in assigns])
            vs_lines.append(f"Vessel {v_id} routes: {seq}")
        vs_text = "\n".join(vs_lines)

    # --- 3. Repositioning ---
    # Find a vessel that has NO assignments to recommend repositioning
    assigned_vessels = {a.vessel_id for a in vs_result.assignments}
    idle_vessels = [v for v in inputs.vessels if v.vessel_id not in assigned_vessels]
    
    repo_lines = []
    # Arbitrary candidate ports for the heuristic
    candidates = list(PortEnum)
    
    for v in idle_vessels:
        repo_rec = recommend_repositioning(v, candidates, inputs)
        if repo_rec.recommended_port == v.current_port:
            repo_lines.append(f"Vessel {v.vessel_id}: Stay at {v.current_port} (best available local market).")
        else:
            repo_lines.append(f"Vessel {v.vessel_id}: Don't idle at {v.current_port}. Sail empty toward {repo_rec.recommended_port}.")
            
    repo_text = "\n".join(repo_lines) if repo_lines else "All vessels are scheduled; no immediate repositioning needed."

    # --- 4. Monte Carlo Savings ---
    mc_dist = estimate_savings_distribution(
        forecasts=inputs.forecasts,
        vessel_class=target_class,
        contract_term_days=inputs.contract_term_days,
        today_quote_usd_per_day=tc_quote,
        num_simulations=2000
    )
    
    if lw_result.action == "LOCK":
        sav_text = f"+${mc_dist.expected_p50_savings:,.0f}/day expected vs always-spot; even in bad runs +${mc_dist.worst_case_p10_savings:,.0f}/day (P10)."
    else:
        # If we wait, our savings against the current bad quote is positive.
        sav_text = f"Waiting avoids an expected loss of ${-mc_dist.expected_p50_savings:,.0f}/day."

    # --- 5. Review Trigger ---
    review_text = "Re-solve weekly, or immediately if BDI jumps >5%."

    return OptimizerRecommendation(
        lock_action=lw_result.action,
        ceiling_usd_per_day=lw_result.ceiling_usd_per_day,
        lock_wait_text=lw_text,
        voyage_schedule_text=vs_text,
        total_voyage_profit_usd=vs_result.total_profit_usd,
        repositioning_text=repo_text,
        savings_text=sav_text,
        expected_savings_usd_per_day=mc_dist.expected_p50_savings,
        p10_savings_usd_per_day=mc_dist.worst_case_p10_savings,
        review_trigger_text=review_text
    )
