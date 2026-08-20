"""
Persistence for the NEW_EP efficacy tracker — one row per original NEW_EP
event (generation 1 only; Retro New EP does not start a fresh study, see
config.py), followed through classification and, eventually, matured
excess returns at each horizon.

The horizon columns (RETURN_N / NIFTY_RETURN_N / EXCESS_RETURN_N) are
derived from config.EFFICACY_RETURN_HORIZONS at CALL time, not fixed at
import time — deliberately, so the schema always matches whatever horizons
are actually configured, including in tests that shrink them.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from src import config

BASE_COLUMNS = ["SYMBOL", "NEW_EP_DATE", "NEW_EP_CLOSE", "BUCKET", "ANCHOR_DATE", "ANCHOR_CLOSE"]


def _horizon_columns() -> list[str]:
    """Per horizon: the stock's own return (once), then per configured
    benchmark: that benchmark's own return, and the stock's excess return
    against it."""
    cols = []
    for h in config.EFFICACY_RETURN_HORIZONS:
        cols.append(f"RETURN_{h}")
        for short_key in config.BENCHMARK_INDICES.keys():
            cols.append(f"{short_key}_RETURN_{h}")
            cols.append(f"EXCESS_RETURN_{short_key}_{h}")
    return cols


def tracker_columns() -> list[str]:
    return BASE_COLUMNS + _horizon_columns()


def load_tracker() -> pd.DataFrame:
    if not config.EFFICACY_TRACKER_PATH.exists():
        return pd.DataFrame(columns=tracker_columns())
    df = pd.read_parquet(config.EFFICACY_TRACKER_PATH)
    for col in tracker_columns():
        if col not in df.columns:
            df[col] = None
    return df


def save_tracker(df: pd.DataFrame) -> None:
    cols = tracker_columns()
    df = df[cols].copy() if not df.empty else pd.DataFrame(columns=cols)
    df.to_parquet(config.EFFICACY_TRACKER_PATH, index=False)


def register_new_events(daily_output: pd.DataFrame, as_of: date) -> None:
    """Adds a fresh tracker row for every GENERATION==1 NEW_EP that fired today."""
    if daily_output.empty:
        return
    new_rows = daily_output[
        (daily_output["LABEL"] == config.STATUS_NEW) & (daily_output["GENERATION"] == 1)
    ]
    if new_rows.empty:
        return

    tracker = load_tracker()
    base = {
        "SYMBOL": new_rows["SYMBOL"].values,
        "NEW_EP_DATE": pd.Timestamp(as_of),
        "NEW_EP_CLOSE": new_rows["CLOSE"].values,
        "BUCKET": None, "ANCHOR_DATE": pd.NaT, "ANCHOR_CLOSE": None,
    }
    for col in _horizon_columns():
        base[col] = None
    additions = pd.DataFrame(base)

    combined = pd.concat([tracker, additions], ignore_index=True)
    save_tracker(combined)
