"""Download S&P 500 and US Dollar Index daily data into raw_data/."""
from __future__ import annotations

import pathlib
import polars as pl
import yfinance as yf


def download_and_save() -> None:
    print("Downloading S&P 500 data...")
    sp500_df = yf.download("^GSPC", start="2005-01-01")

    # yf.download returns a pandas DataFrame with MultiIndex columns in recent versions
    sp500_pandas = sp500_df.reset_index()

    if hasattr(sp500_pandas.columns, "levels"):
        sp500_pandas.columns = [
            "_".join(str(c) for c in col).strip("_") if isinstance(col, tuple) else str(col)
            for col in sp500_pandas.columns.values
        ]

    sp500_pl = pl.from_pandas(sp500_pandas)

    print("Downloading DXY data...")
    dxy_df = yf.download("DX-Y.NYB", start="2005-01-01")
    dxy_pandas = dxy_df.reset_index()

    if hasattr(dxy_pandas.columns, "levels"):
        dxy_pandas.columns = [
            "_".join(str(c) for c in col).strip("_") if isinstance(col, tuple) else str(col)
            for col in dxy_pandas.columns.values
        ]

    dxy_pl = pl.from_pandas(dxy_pandas)

    repo_root = pathlib.Path(__file__).resolve().parents[2]
    raw_data_dir = repo_root / "raw_data"
    raw_data_dir.mkdir(parents=True, exist_ok=True)

    sp500_path = raw_data_dir / "sp500_daily.parquet"
    dxy_path = raw_data_dir / "dxy_daily.parquet"

    sp500_pl.write_parquet(sp500_path)
    print(f"Saved S&P 500 to {sp500_path}")

    dxy_pl.write_parquet(dxy_path)
    print(f"Saved DXY to {dxy_path}")


if __name__ == "__main__":
    download_and_save()
