from __future__ import annotations

import logging
import random
from typing import Final

import numpy as np
import polars as pl
import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

LOGGER: Final[logging.Logger] = logging.getLogger(__name__)

EXCLUDE: Final[frozenset[str]] = frozenset(
    {"date", "target_value", "log_value", "y_step_h7", "y_step_h30", "y_step_h90",
     "y_mean_h7", "y_mean_h30", "y_mean_h90", "target_class"}
)

CLASSES: Final[tuple[str, ...]] = ("Capesize", "Panamax", "Supramax", "Handysize")
CLASS_CODES: Final[dict[str, int]] = {c: i for i, c in enumerate(CLASSES)}


def set_seed(seed: int = 42) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


class QuantileLSTM(nn.Module):
    def __init__(self, n_features: int, hidden: int = 32) -> None:
        super().__init__()
        self.cls_emb = nn.Embedding(len(CLASSES), 2)
        self.lstm = nn.LSTM(n_features, hidden, num_layers=2, batch_first=True)
        self.heads = nn.Linear(hidden + 2, 3)

    def forward(self, x_seq: torch.Tensor, cls_idx: torch.Tensor) -> torch.Tensor:
        emb = self.cls_emb(cls_idx)  # (batch, 2)
        out, _ = self.lstm(x_seq)
        summary = out[:, -1, :]      # (batch, hidden)
        summary_emb = torch.cat([summary, emb], dim=-1)
        return self.heads(summary_emb)


def pinball_loss(pred: torch.Tensor, target: torch.Tensor) -> torch.Tensor:
    target = target.unsqueeze(1)
    err = target - pred
    q = torch.tensor([0.1, 0.5, 0.9], device=pred.device).unsqueeze(0)
    loss = torch.max(q * err, (q - 1.0) * err)
    return loss.sum(dim=-1).mean()


def create_windows(df: pl.DataFrame, h: int, feature_cols: list[str]) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, list[tuple[object, str]]]:
    df = df.sort(["target_class", "date"])
    
    X_list, y_list, cls_list, meta_list = [], [], [], []
    
    for cls_name, grp in df.group_by("target_class", maintain_order=True):
        grp = grp.sort("date")
        diffs = grp["date"].diff().dt.total_days()
        run_ids = (diffs > 5).fill_null(False).cum_sum()
        
        grp = grp.with_columns(pl.Series(name="run_id", values=run_ids))
        
        for _, run_grp in grp.group_by("run_id", maintain_order=True):
            if run_grp.height < 60:
                continue
            
            x_arr = run_grp.select(feature_cols).to_numpy()
            
            if f"y_step_h{h}" in run_grp.columns:
                y_arr = (run_grp[f"y_step_h{h}"] - run_grp["log_value"]).to_numpy()
            else:
                y_arr = np.full(run_grp.height, np.nan)
            
            dates_arr = run_grp["date"].to_list()
            
            for i in range(60, run_grp.height + 1):
                window_x = x_arr[i - 60 : i]
                target = y_arr[i - 1]
                
                X_list.append(window_x)
                y_list.append(target if not np.isnan(target) else np.nan)
                cls_list.append(CLASS_CODES[str(cls_name[0]) if isinstance(cls_name, tuple) else str(cls_name)])
                meta_list.append((dates_arr[i - 1], str(cls_name[0]) if isinstance(cls_name, tuple) else str(cls_name)))

    if not X_list:
        return torch.empty(0), torch.empty(0), torch.empty(0), []
        
    X_tensor = torch.tensor(np.stack(X_list), dtype=torch.float32)
    y_tensor = torch.tensor(np.array(y_list), dtype=torch.float32)
    cls_tensor = torch.tensor(np.array(cls_list), dtype=torch.long)
    return X_tensor, cls_tensor, y_tensor, meta_list


