"""Baltic index point <-> USD/day time-charter-equivalent conversion.

Why this module exists
----------------------
The forecasting models are trained on Baltic *index points* (BC_INDEX, BPI_INDEX,
BSI_INDEX, BHSI_INDEX). Every consumer downstream -- the ceiling rule, the voyage
economics, the savings estimate -- reasons in *USD/day*. Those are different units
by a factor of roughly 8x to 18x depending on vessel class, so treating one as the
other produces confidently wrong money.

The Baltic publishes both the index and the per-class TC average, and the two are
tied by a deterministic published relation::

    TC_usd_per_day = slope * index_points + intercept

Empirically (see tests/ml/test_units.py) that relation is recoverable from the
overlapping history to within about $5/day -- i.e. it is exact up to rounding.

The catch: **the relation changes without notice.** Over the 20 months of overlap
currently in master_long.parquet the Capesize relation moved three times
(slope 8.2931 -> 9.0696 in January 2026, corroborated by a simultaneous break in the
BDI index identity, plus a transient additive offset that persisted until June 2026).
A hardcoded constant would therefore be a latent bug with a fuse on it.

Design consequences
-------------------
1.  The map is **fitted from data, as of a date**, over a trailing window -- never
    hardcoded.
2.  The intercept is **dropped unless the data insists on it**. For Panamax,
    Supramax and Handysize the true intercept is zero, and fitting a spurious one
    makes out-of-sample reconstruction *worse* (it fits noise). The test is
    practical, not statistical: with R^2 ~ 0.99999 a t-test calls a $1 intercept
    significant, which is useless. We instead ask whether forcing the intercept to
    zero keeps the worst residual inside tolerance.
3.  We **never extrapolate the map backwards** across the full training history.
    Overlap starts 2024-12 (Cape/Panamax) and 2025-11 (Supra/Handy); the training
    data starts 2012. Projecting a 2025-vintage map onto 2012 index points would
    silently invent USD/day history across at least one known methodology change.
    Instead the models keep training on log-index returns and conversion happens
    only at the last mile, anchored to today's observed TC average.
4.  Regime breaks are **detected and surfaced**, not smoothed over. See
    detect_regime_breaks; the output feeds the risk / early-warning engine.

The last-mile projection
------------------------
Given today's observed TC average and a forecast log-return on the index, the
correct reconstruction is::

    TC(t+h) = (TC(t) - intercept) * exp(r) + intercept

which collapses to the naive TC(t) * exp(r) exactly when the intercept is zero.
Ignoring a non-zero intercept amplifies the return: at the Capesize offset of
-$3,503/day on a ~$25,000/day rate, returns were overstated by about 16%.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final

import numpy as np
import polars as pl

LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

#: (vessel_class -> (index series_id, TC-average series_id)) as they appear in master_long.
CLASS_SERIES: Final[dict[str, tuple[str, str]]] = {
    "Capesize": ("BC_INDEX", "CAPESIZE_TCAVG"),
    "Panamax": ("BPI_INDEX", "PANAMAX_TCAVG"),
    "Supramax": ("BSI_INDEX", "SUPRAMAX_TCAVG"),
    "Handysize": ("BHSI_INDEX", "HANDYSIZE_TCAVG"),
}

#: Worst-case residual (USD/day) still considered "the intercept is really zero".
#: Rates run $6k-$50k/day, so $25 is ~0.1% -- well inside published rounding.
DEFAULT_INTERCEPT_TOL_USD: Final[float] = 25.0

#: Trailing calendar days used to fit the map. Long enough for a stable fit,
#: short enough that a regime change is picked up within about a quarter.
DEFAULT_WINDOW_DAYS: Final[int] = 120

#: Minimum overlapping observations required before a fit is trusted.
MIN_OBS: Final[int] = 10

#: Below this many in-regime observations the map is flagged as freshly rebased.
#: A 30-day forecast made this soon after a break may resolve against a relation
#: that did not exist when the forecast was formed.
FRESH_REGIME_OBS: Final[int] = 40

#: Neighbourhood size (observations, including the point itself) used for the
#: leave-one-out spike test. Wide enough to pin an affine fit, narrow enough that
#: the local relation is genuinely constant across it.
SPIKE_WINDOW: Final[int] = 11


class InsufficientOverlapError(RuntimeError):
    """Raised when there is not enough index/TC overlap to fit a conversion map."""


@dataclass(frozen=True)
class UnitMap:
    """A fitted, dated index -> USD/day conversion for one vessel class.

    Attributes
    ----------
    slope:
        USD/day per index point.
    intercept:
        USD/day offset. Exactly 0.0 when the scalar form fits within tolerance.
    max_residual_usd:
        Worst absolute reconstruction error over the fitting window. This is the
        honest accuracy claim for the map.
    intercept_dropped:
        True when a non-zero intercept was fitted but discarded as unnecessary.
    regime_start:
        First date still governed by this relation, as far back as the fitting
        window can see. Equal to ``fitted_from``.
    is_fresh_regime:
        True when the Baltic changed the relation recently enough that the fit rests
        on few observations. Conversions remain usable but carry elevated risk, and
        any forecast horizon reaching back across the break cannot be reconstructed
        by *any* map -- the rules changed mid-flight. Surface this rather than
        pretending to a precision we do not have.
    """

    vessel_class: str
    slope: float
    intercept: float
    r2: float
    max_residual_usd: float
    n_obs: int
    fitted_from: date
    fitted_to: date
    asof: date
    intercept_dropped: bool
    regime_start: date
    is_fresh_regime: bool

    def to_usd_per_day(self, index_points: float) -> float:
        """Convert index points to USD/day."""
        return self.slope * index_points + self.intercept

    def to_index(self, usd_per_day: float) -> float:
        """Convert USD/day back to index points."""
        return (usd_per_day - self.intercept) / self.slope

    def project_return(self, tc_now_usd_per_day: float, log_return: float) -> float:
        """Apply a forecast log-return *on the index* to an observed USD/day level.

        This is the last-mile conversion: the model forecasts index returns, we hold
        an observed TC average, and we want the implied future TC average. Collapses
        to tc_now * exp(r) when the intercept is zero.
        """
        grown = (tc_now_usd_per_day - self.intercept) * float(np.exp(log_return))
        return grown + self.intercept


@dataclass(frozen=True)
class RegimeSegment:
    """A maximal date range over which one affine map holds to within tolerance."""

    vessel_class: str
    start: date
    end: date
    n_obs: int
    slope: float
    intercept: float
    max_residual_usd: float


def _spike_mask(
    index_points: np.ndarray,
    tc_usd: np.ndarray,
    tol_usd: float = DEFAULT_INTERCEPT_TOL_USD,
) -> np.ndarray:
    """Flag single-observation outliers in the TC/index relation.

    The source series carry occasional bad days -- Supramax 2026-06-02 prints a ratio
    of 12.678 against 12.638 either side, Handysize 2026-07-07 prints 18.53 against
    18.00 with the TC average *rising* while the index fell. Left in, a single such
    point drags the fitted intercept off zero and makes the regime detector announce
    a Baltic methodology change that never happened.

    Each point is judged by a leave-one-out affine fit on its immediate neighbours:
    fit ``TC = slope*index + intercept`` on the surrounding window *excluding* the
    point, then ask whether the point sits on that line. A local *affine* fit is
    required rather than a local ratio -- inside an intercept regime the TC/index
    ratio legitimately drifts with the index level, so a ratio test manufactures
    false positives exactly where the Capesize offset lived.

    A verdict is only issued when the neighbourhood itself is well described by one
    line. Near a genuine rebasing the local fit straddles two relations and fits
    badly, so we abstain instead of mislabelling the break as a glitch.

    The threshold is expressed in USD/day so it matches the tolerance the regime
    detector uses. Judging on a relative ratio instead lets a point slip through as
    "small" (0.29% of the ratio) while still being a $60/day error, which then trips
    the detector and invents a regime.
    """
    n = len(tc_usd)
    mask = np.zeros(n, dtype=bool)
    half = SPIKE_WINDOW // 2
    if n < 2 * half + 1:
        return mask

    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        keep = np.arange(lo, hi) != i
        x = index_points[lo:hi][keep]
        y = tc_usd[lo:hi][keep]
        if len(x) < 4 or float(x.max() - x.min()) <= 0.0:
            continue
        slope, intercept, _r2, resid = _fit_affine(x, y)
        # Only trust the verdict when the neighbourhood is itself clean.
        if resid > 0.5 * tol_usd:
            continue
        if abs(tc_usd[i] - (slope * index_points[i] + intercept)) > tol_usd:
            mask[i] = True
    return mask


def _overlap(
    master: pl.DataFrame, vessel_class: str, drop_spikes: bool = True
) -> pl.DataFrame:
    """Inner-join the index and TC-average series for one class, sorted by date.

    Single-day ratio outliers are removed by default; pass ``drop_spikes=False`` to
    inspect them (see :func:`find_data_quality_outliers`).
    """
    if vessel_class not in CLASS_SERIES:
        raise KeyError(
            f"Unknown vessel class {vessel_class!r}; expected one of {sorted(CLASS_SERIES)}."
        )
    index_id, tc_id = CLASS_SERIES[vessel_class]
    idx = master.filter(pl.col("series_id") == index_id).select(
        "date", pl.col("value").alias("index_points")
    )
    tc = master.filter(pl.col("series_id") == tc_id).select(
        "date", pl.col("value").alias("tc_usd")
    )
    joined = idx.join(tc, on="date", how="inner").drop_nulls().sort("date")
    if joined.is_empty():
        return joined
    spikes = _spike_mask(
        joined["index_points"].to_numpy().astype(float),
        joined["tc_usd"].to_numpy().astype(float),
    )
    joined = joined.with_columns(pl.Series("is_spike", spikes))
    return joined.filter(~pl.col("is_spike")) if drop_spikes else joined


def find_data_quality_outliers(master: pl.DataFrame, vessel_class: str) -> pl.DataFrame:
    """Return the single-day ratio outliers excluded from fitting, for review."""
    full = _overlap(master, vessel_class, drop_spikes=False)
    if full.is_empty():
        return full
    return full.filter(pl.col("is_spike")).with_columns(
        (pl.col("tc_usd") / pl.col("index_points")).alias("ratio")
    )


def _fit_affine(x: np.ndarray, y: np.ndarray) -> tuple[float, float, float, float]:
    """Least-squares y = slope*x + intercept. Returns (slope, intercept, r2, max_resid).

    Computed in centered form rather than via ``np.polyfit``: index points run into
    the thousands over a narrow range, which makes the Vandermonde solve badly
    conditioned and emits RankWarning. The closed form on centered data is both
    stable and faster, and this runs inside a walk-forward loop.
    """
    x_mean = float(x.mean())
    y_mean = float(y.mean())
    dx = x - x_mean
    var = float((dx * dx).sum())
    slope = float((dx * (y - y_mean)).sum() / var) if var > 0 else 0.0
    intercept = y_mean - slope * x_mean
    resid = y - (slope * x + intercept)
    ss_tot = float(((y - y_mean) ** 2).sum())
    r2 = 1.0 - float((resid**2).sum()) / ss_tot if ss_tot > 0 else 1.0
    return slope, intercept, r2, float(np.abs(resid).max())


def _fit_scalar(x: np.ndarray, y: np.ndarray) -> tuple[float, float, float]:
    """Least-squares through the origin y = slope*x. Returns (slope, r2, max_resid)."""
    slope = float((x * y).sum() / (x * x).sum())
    resid = y - slope * x
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - float((resid**2).sum()) / ss_tot if ss_tot > 0 else 1.0
    return slope, r2, float(np.abs(resid).max())


def _current_regime_start(
    x: np.ndarray, y: np.ndarray, tol_usd: float, min_obs: int
) -> tuple[int, int]:
    """Locate the relation in force at the end of the window.

    Returns ``(detected_start, fit_start)``.

    ``detected_start`` is the first row still governed by the current relation, found
    by walking backwards while a single affine fit reconstructs every point in the
    candidate window to within ``tol_usd``. The moment the fit degrades we have
    crossed a methodology change and must stop -- fitting across a break produces a
    line through two regimes, which is worse than either. The separation is stark in
    practice: in-regime residuals run about $5/day while a window straddling a break
    blows out past $2,800/day, so this is not a knife-edge decision.

    ``fit_start`` may reach further back when the current regime holds too few
    observations to fit safely. That borrows data from before the break and is
    therefore *known to be biased*; callers detect it via ``fit_start <
    detected_start`` and must flag the resulting map as fresh rather than trust it.
    """
    n = len(x)
    detected = n - 1
    for start in range(n - 2, -1, -1):
        _, _, _, resid = _fit_affine(x[start:n], y[start:n])
        if resid > tol_usd:
            break
        detected = start
    # Two points always fit a line exactly; require real evidence before trusting it.
    fit_start = detected if n - detected >= min_obs else max(0, n - min_obs)
    return detected, fit_start


def fit_unit_map(
    master: pl.DataFrame,
    vessel_class: str,
    asof: date | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    intercept_tol_usd: float = DEFAULT_INTERCEPT_TOL_USD,
) -> UnitMap:
    """Fit the index -> USD/day map for one class using data available at ``asof``.

    Only observations on or before ``asof`` are used, so this is safe to call inside
    a backtest without leaking future information.

    The fitting window is the intersection of the trailing ``window_days`` and the
    *current regime* -- the run of recent observations still governed by one affine
    relation. Fitting a fixed calendar window instead would silently average across
    Baltic methodology changes; doing so across the June-2026 Capesize break yields
    slope 7.43 / intercept +6332 with $2,830/day of error, versus slope 9.069 /
    intercept 0 with $5/day once the break is respected.

    The intercept is kept only when forcing it to zero would push the worst residual
    beyond ``intercept_tol_usd``.

    Raises
    ------
    InsufficientOverlapError
        When fewer than MIN_OBS overlapping observations exist. This is expected for
        dates before the TC-average series begins, and callers must decide whether to
        widen the window or refuse to report USD/day.
    """
    df = _overlap(master, vessel_class)
    if asof is not None:
        df = df.filter(pl.col("date") <= asof)
    if df.is_empty():
        raise InsufficientOverlapError(
            f"No index/TC overlap for {vessel_class} at or before {asof}. "
            f"TC-average history is short; USD/day conversion is not available here."
        )

    last = df["date"].max()
    resolved_asof = asof if asof is not None else last
    if df.height < MIN_OBS:
        raise InsufficientOverlapError(
            f"Only {df.height} overlapping observation(s) for {vessel_class} at "
            f"{resolved_asof}; need >= {MIN_OBS} to fit a conversion map."
        )

    window = df.filter(pl.col("date") > last - timedelta(days=window_days))
    if window.height < MIN_OBS:
        window = df.tail(MIN_OBS)

    x_win = window["index_points"].to_numpy().astype(float)
    y_win = window["tc_usd"].to_numpy().astype(float)
    detected, start = _current_regime_start(x_win, y_win, intercept_tol_usd, MIN_OBS)
    regime_start: date = window["date"][detected]
    borrowed = start < detected
    if detected > 0:
        LOGGER.debug(
            f"{vessel_class}: regime break at {regime_start}; fitting on "
            f"{window.height - start} of {window.height} window rows"
            + (" (borrowing pre-break rows)" if borrowed else "")
        )
    window = window[start:]
    x = x_win[start:]
    y = y_win[start:]

    slope_a, intercept_a, r2_a, resid_a = _fit_affine(x, y)
    slope_s, r2_s, resid_s = _fit_scalar(x, y)

    if resid_s <= intercept_tol_usd:
        slope, intercept, r2, resid = slope_s, 0.0, r2_s, resid_s
        dropped = abs(intercept_a) > intercept_tol_usd
    else:
        slope, intercept, r2, resid = slope_a, intercept_a, r2_a, resid_a
        dropped = False

    return UnitMap(
        vessel_class=vessel_class,
        slope=slope,
        intercept=intercept,
        r2=r2,
        max_residual_usd=resid,
        n_obs=window.height,
        fitted_from=window["date"].min(),
        fitted_to=window["date"].max(),
        asof=resolved_asof,
        intercept_dropped=dropped,
        regime_start=regime_start,
        # Fresh when the relation changed recently, or when we had to borrow rows
        # from before the break to fit at all.
        is_fresh_regime=borrowed or (len(x) - (detected - start)) < FRESH_REGIME_OBS,
    )


def fit_all(
    master: pl.DataFrame,
    asof: date | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    intercept_tol_usd: float = DEFAULT_INTERCEPT_TOL_USD,
) -> dict[str, UnitMap]:
    """Fit conversion maps for every class that has usable overlap at ``asof``.

    Classes without enough overlap are omitted and logged rather than raising, so a
    partial result is still usable.
    """
    out: dict[str, UnitMap] = {}
    for cls in CLASS_SERIES:
        try:
            out[cls] = fit_unit_map(master, cls, asof, window_days, intercept_tol_usd)
        except InsufficientOverlapError as exc:
            LOGGER.warning(f"no USD/day map for {cls}: {exc}")
    return out


def detect_regime_breaks(
    master: pl.DataFrame,
    vessel_class: str,
    tol_usd: float = DEFAULT_INTERCEPT_TOL_USD,
) -> list[RegimeSegment]:
    """Split the overlap history into maximal ranges where one affine map holds.

    A new segment starts wherever extending the current one would push the worst
    residual past ``tol_usd``. More than one segment means the Baltic changed the
    relation -- which it does, silently. Feed the boundaries to the early-warning
    engine and re-fit anything that depends on the map.
    """
    df = _overlap(master, vessel_class)
    dates = df["date"].to_list()
    x_all = df["index_points"].to_numpy().astype(float)
    y_all = df["tc_usd"].to_numpy().astype(float)
    n = len(x_all)

    segments: list[RegimeSegment] = []
    start = 0

    def _emit(lo: int, hi: int) -> None:
        """Record the segment covering rows [lo, hi] inclusive."""
        if hi - lo + 1 < 3:
            return
        slope, intercept, _r2, resid = _fit_affine(x_all[lo : hi + 1], y_all[lo : hi + 1])
        segments.append(
            RegimeSegment(
                vessel_class=vessel_class,
                start=dates[lo],
                end=dates[hi],
                n_obs=hi - lo + 1,
                slope=slope,
                intercept=intercept,
                max_residual_usd=resid,
            )
        )

    for end in range(2, n):
        _, _, _, resid = _fit_affine(x_all[start : end + 1], y_all[start : end + 1])
        if resid > tol_usd:
            _emit(start, end - 1)
            start = end
    _emit(start, n - 1)
    return segments
