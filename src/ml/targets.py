"""Forward-looking targets on a calendar-day horizon.

The defect this replaces
------------------------
Targets used to be built with ``pl.col("log_value").shift(-h)``, which steps *h rows*
-- h trading days -- while every consumer treats h as calendar days. The gap is not
small. Measured on the existing Capesize training split:

    label      actual calendar gap (median)
    h=7        9 days
    h=30       42 days
    h=90       132 days

So the "90-day forecast" resolved 132 days out, 47% further than advertised. That flows
straight into money: ``opt.ceiling._horizon_weight`` slices a contract term into
[0,19), [19,61), [61,inf) *calendar-day* bands and weights the h=7/30/90 fans over them,
so a 90-day time-charter was being priced against a forecast for month four.

What this module does instead
-----------------------------
For a row at date t and horizon h, the target is the value at the first trading day on
or after ``t + h calendar days``. Weekends and holidays mean an exact match rarely
exists, so the resolution slips forward -- and that slip is recorded per row and capped,
rather than being absorbed silently. A row whose nearest observation sits too far past
the intended date is dropped: it is not a horizon-h observation and training on it
teaches the model the wrong horizon.

Two targets are produced per horizon:

``y_step_h{h}``
    Log level at the resolved date. What "the rate in h days" means.
``y_mean_h{h}``
    Log of the mean level over the trading days in ``(t, t + h]``. What a charterer
    paying spot every day over the period would actually average, which is the right
    comparison for a time-charter decision.
"""
from __future__ import annotations

import logging
from typing import Final

import numpy as np
import polars as pl

LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

#: Minimum slip allowance in days, to absorb an ordinary weekend or public holiday.
MIN_SLIP_DAYS: Final[int] = 4

#: Slip allowance as a share of the horizon. A 4-day slip is negligible at h=90 but
#: doubles a 7-day horizon, so the tolerance scales with what is being measured.
SLIP_FRACTION: Final[float] = 0.15


def slip_tolerance_days(horizon_days: int) -> int:
    """How far past ``t + h`` an observation may sit and still count as horizon h."""
    return max(MIN_SLIP_DAYS, int(SLIP_FRACTION * horizon_days))


def build_forward_targets(
    df: pl.DataFrame,
    horizons: tuple[int, ...],
    value_col: str = "target_value",
    log_col: str = "log_value",
    date_col: str = "date",
) -> pl.DataFrame:
    """Attach calendar-horizon targets to one class's sorted daily series.

    Parameters
    ----------
    df:
        One vessel class, sorted ascending by ``date_col``, with no null values.
    horizons:
        Calendar-day horizons, e.g. ``(7, 30, 90)``.

    Returns
    -------
    The input frame plus, for each horizon h, the columns ``y_step_h{h}``,
    ``y_mean_h{h}`` and ``y_slip_h{h}`` (days between the intended date and the
    observation actually used, for auditing).

    Notes
    -----
    Rows are not dropped here -- targets are null where they cannot be resolved, and
    the caller decides. That keeps this function free of split logic.
    """
    if df.is_empty():
        return df

    dates = df[date_col].to_numpy().astype("datetime64[D]")
    if not np.all(np.diff(dates) > np.timedelta64(0, "D")):
        raise ValueError(
            "build_forward_targets expects one class sorted strictly ascending by date."
        )

    values = df[value_col].to_numpy().astype(float)
    logs = df[log_col].to_numpy().astype(float)
    n = len(dates)

    # Prefix sums let the windowed mean be computed without a Python loop.
    prefix = np.concatenate([[0.0], np.cumsum(values)])

    # First index strictly after t. Used as the lower bound of the forward window so
    # the current day is excluded -- you cannot charter at a rate you have not seen.
    lo = np.searchsorted(dates, dates + np.timedelta64(1, "D"), side="left")

    new_cols: list[pl.Series] = []
    for h in horizons:
        intended = dates + np.timedelta64(h, "D")
        tol = slip_tolerance_days(h)

        # --- y_step: first observation on or after the intended date
        idx = np.searchsorted(dates, intended, side="left")
        in_range = idx < n
        safe_idx = np.clip(idx, 0, n - 1)
        slip = np.where(
            in_range,
            (dates[safe_idx] - intended).astype("timedelta64[D]").astype(float),
            np.nan,
        )
        usable = in_range & (slip <= tol)
        y_step = np.where(usable, logs[safe_idx], np.nan)

        # --- y_mean: mean level over the trading days in (t, t + h]
        hi = np.searchsorted(dates, intended, side="right")
        count = hi - lo
        # Require the window to actually reach the horizon, not stop short at the
        # end of the series, or the average silently covers a shorter period.
        complete = in_range & (count > 0)
        total = prefix[np.clip(hi, 0, n)] - prefix[np.clip(lo, 0, n)]
        with np.errstate(invalid="ignore", divide="ignore"):
            mean_level = np.where(complete, total / np.maximum(count, 1), np.nan)
            y_mean = np.where(
                complete & (mean_level > 0), np.log(np.maximum(mean_level, 1e-12)), np.nan
            )

        dropped = int(in_range.sum() - usable.sum())
        if dropped:
            LOGGER.debug(
                f"h={h}: dropped {dropped} row(s) whose nearest observation slipped "
                f"more than {tol} day(s) past the intended date"
            )

        # Emit null, not NaN. Polars treats them as different things: drop_nulls and
        # null_count ignore NaN, so a NaN target survives every guard downstream and
        # reaches the model as a number. ml.baselines.load_split currently patches this
        # up after the fact with fill_nan(None); producing nulls here removes the need.
        new_cols.extend(
            [
                pl.Series(f"y_step_h{h}", y_step).fill_nan(None),
                pl.Series(f"y_mean_h{h}", y_mean).fill_nan(None),
                pl.Series(f"y_slip_h{h}", np.where(usable, slip, np.nan)).fill_nan(None),
            ]
        )

    return df.with_columns(new_cols)


def required_embargo_days(
    max_lag_rows: int,
    horizons: tuple[int, ...],
    trading_days_per_week: float = 5.0,
) -> int:
    """Calendar-day gap needed between splits so no information crosses the boundary.

    The last training row reads features back ``max_lag_rows`` trading days and its
    target resolves ``max(horizons)`` calendar days forward. The first validation row
    reads features back over the same lag window. For those two windows not to touch::

        embargo > lag_span_in_calendar_days + max_horizon

    The original code asserted ``embargo_days >= max(lags) + max(horizons)``, adding a
    count of *rows* to a count of *days*: 63 + 90 = 153, satisfied by an embargo of
    160. Converted properly, 63 trading days span about 91 calendar days and the true
    horizon under the old row-stepped targets was about 132 days, so roughly 223 days
    were needed and the splits leaked.
    """
    lag_calendar_days = int(np.ceil(max_lag_rows * 7.0 / trading_days_per_week))
    return lag_calendar_days + max(horizons)
