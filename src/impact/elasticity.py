"""Local elasticity: how much SAIL's own demand moves the rate it faces.

``d(rate)/d(demand)`` is built by the chain rule through tightness:

    d(rate)/d(demand) = d(rate)/d(tightness) * d(tightness)/d(demand)

The second factor is exact and mechanical, not estimated: tightness is defined as
``smoothed_outflow / stock`` (``tonnage.supplycurve``), and fixing a vessel to load
a parcel is itself an outflow event, so an incremental demand of ``dd`` tonnes moves
tightness by exactly ``dd / stock`` -- a definitional derivative with no fitting
error. The first factor is ``tonnage.supplycurve``'s fitted quantile-regression
slope, which is real but -- as that module's own docstring reports -- weak and
wrong-signed on levels for Capesize and Handysize. Every estimate here carries the
``low_confidence`` flag through from there rather than presenting all four classes
as equally trustworthy.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import polars as pl

from opt.types import VesselClass
from tonnage.basins import Basin
from tonnage.stockflow import StockflowResult
from tonnage.supplycurve import SupplyCurveFit


class NoStockDataError(ValueError):
    """Raised when the requested (basin, class, date) has no reconstructed stock."""


@dataclass(frozen=True)
class ElasticityEstimate:
    vessel_class: VesselClass
    basin: Basin
    as_of: date
    quantile: float
    stock_dwt: float
    world_stock_dwt: float
    d_tightness_d_demand: float  # exact: 1 / stock_dwt
    d_rate_d_tightness_usd_per_day: float  # from the supply curve slope, at `quantile`
    d_rate_d_demand_usd_per_day_per_dwt: float  # chain-rule product
    low_confidence: bool
    weak_signal: bool

    @property
    def basin_share_of_world_stock(self) -> float:
        """How much of the *world* pool for this class this basin represents.

        Low share -> SAIL's demand is concentrated against a thin local pool
        (material local impact plausible). High share -> diluted against a much
        larger pool (impact closer to zero) -- the "global impact ~= 0, local
        impact is material" honesty the plan asks for, made a checkable number.
        """
        if self.world_stock_dwt <= 0:
            return float("nan")
        return self.stock_dwt / self.world_stock_dwt

    def price_impact_usd_per_day(self, demand_increment_dwt: float) -> float:
        """Estimated rate move from fixing `demand_increment_dwt` of incremental
        capacity in this (basin, class, week). Linear local approximation -- valid
        near the current tightness level, not for a demand shock large enough to
        move tightness far from where the curve was fit."""
        return self.d_rate_d_demand_usd_per_day_per_dwt * demand_increment_dwt

    def footprint_fraction(self, demand_increment_dwt: float) -> float:
        """What fraction of this basin's reconstructed stock the increment
        consumes -- the number the plan's demo narrative calls the "12-20% of the
        regional ballaster list" footprint.

        Read against ``stock_dwt``, which ``tonnage.validate`` found runs 0.35x to
        26x the real Signal ballaster count depending on class/basin (anchored to
        total registered fleet, not the free/ballasting fraction of it -- see
        ``tonnage.stockflow``'s module docstring). Concretely, on real data this
        comes out around 0.1% for "3 Panamaxes in the Pacific," well under the
        plan's illustrative 12-20% -- because the true regional *ballaster* pool
        (what Signal counts) is a much smaller number than the reconstructed
        *fleet* stock this divides by. Read this as a floor/relative indicator,
        not the calibrated headline percentage.
        """
        if self.stock_dwt <= 0:
            return float("nan")
        return demand_increment_dwt / self.stock_dwt


def local_elasticity(
    stockflow_result: StockflowResult,
    supply_fit: SupplyCurveFit,
    basin: Basin,
    as_of: date,
    quantile: float = 0.5,
) -> ElasticityEstimate:
    if quantile not in supply_fit.slope:
        raise ValueError(f"quantile {quantile} was not fit; available: {sorted(supply_fit.slope)}")

    df = stockflow_result.frame
    row = df.filter(
        (pl.col("basin") == basin.value)
        & (pl.col("vessel_class") == supply_fit.vessel_class.value)
        & (pl.col("date") == as_of)
    )
    if row.is_empty():
        raise NoStockDataError(f"No reconstructed stock for {basin}/{supply_fit.vessel_class}/{as_of}.")
    stock_dwt = float(row["stock_dwt"][0])
    if stock_dwt <= 0:
        raise NoStockDataError(f"Zero reconstructed stock for {basin}/{supply_fit.vessel_class}/{as_of}.")

    world_row = df.filter((pl.col("vessel_class") == supply_fit.vessel_class.value) & (pl.col("date") == as_of))
    world_stock_dwt = float(world_row["stock_dwt"].sum())

    d_tightness_d_demand = 1.0 / stock_dwt
    d_rate_d_tightness = supply_fit.slope[quantile]
    d_rate_d_demand = d_rate_d_tightness * d_tightness_d_demand

    return ElasticityEstimate(
        vessel_class=supply_fit.vessel_class,
        basin=basin,
        as_of=as_of,
        quantile=quantile,
        stock_dwt=stock_dwt,
        world_stock_dwt=world_stock_dwt,
        d_tightness_d_demand=d_tightness_d_demand,
        d_rate_d_tightness_usd_per_day=d_rate_d_tightness,
        d_rate_d_demand_usd_per_day_per_dwt=d_rate_d_demand,
        low_confidence=supply_fit.low_confidence,
        weak_signal=supply_fit.weak_signal,
    )
