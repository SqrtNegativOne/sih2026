"""Output polish for ``opt.quote``: $/MT, per-horizon confidence%, and a
congestion label -- the "final lap" ask, on top of numbers already computed
elsewhere in ``opt``. Nothing here computes a new *decision*; it only
converts/derives real, already-correct numbers into the units and labels a
charterer actually reads (freight is quoted $/MT, not $/day; "how sure" and
"how busy," not a bare z-score).

All three deliberately degrade to ``None``/an honest default rather than
raise or fabricate -- the same graceful-degradation contract every other
real-data signal in this codebase already follows (``opt.congestion``,
``opt.repositioning``'s hazard rates, ``opt.risk``).
"""
from __future__ import annotations

import math
from datetime import date
from typing import Literal

from opt.congestion import dynamic_wait_days
from opt.geography import UnknownPortPairError, distance_nm
from opt.network import PortEnum
from opt.types import ForecastFan

__all__ = [
    "ASSUMED_TRANSIT_SPEED_KN",
    "CongestionLabel",
    "congestion_label",
    "estimate_transit_days",
    "forecast_confidence",
    "usd_per_day_to_usd_per_mt",
]

CongestionLabel = Literal["LOW", "MODERATE", "HIGH"]

#: A representative bulk-carrier laden speed, used only to convert a $/day TC
#: rate into a $/MT freight-equivalent for display -- not a claim about any
#: specific vessel's real service speed (the voyage scheduler, when a real
#: vessel is on hand, uses that vessel's own speed_kn instead). Matches the
#: speed already used for every demo-script vessel in this codebase (13.0-
#: 13.5 kn).
ASSUMED_TRANSIT_SPEED_KN: float = 13.0

#: z-score for the p10/p90 quantiles -- same convention opt.stopping already
#: uses (opt.stopping._Z90) for calibrating a lognormal from a 3-point fan.
_Z90 = 1.2816

#: Multiplier-on-static-baseline thresholds bucketing opt.congestion's
#: continuous estimate into a label a charterer reads at a glance. Centered
#: on 1.0 (== the port's own long-run normal), with the same width on each
#: side; not fitted to anything, a deliberately simple, documented choice.
_LOW_MULTIPLIER_MAX = 0.8
_HIGH_MULTIPLIER_MIN = 1.3


def estimate_transit_days(
    origin: PortEnum, dest: PortEnum, speed_kn: float = ASSUMED_TRANSIT_SPEED_KN
) -> float | None:
    """Real sea distance / assumed speed, in days. None when the pair isn't in
    the real distance matrix (opt.geography.UnknownPortPairError) -- a $/MT
    conversion has nothing honest to report without a real transit time, so
    callers must treat None as "not available," not silently 0."""
    try:
        nm = distance_nm(origin.value.id, dest.value.id)
    except UnknownPortPairError:
        return None
    if speed_kn <= 0:
        return None
    return nm / (speed_kn * 24.0)


def usd_per_day_to_usd_per_mt(
    usd_per_day: float, transit_days: float | None, cargo_volume_dwt: float
) -> float | None:
    """A TC hire rate, restated as a spot/freight-style $ per tonne of cargo:
    (rate x real transit days for this route) / cargo tonnage. This is a
    display conversion, not a real voyage costing (it ignores port time,
    demurrage, and ballast positioning, all of which opt.voyage's own CP-SAT
    schedule already prices properly once a real vessel is on hand) -- it
    exists because "spot" contracts are quoted $/MT in practice and a $/day
    TC number alone doesn't answer that question. None when transit_days or
    cargo_volume_dwt isn't available/positive.
    """
    if transit_days is None or transit_days <= 0 or cargo_volume_dwt <= 0:
        return None
    return usd_per_day * transit_days / cargo_volume_dwt


def forecast_confidence(
    today_quote_usd_per_day: float, fan: ForecastFan
) -> tuple[Literal["up", "down", "flat"], float]:
    """(direction, confidence_pct) for one real forecast horizon, derived from
    the fan's own p10/p50/p90 -- the same piecewise-lognormal-from-quantiles
    calibration opt.stopping already uses (opt.stopping._calibrate_piecewise_
    lognormal), applied to a single horizon instead of a whole path. Confidence
    is P(the real forecast distribution agrees with the direction implied by
    its own p50 vs today's quote), in percent -- a real, computed number, not
    a hardcoded confidence a caller might mistake for calibrated.

    "flat" (confidence 50.0) when p50 == today's quote exactly -- no direction
    to be confident about.
    """
    p10, p50, p90 = fan.p10, fan.p50, fan.p90
    if p50 == today_quote_usd_per_day:
        return "flat", 50.0

    vol_from_p90 = math.log(p90 / p50) / _Z90 if p90 > 0 and p50 > 0 else 0.0
    vol_from_p10 = math.log(p50 / p10) / _Z90 if p50 > 0 and p10 > 0 else 0.0
    sigma = max((vol_from_p90 + vol_from_p10) / 2.0, 1e-9)

    z = (math.log(today_quote_usd_per_day) - math.log(p50)) / sigma
    prob_below_quote = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))  # P(horizon rate < today's quote)

    if p50 < today_quote_usd_per_day:
        return "down", 100.0 * prob_below_quote
    return "up", 100.0 * (1.0 - prob_below_quote)


def congestion_label(port: PortEnum, as_of: date | None = None) -> tuple[CongestionLabel, bool]:
    """(label, is_real_data) for a port's congestion as of ``as_of`` (F-21:
    optional, defaults to None -- the file's own latest real rows, i.e.
    "today", unchanged legacy behaviour), bucketing
    opt.congestion.dynamic_wait_days's real multiplier-on-baseline estimate
    into LOW/MODERATE/HIGH. is_real_data is False exactly when
    dynamic_wait_days itself fell back to the static baseline (no real
    PortWatch coverage) -- in that case the label reflects the port's normal
    baseline wait, not live conditions, and callers should show is_real_data
    alongside it rather than presenting it as a live read."""
    value, is_real = dynamic_wait_days(port, as_of)
    static_baseline = port.value.expected_wait_days
    if static_baseline <= 0:
        return "LOW", is_real
    multiplier = value / static_baseline
    if multiplier <= _LOW_MULTIPLIER_MAX:
        return "LOW", is_real
    if multiplier >= _HIGH_MULTIPLIER_MIN:
        return "HIGH", is_real
    return "MODERATE", is_real
