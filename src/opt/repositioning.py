"""Repositioning Engine (Sub-problem 3)

Evaluates candidate ports for ballast routing when a vessel is empty.
Balances the expected TCE from the new port against the ballast fuel
cost and port queue time required to get there.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from opt.ceiling import compute_ceiling
from opt.types import BasisEntry, ForecastFan, OptimizerInputs, Vessel, VesselClass


@dataclass
class RepositionOption:
    port_id: str
    ballast_distance_nm: float
    ballast_days: float
    wait_days: float
    ballast_cost_usd: float
    wait_cost_usd: float
    expected_tce_usd_per_day: float
    score_usd: float      # Net expected profit from moving here
    is_current_location: bool


@dataclass
class RepositionRecommendation:
    vessel_id: str
    current_port: str
    recommended_port: str
    options: list[RepositionOption]


def _get_distance_nm(
    port_a: str, port_b: str, distances: dict[tuple[str, str], float]
) -> float:
    """Safely get distance between ports (symmetric). Defaults to 0 if same."""
    if port_a == port_b:
        return 0.0
    if (port_a, port_b) in distances:
        return distances[(port_a, port_b)]
    if (port_b, port_a) in distances:
        return distances[(port_b, port_a)]
    return 5_000.0  # Safe pessimistic fallback


def recommend_repositioning(
    vessel: Vessel,
    candidate_ports: list[str],
    inputs: OptimizerInputs,
    assumed_voyage_days: int = 30,
) -> RepositionRecommendation:
    """Rank candidate ports for repositioning a vessel.
    
    The score evaluates the expected profit of moving to the port and picking
    up a hypothetical cargo, minus the cost of getting there and waiting.
    
    Score = (TCE_at_port * assumed_voyage_days) - BallastCost - WaitCost
    """
    # 1. Get the baseline P50 forecast for the vessel class over the assumed voyage length
    # We use compute_ceiling with risk_tolerance=0 to get the blended P50 spot cost.
    try:
        baseline_res = compute_ceiling(
            inputs.forecasts, vessel.vessel_class,
            contract_term_days=assumed_voyage_days,
            risk_tolerance=inputs.risk_tolerance
        )
        base_tce = baseline_res["expected_spot_p50"]
    except ValueError:
        # Fallback if forecasts are missing for this class
        base_tce = inputs.tc_quotes.get(vessel.vessel_class, 10_000.0)

    options: list[RepositionOption] = []

    for port in candidate_ports:
        # Distance and Time
        dist_nm = _get_distance_nm(vessel.current_port, port, inputs.port_distances)
        speed = vessel.speed_kn if vessel.speed_kn > 0 else 12.0
        ballast_days = dist_nm / (speed * 24.0)
        
        # Queue / Wait Time
        wait_days = 0.0
        if port in inputs.port_specs:
            wait_days = inputs.port_specs[port].expected_wait_days

        # Costs
        fuel_cost = ballast_days * vessel.fuel_consumption_tpd * inputs.bunker_price_usd_per_tonne
        ballast_penalty = ballast_days * inputs.ballast_penalty_usd_per_day
        ballast_cost_usd = fuel_cost + ballast_penalty
        
        wait_cost_usd = wait_days * inputs.idle_penalty_usd_per_day

        # Revenue (Expected TCE adjusted by route basis)
        # We assume the route_family is roughly named after the origin port for this heuristic,
        # or we check if there's a specific basis entry mapping for this port.
        # In a real app, you'd map port -> average route basis.
        basis_mean = 0.0
        if port in inputs.basis:
            basis_mean = inputs.basis[port].basis_mean
            
        port_tce = base_tce * (1.0 + basis_mean)
        
        # Score = Expected gross profit of next voyage - cost to get there
        expected_voyage_profit = port_tce * assumed_voyage_days
        score = expected_voyage_profit - ballast_cost_usd - wait_cost_usd

        options.append(RepositionOption(
            port_id=port,
            ballast_distance_nm=dist_nm,
            ballast_days=ballast_days,
            wait_days=wait_days,
            ballast_cost_usd=ballast_cost_usd,
            wait_cost_usd=wait_cost_usd,
            expected_tce_usd_per_day=port_tce,
            score_usd=score,
            is_current_location=(port == vessel.current_port)
        ))

    # Sort options by score (descending)
    options.sort(key=lambda o: o.score_usd, reverse=True)
    
    best_port = options[0].port_id if options else vessel.current_port

    return RepositionRecommendation(
        vessel_id=vessel.vessel_id,
        current_port=vessel.current_port,
        recommended_port=best_port,
        options=options
    )
