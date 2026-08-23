"""Shared types and Pydantic models for the optimizer."""
from __future__ import annotations

from enum import Enum
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from opt.network import PortEnum, RouteFamily

class VesselClass(str, Enum):
    CAPESIZE = "Capesize"
    PANAMAX = "Panamax"
    SUPRAMAX = "Supramax"
    HANDYSIZE = "Handysize"

class ForecastFan(BaseModel):
    model_config = ConfigDict(frozen=True)

    vessel_class: VesselClass
    horizon_days: Literal[7, 30, 90]
    p10: float
    p50: float
    p90: float

    @model_validator(mode='after')
    def check_quantiles(self) -> 'ForecastFan':
        if not (self.p10 <= self.p50 <= self.p90):
            raise ValueError(f"Quantile order violated: p10={self.p10} p50={self.p50} p90={self.p90}")
        if self.p10 <= 0:
            raise ValueError("TCE forecasts must be positive USD/day values.")
        return self

class BasisEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    route_family: RouteFamily
    basis_mean: float
    basis_std: float

class Vessel(BaseModel):
    model_config = ConfigDict(frozen=True)

    vessel_id: str
    vessel_class: VesselClass
    current_port: PortEnum
    status: Literal["idle", "on_tc", "on_voyage"]
    available_from: date
    available_until: date | None = None
    dwt: float
    draft_m: float
    speed_kn: float
    fuel_consumption_tpd: float

class CargoParcel(BaseModel):
    model_config = ConfigDict(frozen=True)

    parcel_id: str
    origin_port: PortEnum
    dest_port: PortEnum
    commodity: str
    volume_dwt: float
    laycan_start: date
    laycan_end: date
    route_family: RouteFamily
    revenue_usd: float

class OptimizerInputs(BaseModel):
    model_config = ConfigDict(frozen=True)

    parcels: list[CargoParcel]
    vessels: list[Vessel]
    tc_quotes: dict[VesselClass, float]
    planning_horizon_days: int
    contract_term_days: int
    forecasts: list[ForecastFan]
    basis: dict[RouteFamily, BasisEntry]
    
    risk_tolerance: float = 0.0
    idle_penalty_usd_per_day: float = 500.0
    ballast_penalty_usd_per_day: float = 250.0
    late_delivery_penalty_usd_per_day: float = 2000.0

class LockWaitResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    vessel_class: VesselClass
    contract_term_days: int
    ceiling_usd_per_day: float
    today_quote_usd_per_day: float
    action: Literal["LOCK", "WAIT"]
    expected_spot_cost_usd_per_day: float
    savings_p50_usd_per_day: float
    savings_p10_usd_per_day: float
    route_adjusted: bool