def predict(
    train: pl.DataFrame, eval_frames: dict[str, pl.DataFrame], h: int
) -> dict[str, pl.DataFrame]:
    set_seed(42)

    # 1. Select features and compute stats
    feature_cols = [c for c in train.columns if c not in EXCLUDE]
    
    mu_expr = [pl.col(c).mean().alias(c) for c in feature_cols]
    sd_expr = [pl.col(c).std().alias(c) for c in feature_cols]
    
    mu_df = train.select(mu_expr)
    sd_df = train.select(sd_expr)
    
    mu = {c: mu_df[c][0] for c in feature_cols}
    sd = {c: sd_df[c][0] for c in feature_cols}
    
    def preprocess(df: pl.DataFrame) -> pl.DataFrame:
        exprs = []
        for c in feature_cols:
            m, s = mu[c], sd[c]
            if s is None or s == 0 or np.isnan(s):
                s = 1.0
            if m is None or np.isnan(m):
                m = 0.0
            exprs.append(
                ((pl.col(c) - m) / s).fill_null(0.0).alias(c)
            )
        return df.with_columns(exprs)
    
    train_norm = preprocess(train)
    
    X_all, c_all, y_all, meta_all = create_windows(train_norm, h, feature_cols)
    
    if len(X_all) == 0:
        raise ValueError("No valid training windows found.")
        
    dates = np.array([m[0].toordinal() for m in meta_all])
    sort_idx = np.argsort(dates)
    
    X_all = X_all[sort_idx]
    c_all = c_all[sort_idx]
    y_all = y_all[sort_idx]
    meta_all = [meta_all[i] for i in sort_idx]
    
    valid_mask = ~torch.isnan(y_all)
    X_all = X_all[valid_mask]
    c_all = c_all[valid_mask]
    y_all = y_all[valid_mask]
    
    core_n = max(int(len(X_all) * 0.85), len(X_all) - 60)
    
    X_tr = X_all[:core_n]
    c_tr = c_all[:core_n]
    y_tr = y_all[:core_n]
    
    X_es = X_all[core_n:]
    c_es = c_all[core_n:]
    y_es = y_all[core_n:]

    model = QuantileLSTM(n_features=len(feature_cols), hidden=32)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    
    tr_ds = TensorDataset(X_tr, c_tr, y_tr)
    tr_dl = DataLoader(tr_ds, batch_size=64, shuffle=True)
    
    best_loss = float("inf")
    patience = 8
    patience_counter = 0
    best_state = None
    
    for epoch in range(100):
        model.train()
        for bx, bc, by in tr_dl:
            optimizer.zero_grad()
            pred = model(bx, bc)
            loss = pinball_loss(pred, by)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            
        model.eval()
        with torch.no_grad():
            if len(X_es) > 0:
                pred_es = model(X_es, c_es)
                val_loss = pinball_loss(pred_es, y_es).item()
            else:
                val_loss = 0.0
                
        if val_loss < best_loss:
            best_loss = val_loss
            patience_counter = 0
            best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
        else:
            patience_counter += 1
            if patience_counter >= patience:
                break
                
    if best_state is not None:
         model.load_state_dict(best_state)
    
    model.eval()
    
    out = {}
    for split_name, df in eval_frames.items():
        df_norm = preprocess(df)
        X_eval, c_eval, _, meta_eval = create_windows(df_norm, h, feature_cols)
        
        if len(X_eval) > 0:
            with torch.no_grad():
                preds = model(X_eval, c_eval).numpy()
        else:
            preds = np.empty((0, 3))
            
        dates_list = [m[0] for m in meta_eval]
        classes_list = [m[1] for m in meta_eval]
        
        pred_df = pl.DataFrame({
            "date": dates_list,
            "target_class": classes_list,
            "p_0.1_ret": preds[:, 0],
            "p_0.5_ret": preds[:, 1],
            "p_0.9_ret": preds[:, 2],
        })
        
        df_join = df.select(["date", "target_class", "log_value"])
        res = df_join.join(pred_df, on=["date", "target_class"], how="left")
        
        res = res.select([
            pl.col("date"),
            pl.col("target_class"),
            pl.lit(h).cast(pl.Int64).alias("h"),
            (pl.col("log_value") + pl.col("p_0.1_ret")).alias("p_0.1"),
            (pl.col("log_value") + pl.col("p_0.5_ret")).alias("p_0.5"),
            (pl.col("log_value") + pl.col("p_0.9_ret")).alias("p_0.9"),
        ])
        
        res = res.drop_nulls(["p_0.5"])
        out[split_name] = res
        
    return out
