"""Repositioning Engine (Sub-problem 3)

Evaluates candidate ports for ballast routing when a vessel is empty.
Balances the expected TCE from the new port against the ballast fuel
cost and port queue time required to get there.
"""
from __future__ import annotations

from dataclasses import dataclass

from opt.ceiling import compute_ceiling
from opt.types import OptimizerInputs, Vessel
from opt.network import PortEnum, RouteEnum, BLENDED_BUNKER_USD_PER_TONNE


@dataclass
class RepositionOption:
    port: PortEnum
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
    current_port: PortEnum
    recommended_port: PortEnum
    options: list[RepositionOption]


def _get_distance_nm(
    port_a: PortEnum, port_b: PortEnum
) -> float:
    if port_a == port_b:
        return 0.0
    for r in RouteEnum:
        if (r.value.origin.id == port_a.value.id and r.value.destination.id == port_b.value.id) or \
           (r.value.origin.id == port_b.value.id and r.value.destination.id == port_a.value.id):
            return r.value.distance_nm
    return 5_000.0


def recommend_repositioning(
    vessel: Vessel,
    candidate_ports: list[PortEnum],
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
        dist_nm = _get_distance_nm(vessel.current_port, port)
        speed = vessel.speed_kn if vessel.speed_kn > 0 else 12.0
        ballast_days = dist_nm / (speed * 24.0)
        
        # Queue / Wait Time
        wait_days = 0.0
        wait_days = port.value.expected_wait_days

        # Costs
        fuel_cost = ballast_days * vessel.ballast_fuel_consumption_tpd * BLENDED_BUNKER_USD_PER_TONNE
        ballast_cost_usd = fuel_cost
        
        wait_cost_usd = wait_days * inputs.opex_usd_per_day

        # Revenue (Expected TCE)
        # Without a specific cargo, we don't know the exact route family to apply.
        # We assume the base P50 TCE.
        basis_mean = 0.0
            
        port_tce = base_tce * (1.0 + basis_mean)
        
        # Score = Expected gross profit of next voyage - cost to get there
        expected_voyage_profit = port_tce * assumed_voyage_days
        score = expected_voyage_profit - ballast_cost_usd - wait_cost_usd

        options.append(RepositionOption(
            port=port,
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
    
    best_port = options[0].port if options else vessel.current_port

    return RepositionRecommendation(
        vessel_id=vessel.vessel_id,
        current_port=vessel.current_port,
        recommended_port=best_port,
        options=options
    )
