# Model development guide

How to add a new forecasting model to this repo (e.g., an LSTM experiment),
run it under identical conditions, and compare it fairly against what exists.

## Current pipeline commands

```
uv sync                  # install deps into .venv
uv run build-master      # data_raw/* -> data/master_long.parquet (tidy long table)
uv run baselines         # trains rw/ar1/lgbm on sample splits, writes data/baseline_metrics.csv
```

Data flow:

```
data_raw/  ->  master_long.parquet  ->  samples_{train,valid,test}.parquet  ->  models  ->  baseline_metrics.csv
```

Sample splits are FIXED. Never regenerate them for an experiment.
train <= 2022-07-25 | valid 2023-01-03..2024-07-25 | test 2025-01-02..2026-04-14
(~5 month embargo gaps, larger than any lookback + horizon we use.)

## The samples table (input contract)

One row = (target_class, date t). 40 columns:

- keys: `date` (Date), `target_class` (str: Capesize/Panamax/Supramax/Handysize)
- target level: `log_value` = ln(TCE $/day) at t
- targets: `y_step_h7`, `y_step_h30`, `y_step_h90` = log levels at t+h (calendar-day horizons,
  resolved to next available trading day)
- features: lags (`lag_1..lag_63`), rolling stats (30d/90d mean/std/zscore),
  returns (`ret_1/7/30`), calendar (`dayofweek, month, weekofyear,
  indian_fiscal_quarter, is_monsoon, is_cyclone_season, is_cny_window`),
  PortWatch congestion (`congestion_origin_calls`, `congestion_dest_calls`,
  `congestion_origin_exports_log1p`, `congestion_dest_imports_log1p`),
  cross-class (`cross_BDI_log_level`, `cross_*_INDEX_ret_1`)

Known quirks: NaNs exist in train (normalized to null by `load_split`);
some cross-class ret columns are all-null in train (sparse source history).
Nulls are fine for LightGBM; neural nets need explicit handling (see below).

## Prediction contract

A model produces, per horizon h in {7, 30, 90}, a frame with exactly:

```
date | target_class | h | p_0.1 | p_0.5 | p_0.9     <- LOG-LEVEL space
```

i.e., predictions of ln(TCE) at t+h. If your model predicts log-returns,
add `log_value` back before evaluation (see `predict_lgbm`).

Anything that emits those frames plugs into the existing evaluator:
`ml.baselines.evaluate(pred, source_df, split_name, model_name)` computes
pinball@10/50/90, median MAE, and directional hit-rate, pooled and per class.

## Adding a model: the rules

1. Train on `train`, early-stop/tune on an INNER holdout carved from the tail
   of train (~15%). Touch `valid` only for reporting until final runs;
   touch `test` once, at the end, ever.
2. No feature may use information published after row-date t. The lag columns
   already respect this; anything you engineer must too.
3. Normalize/scale using TRAIN statistics only, applied unchanged to
   valid/test (per-class stats where relevant, since classes live on
   different TCE scales).
4. Report against the reference table below. A model "wins" only if it beats
   ALL of rw/ar1/lgbm on pinball_0.5 for its horizon, pooled AND per class.
   Directional hit-rate is reported but secondary.
5. Keep seeds fixed and logged. Determinism > vibes.

Reference numbers to beat (valid, pooled, pinball_0.5 / dir_hit):

| h  | rw           | ar1          | lgbm         | lgbm_tuned   | xgb          | lstm         |
|----|--------------|--------------|--------------|--------------|--------------|--------------|
| 7  | 0.048 / 51%  | 0.049 / 50%  | 0.041 / 72%  | 0.039 / 73%  | 0.039 / 74%  | 0.041 / 59%  |
| 30 | 0.119 / 48%  | 0.116 / 55%  | 0.117 / 55%  | 0.117 / 52%  | 0.097 / 65%  | 0.110 / 50%  |
| 90 | 0.145 / 42%  | 0.136 / 64%  | 0.139 / 58%  | 0.143 / 58%  | 0.135 / 65%  | 0.209 / 66%  |

**These numbers superseded the previous table on 2026-08-24.** Targets were being built
with `shift(-h)`, which steps h *trading rows*, so the labelled horizons were really
medians of 9 / 42 / 132 calendar days. They are now true calendar-day horizons
(`ml.targets.build_forward_targets`). Scores improved partly because a genuine 90-day
forecast is an easier problem than the 132-day one the old labels encoded -- do not read
the improvement as a modelling gain.

Two caveats when reading the table:

- **lstm is not comparable.** It predicts only 889 of 1413 valid rows, because window
  construction drops rows near gaps. Its column scores a different, easier subset.
  Compare it only against other models restricted to the same rows.
- **Splits moved.** The embargo is now 200 days, up from 160. The old assert added lag
  *rows* to horizon *days* (63 + 90 = 153) and passed; converted properly the requirement
  is ~179 days, so the previous splits leaked information across the boundary.
