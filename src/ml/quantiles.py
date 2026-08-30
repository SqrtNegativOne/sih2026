"""Repairing crossed quantile forecasts.

Why this exists
----------------
A quantile model built as three independent regressions -- one booster per
quantile level, which is exactly how ``ml.baselines.predict_lgbm`` and
``opt.backtest._build_fan`` consume LightGBM output -- has no constraint tying
the three outputs together. Nothing stops it from predicting p10 > p50 on a
given row, and it does: measured on the frozen test split with the current
models, LightGBM's h=90 forecasts cross on 188 of 818 rows (23%). h=7 and h=30
never cross for either LightGBM or XGBoost on this data, so the defect is real
but horizon- and model-dependent -- exactly the kind of thing that looks fine
in a demo and breaks the moment someone swaps in a new model.

``opt.backtest._build_fan`` used to treat a crossed row as "no forecast" and
silently drop it. That is survivorship bias with a direction: quantile
crossing correlates with model uncertainty (a confused model is more likely
to produce an incoherent spread), so the dropped rows were systematically the
ones the model was least sure about. The reported decision-value metrics were
computed on an easier subset than the real one, and the summary quietly said
nothing about it.

The fix
-------
Sort the three quantile values ascending. This is quantile rearrangement
(Chernozhukov, Fernandez-Val & Galichon, 2009): every value keeps its value,
only its assigned quantile level changes, so a p10 that was actually the
largest of the three becomes the new p90 instead of being discarded. It
requires no retraining, and for exactly three ordered points it coincides
with full isotonic regression -- there is no better monotone fit to choose
between.

A row is only unusable when there genuinely is no prediction to repair, not
when a prediction disagrees with itself.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final

LOGGER: Final[logging.Logger] = logging.getLogger(__name__)


@dataclass(frozen=True)
class QuantileTriple:
    """A monotone (p10, p50, p90), plus whether repair was needed to get there."""

    p10: float
    p50: float
    p90: float
    was_crossed: bool


def repair_crossed_quantiles(p10: float, p50: float, p90: float) -> QuantileTriple:
    """Return a monotone (p10, p50, p90) triple, repairing any crossing by rank-sort.

    Already-monotone input passes through unchanged (``was_crossed=False``); the
    function is idempotent.
    """
    values = sorted((p10, p50, p90))
    crossed = values != [p10, p50, p90]
    return QuantileTriple(p10=values[0], p50=values[1], p90=values[2], was_crossed=crossed)


def crossing_rate(p10: object, p50: object, p90: object) -> float:
    """Share of rows where p10 <= p50 <= p90 fails to hold, for numpy/polars arrays.

    Diagnostic only -- use this to check a new model before trusting it, the way
    the docstring above measured LightGBM's h=90 output.
    """
    import numpy as np

    a = np.asarray(p10, dtype=float)
    b = np.asarray(p50, dtype=float)
    c = np.asarray(p90, dtype=float)
    crossed = (a > b) | (b > c) | (a > c)
    return float(crossed.mean()) if crossed.size else 0.0
