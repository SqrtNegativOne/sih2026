"""Monte Carlo Savings Estimator (Sub-problem 4)

Simulates thousands of potential market futures (price paths) drawn from the 
ML model's uncertainty fans. 

By running our decisions through these "parallel universes," we can extract 
not just the expected (mean) savings, but the full risk distribution 
(e.g., the P10 "bad run" floor).
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
import random

from opt.types import ForecastFan, VesselClass


@dataclass
class SavingsDistribution:
    vessel_class: VesselClass
    contract_term_days: int
    quote_usd_per_day: float
    
    # Savings ($/day) compared to staying spot
    expected_p50_savings: float
    worst_case_p10_savings: float
    best_case_p90_savings: float
    
    # Probability that locking beats staying spot
    prob_positive_savings: float


def _interpolate_fan_for_day(fans: list[ForecastFan], day: int) -> tuple[float, float, float]:
    """Get (P10, P50, P90) for a specific day using nearest/linear interpolation."""
    sorted_fans = sorted(fans, key=lambda f: f.horizon_days)
    
    if not sorted_fans:
        raise ValueError("Empty forecast fans list.")
        
    # Flat extrapolation before the first horizon
    if day <= sorted_fans[0].horizon_days:
        f = sorted_fans[0]
        return f.p10, f.p50, f.p90
        
    # Flat extrapolation after the last horizon
    if day >= sorted_fans[-1].horizon_days:
        f = sorted_fans[-1]
        return f.p10, f.p50, f.p90
        
    # Linear interpolation between horizons
    for i in range(len(sorted_fans) - 1):
        f1 = sorted_fans[i]
        f2 = sorted_fans[i+1]
        if f1.horizon_days < day < f2.horizon_days:
            weight = (day - f1.horizon_days) / (f2.horizon_days - f1.horizon_days)
            p10 = f1.p10 + weight * (f2.p10 - f1.p10)
            p50 = f1.p50 + weight * (f2.p50 - f1.p50)
            p90 = f1.p90 + weight * (f2.p90 - f1.p90)
            return p10, p50, p90
            
    # Fallback (should be unreachable due to checks above)
    f = sorted_fans[-1]
    return f.p10, f.p50, f.p90


def estimate_savings_distribution(
    forecasts: list[ForecastFan],
    vessel_class: VesselClass,
    contract_term_days: int,
    today_quote_usd_per_day: float,
    num_simulations: int = 5000,
) -> SavingsDistribution:
    """Run a Monte Carlo simulation to generate the distribution of savings.
    
    Savings = Simulated Spot Rate Average - TC Quote.
    """
    class_fans = [f for f in forecasts if f.vessel_class == vessel_class]
    if not class_fans:
        raise ValueError(f"No forecasts provided for {vessel_class.value}")

    # Standard normal z-score for the 10th and 90th percentiles
    Z_90 = 1.28155 
    
    simulated_savings = []
    
    for _ in range(num_simulations):
        # 1. Draw a market shock for this universe (standard normal)
        # We assume shocks are highly auto-correlated over the term for simplicity 
        # (if the market is in a bull cycle, it stays in a bull cycle).
        market_shock_z = random.gauss(0, 1)
        
        path_spot_sum = 0.0
        
        # 2. Walk forward day by day
        for day in range(1, contract_term_days + 1):
            p10, p50, p90 = _interpolate_fan_for_day(class_fans, day)
            
            # Map the Z-shock to the skewed P10/P50/P90 fan
            if market_shock_z >= 0:
                # Top half of the distribution
                day_spot = p50 + market_shock_z * ((p90 - p50) / Z_90)
            else:
                # Bottom half of the distribution
                day_spot = p50 + market_shock_z * ((p50 - p10) / Z_90)
                
            # Prices can't drop below OPEX (e.g., ~$4,000/day absolute floor)
            day_spot = max(4000.0, day_spot)
            
            path_spot_sum += day_spot
            
        path_spot_avg = path_spot_sum / contract_term_days
        
        # Savings = What we would have paid on spot - what we pay on TC
        savings = path_spot_avg - today_quote_usd_per_day
        simulated_savings.append(savings)
        
    simulated_savings.sort()
    
    # Extract percentiles
    idx_p10 = int(num_simulations * 0.10)
    idx_p50 = int(num_simulations * 0.50)
    idx_p90 = int(num_simulations * 0.90)
    
    prob_positive = sum(1 for s in simulated_savings if s > 0) / num_simulations

    return SavingsDistribution(
        vessel_class=vessel_class,
        contract_term_days=contract_term_days,
        quote_usd_per_day=today_quote_usd_per_day,
        expected_p50_savings=simulated_savings[idx_p50],
        worst_case_p10_savings=simulated_savings[idx_p10],
        best_case_p90_savings=simulated_savings[idx_p90],
        prob_positive_savings=prob_positive,
    )
