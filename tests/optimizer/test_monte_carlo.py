import pytest
from sih.optimizer.types import ForecastFan, VesselClass
from sih.optimizer.monte_carlo import estimate_savings_distribution

def test_monte_carlo_savings():
    # Model predicts market going UP from 10k to 14k over 90 days.
    # Fan is fairly wide (uncertainty).
    fans = [
        ForecastFan(VesselClass.SUPRAMAX, 7,  p10=8000,  p50=10000, p90=12000),
        ForecastFan(VesselClass.SUPRAMAX, 30, p10=9000,  p50=12000, p90=15000),
        ForecastFan(VesselClass.SUPRAMAX, 90, p10=10000, p50=14000, p90=18000),
    ]
    
    # We are offered a TC quote of 11,500 for a 30-day term.
    # The P50 spot average over those 30 days is roughly 11,000 (starts at 10k, ends at 12k).
    # Since the quote (11,500) is slightly HIGHER than the P50 spot average (11,000),
    # the EXPECTED savings should be slightly negative (we lose money by locking).
    # But because the market *could* explode upward (P90 is 15k), the best-case savings will be positive.
    
    dist = estimate_savings_distribution(
        forecasts=fans,
        vessel_class=VesselClass.SUPRAMAX,
        contract_term_days=30,
        today_quote_usd_per_day=11_500,
        num_simulations=10_000 # High count for stable percentiles
    )
    
    assert dist.vessel_class == VesselClass.SUPRAMAX
    assert dist.contract_term_days == 30
    
    # P50 savings should be roughly -500 (since spot avg ~ 11,000 and quote is 11,500)
    assert -1000 < dist.expected_p50_savings < 0
    
    # Worst case P10 (market drops to the 8k-9k range)
    # Spot avg ~ 8.5k. Quote = 11.5k. Savings = -3000.
    assert -4000 < dist.worst_case_p10_savings < -2000
    
    # Best case P90 (market moons to the 12k-15k range)
    # Spot avg ~ 13.5k. Quote = 11.5k. Savings = +2000.
    assert 1000 < dist.best_case_p90_savings < 3000
    
    # There should be *some* probability of positive savings, but < 50%
    assert 0.1 < dist.prob_positive_savings < 0.5