- **Directional hit-rate is not an accuracy claim on its own -- read it against a
  baseline, always (F-19).** It rewards a model for guessing the market's *prevailing
  direction*, which in a sustained trend is not a hard problem. On the real, frozen
  **test** split (2025-01 to 2026-05, not the valid split the table above reports), the
  market rose in 55.3% / 62.8% / 73.5% of the real 7/30/90-day windows -- i.e. an
  "always predict up" model with zero intelligence scores roughly that well by
  construction. xgb's real pooled test-set dir-hit is 68.2% / 63.0% / 89.7% at
  those same three horizons: a genuine ~16-point edge over always-up at 90 days, but
  reporting the 89.7% alone -- in a demo, a deck, anywhere -- reads as "89.7%
  accurate," which it is not. Always cite it as *"+16 points over an always-predict-up
  baseline in a rising market,"* or lead with pinball loss instead, which does not have
  this pathology.

## Wiring a new model in

Cleanest pattern: one module per family, same shape as `baselines.py`.

1. `uv add <dep>` (never pip).
2. Create `src/sih/model_lstm.py` exposing
   `predict(train, eval_frames, h) -> dict[str, pl.DataFrame]` matching the
   contract above.
3. In `baselines.py::main`, append it to `candidates`:

```python
from ml.model_lstm import predict as predict_lstm
candidates["lstm"] = predict_lstm(splits["train"], {"valid": ..., "test": ...}, h)
```

Everything downstream (metrics csv, logging) just works.

## Worked example: LSTM experiment

Honest expectation setting first: ~2.4k usable train rows across 4 classes is
far below what recurrent nets want. Treat this as (a) a learning exercise,
(b) a potential ensemble member, not as a expected winner over LightGBM.

### Framing

- Input: sliding window of length L=60 trading days of features per class.
- Output: quantile predictions of the LOG-RETURN at horizon h
  (r_{t+h} = y_step_h{h} - log_value), three heads (q10/q50/q90).
- Loss: sum of pinball losses across the three quantiles.
- One shared model across classes: feed target_class as a small learned
  embedding (dim 2) concatenated to the window summary.

### Preprocessing (train-stats rule applies)

```python
FEATURE_COLS: list[str] = [...]  # numeric feature columns you select
# z-score using train mean/std PER FEATURE (global, not per-class, since the
# features are already scale-free-ish: lags are logs, rets/zscores are ratios)
mu = train.select([pl.col(c).mean() for c in FEATURE_COLS])
sd = train.select([pl.col(c).std() for c in FEATURE_COLS])
# fill nulls AFTER computing stats: null -> 0 post-normalization, plus a
# per-column null-indicator channel if the column has many gaps
```

### Windowing

For each split, sort by (target_class, date); emit windows only within a
class's contiguous runs so no window straddles a data gap (check
`date.diff() > 5 days` to cut runs). Label = return at index t+h if it exists
in the SAME run, else drop the window. This mirrors how y_step_h* was built.

### Skeleton (PyTorch, CPU is fine at this size)

```python
class QuantileLSTM(torch.nn.Module):
    def __init__(self, n_features: int, hidden: int = 64) -> None:
        super().__init__()
        self.lstm = torch.nn.LSTM(n_features - 1 + 2, hidden, num_layers=2, batch_first=True)
        self.heads = torch.nn.Linear(hidden, 3)

    def forward(self, x_seq: torch.Tensor, cls_emb: torch.Tensor) -> torch.Tensor:
        out, _ = self.lstm(x_seq)
        return self.heads(out[:, -1, :])  # q10, q50, q90 in return space
```

Training loop essentials:

- pinball loss: `max(q * (y - p), (q - 1) * (y - p))` summed over heads
- Adam lr 1e-3, batch 64, max ~100 epochs, early stop patience 8 on inner
  holdout loss
- clip grad norm 1.0; seed everything (torch/numpy/random) before init
- at inference: `log_value + head_q` gives the contracted log-level frame

### Pitfalls specific to this dataset

- Sparse classes: Handysize has ~116 rows total; without pooling the net will
  memorize noise. Pooling + class embedding is mandatory, not optional.
  - Don't worry too much about this though. I will be adding more data, eventually.
- Gap-straddling windows silently create lookahead; enforce contiguous runs.
- Do not shuffle across the train/inner-holdout boundary when carving.
- If validation loss diverges from train loss immediately, shrink hidden size
  before adding regularization.

## Reporting

Add your model's row to the reference table above (update this doc in the
same commit), including test-set numbers once unlocked. Note anything weird
(per-class failures, band pathologies like P10 > P50) in the PR description.

Quantile sanity check worth running on any model output:

```
share(y < p_0.1) ~= 0.10, share(y < p_0.9) ~= 0.90
```

If coverage is badly off, the bands are miscalibrated even if pinball looks fine.
