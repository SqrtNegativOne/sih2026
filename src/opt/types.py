"""Shared dataclasses for the optimizer.

All inputs and outputs are plain dataclasses — no business logic here.
Everything is in SI/domain units as documented in docs/02_overview.md:
  - rates in USD/day
  - distances in nautical miles
  - times in calendar days (floats)
  - volumes in dwt (metric tonnes)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Literal


class VesselClass(str, Enum):
    """The four bulk vessel classes tracked by the Baltic indices."""
    CAPESIZE = "Capesize"
    PANAMAX = "Panamax"
    SUPRAMAX = "Supramax"
    HANDYSIZE = "Handysize"


@dataclass(frozen=True)
class ForecastFan:
    """P10/P50/P90 BASE TCE forecast for one (vessel_class, horizon) cell.

    These come directly from the ML model output contract (02_overview.md).
    Values are in USD/day in *linear* space (already exp'd from log-level).
    horizon_days is a calendar-day horizon (7, 30, or 90).
    """
    vessel_class: VesselClass
    horizon_days: int  # 7, 30, or 90
    p10: float  # USD/day
    p50: float  # USD/day
    p90: float  # USD/day

    def __post_init__(self) -> None:
        if not (self.p10 <= self.p50 <= self.p90):
            raise ValueError(
                f"Quantile order violated for {self.vessel_class} h={self.horizon_days}: "
                f"p10={self.p10} p50={self.p50} p90={self.p90}"
            )
        if self.p10 <= 0:
            raise ValueError("TCE forecasts must be positive USD/day values.")
        if self.horizon_days not in {7, 30, 90}:
            raise ValueError(f"Unsupported horizon_days={self.horizon_days}; expected 7, 30, or 90.")


@dataclass(frozen=True)
class BasisEntry:
    """Route-family basis calibration entry (from the BASIS table, 02_overview.md).

    basis_mean: fractional adjustment, e.g. -0.08 means route trades 8% below BASE.
    basis_std:  fractional 1-sigma uncertainty on that adjustment.
    """
    route_family: str
    basis_mean: float  # fraction, e.g. -0.08
    basis_std: float   # fraction, >= 0


@dataclass(frozen=True)
class PortSpec:
    """Per-berth port capability constraints.

    Null values (None) mean the constraint is unknown / not applicable.
    """
    port_id: str
    max_loa_m: float | None = None        # Length Overall in metres
    max_beam_m: float | None = None       # Beam in metres
    max_draft_m: float | None = None      # Draft in metres
    max_dwt: float | None = None          # Deadweight tonnage in metric tonnes
    handling_rate_tph: float | None = None # Tonnes per hour cargo handling
    expected_wait_days: float = 0.0       # Expected anchorage queue time in days


@dataclass(frozen=True)
class Vessel:
    """A physical vessel in the fleet."""
    vessel_id: str
    vessel_class: VesselClass
    current_port: str             # port_id or "at_sea"
    status: Literal["idle", "on_tc", "on_voyage"]
    available_from: date
    available_until: date | None  # None = indefinitely available
    dwt: float                    # metric tonnes
    draft_m: float                # laden draft
    speed_kn: float               # service speed in knots
    fuel_consumption_tpd: float   # metric tonnes fuel per day at service speed


@dataclass(frozen=True)
class CargoParcel:
    """A single cargo parcel to be moved."""
    parcel_id: str
    origin_port: str
    dest_port: str
    commodity: str
    volume_dwt: float       # metric tonnes
    laycan_start: date
    laycan_end: date
    route_family: str       # key into BasisEntry table, e.g. "indo_ec_india"
    revenue_usd: float      # freight revenue (or derived from predicted TCE)


@dataclass
class OptimizerInputs:
    """Full set of inputs passed to the optimizer in a single call.

    Separates user-supplied knobs from model outputs and static reference data.
    """
    # --- from the user ---
    parcels: list[CargoParcel]
    vessels: list[Vessel]
    tc_quotes: dict[VesselClass, float]   # today's broker TC quotes in USD/day
    planning_horizon_days: int
    contract_term_days: int               # desired TC contract length

    # --- from the ML model ---
    forecasts: list[ForecastFan]          # one per (class, horizon) within horizon

    # --- from the basis table ---
    basis: dict[str, BasisEntry]          # keyed by route_family

    # --- static reference ---
    port_specs: dict[str, PortSpec]       # keyed by port_id
    port_distances: dict[tuple[str, str], float] # nautical miles between (port_a, port_b)
    bunker_price_usd_per_tonne: float     # blended fleet-wide bunker price

    # --- risk / penalty hyperparameters ---
    risk_tolerance: float = 0.0   # 0 = risk-neutral (P50), 1 = very risk-averse (P10)
    idle_penalty_usd_per_day: float = 500.0
    ballast_penalty_usd_per_day: float = 250.0
    late_delivery_penalty_usd_per_day: float = 2000.0


@dataclass(frozen=True)
class LockWaitResult:
    """Output of the ceiling calculator for one (vessel_class, contract_term)."""
    vessel_class: VesselClass
    contract_term_days: int
    ceiling_usd_per_day: float      # max rate at which locking still beats spot
    today_quote_usd_per_day: float  # the TC quote being evaluated
    action: Literal["LOCK", "WAIT"]
    expected_spot_cost_usd_per_day: float   # P50-blended expected spot over term
    savings_p50_usd_per_day: float          # ceiling - expected_spot (>0 = lock saves)
    savings_p10_usd_per_day: float          # worst-case savings at P10 spot
    route_adjusted: bool                    # True if a basis adjustment was applied
