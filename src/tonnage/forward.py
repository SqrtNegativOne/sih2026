"""Forward projection of available tonnage.

Not a forecast model. It extrapolates the trailing real net-flow rate (and its
day-to-day spread) at each (basin, class) forward at a constant drift, which is a
standard persistence/random-walk-with-drift baseline: the p50 continues the recent
trend in a straight line, and the p10/p90 band widens as sqrt(horizon) the way an
i.i.d.-increment random walk's uncertainty genuinely does. This is honest about what
it cannot see -- a demand shock, a new mine ramping up, a chokepoint closure -- 90
days out, it does not pretend to. What it *can* say honestly is "here is where the
tonnage already in the pipeline puts available capacity if recent port activity
continues," which is exactly the plan's "most of it is already determined by ships
currently at sea" framing: the p50 line is that determinism, and the widening band
around it is the part that genuinely isn't determined yet.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final

import numpy as np
import polars as pl

from opt.types import VesselClass
from tonnage.basins import Basin
from tonnage.stockflow import StockflowResult

#: Trailing days of history whose day-to-day change sets the assumed continuing
#: drift and its spread. Deliberately shorter than a full season/year -- this is a
#: recent-trend extrapolation, not a seasonal model.
TREND_WINDOW_DAYS: Final[int] = 90

DEFAULT_HORIZON_DAYS: Final[int] = 90

#: z-score for an 80% central interval (p10-p90), matching the p10/p50/p90
#: convention already used by opt.types.ForecastFan elsewhere in this codebase.
Z_80: Final[float] = 1.2816


@dataclass(frozen=True)
class ForwardProjection:
    """Long-format forward tonnage projection: date, basin, vessel_class,
    horizon_days, p10, p50, p90 (all >= 0, p10 <= p50 <= p90)."""

    frame: pl.DataFrame
    as_of_date: date
    trend_window_days: int


def project_forward(
    result: StockflowResult,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    trend_window_days: int = TREND_WINDOW_DAYS,
) -> ForwardProjection:
    df = result.frame
    if df.is_empty():
        raise ValueError("stockflow result is empty -- nothing to project forward")
    as_of = df["date"].max()

    rows: list[dict] = []
    for basin in Basin:
        for cls in VesselClass:
            sub = df.filter(
                (pl.col("basin") == basin.value) & (pl.col("vessel_class") == cls.value)
            ).sort("date")
            if sub.is_empty():
                continue
            stock_vals = sub["stock_dwt"].to_numpy()
            last_stock = float(stock_vals[-1])
            tail = stock_vals[-trend_window_days:]
            if len(tail) >= 2:
                daily_changes = np.diff(tail)
                drift = float(np.mean(daily_changes))
                spread = float(np.std(daily_changes, ddof=1)) if len(daily_changes) > 1 else 0.0
            else:
                drift, spread = 0.0, 0.0

            for k in range(1, horizon_days + 1):
                mean_k = last_stock + drift * k
                sigma_k = spread * math.sqrt(k)
                p50 = max(0.0, mean_k)
                p10 = max(0.0, min(mean_k - Z_80 * sigma_k, p50))
                p90 = max(mean_k + Z_80 * sigma_k, p50)
                rows.append(
                    {
                        "date": as_of + timedelta(days=k),
                        "basin": basin.value,
                        "vessel_class": cls.value,
                        "horizon_days": k,
                        "p10": p10,
                        "p50": p50,
                        "p90": p90,
                    }
                )
    return ForwardProjection(
        frame=pl.DataFrame(rows), as_of_date=as_of, trend_window_days=trend_window_days
    )


# ---------------------------------------------------------------------------
# P3 requirement 5 -- forward physical tightness (relative-index branch).
#
# `project_forward` above extrapolates `stock_dwt`, a basin x class DWT-scale
# quantity `tonnage.identification`'s gate labels RELATIVE, not absolute, on
# the real evidence available (see tonnage.identification.evaluate_
# identification_gate). Kept unchanged above -- it has real callers/tests and
# its own honest docstring already frames stock_dwt correctly. This is a
# separate, additive function projecting the actual TIGHTNESS INDEX
# (tonnage.supplycurve.build_tightness_index's dimensionless outflow/stock
# ratio, class-level, all basins pooled) forward with the same persistence/
# random-walk-with-drift methodology -- the honest "forward tightness index"
# the P3 prompt asks for when the gate takes the relative branch, answering
# "is physical tonnage likely to tighten or loosen" with intervals, not a
# point estimate and not a historical heatmap.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TightnessForwardProjection:
    """Long-format forward TIGHTNESS INDEX projection: date, vessel_class,
    horizon_days, p10, p50, p90 (p10 <= p50 <= p90). Unlike `ForwardProjection`
    there is no `basin` column -- `build_tightness_index` is already pooled
    across basins by construction, and this function does not re-introduce a
    basin split it does not have real per-basin rate data to justify.

    Explicitly a RELATIVE index projection: p10/p50/p90 describe a
    dimensionless ratio (trailing outflow / stock), never an absolute DWT
    figure. A consumer must not present this as tonnage available in DWT."""

    frame: pl.DataFrame
    as_of_date: date
    trend_window_days: int
    index_type: str = "RELATIVE"


def project_tightness_forward(
    tightness_index: pl.DataFrame,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    trend_window_days: int = TREND_WINDOW_DAYS,
) -> TightnessForwardProjection:
    """Persistence/random-walk-with-drift projection of the class-level
    tightness index, identical methodology to `project_forward` (same trend
    window, same sqrt(horizon) band widening) applied to
    `tonnage.supplycurve.build_tightness_index`'s output instead of raw
    `stock_dwt`. Tightness is a ratio, not a stock, so it is not clipped to
    >= 0 the same way stock_dwt is (a ratio can be very small but real);
    it IS clipped to a physical floor of 0.0, since a negative draw-down
    ratio has no meaning here (tightness is outflow/stock, both non-negative
    by construction in `build_tightness_index`)."""
    if tightness_index.is_empty():
        raise ValueError("tightness_index is empty -- nothing to project forward")
    as_of = tightness_index["date"].max()

    rows: list[dict] = []
    for cls in VesselClass:
        sub = tightness_index.filter(pl.col("vessel_class") == cls.value).sort("date")
        if sub.is_empty():
            continue
        vals = sub["tightness"].to_numpy()
        last = float(vals[-1])
        tail = vals[-trend_window_days:]
        if len(tail) >= 2:
            daily_changes = np.diff(tail)
            drift = float(np.mean(daily_changes))
            spread = float(np.std(daily_changes, ddof=1)) if len(daily_changes) > 1 else 0.0
        else:
            drift, spread = 0.0, 0.0

        for k in range(1, horizon_days + 1):
            mean_k = last + drift * k
            sigma_k = spread * math.sqrt(k)
            p50 = max(0.0, mean_k)
            p10 = max(0.0, min(mean_k - Z_80 * sigma_k, p50))
            p90 = max(mean_k + Z_80 * sigma_k, p50)
            rows.append(
                {
                    "date": as_of + timedelta(days=k),
                    "vessel_class": cls.value,
                    "horizon_days": k,
                    "p10": p10,
                    "p50": p50,
                    "p90": p90,
                }
            )
    return TightnessForwardProjection(
        frame=pl.DataFrame(rows), as_of_date=as_of, trend_window_days=trend_window_days
    )
