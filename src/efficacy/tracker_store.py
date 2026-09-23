"""
Persistence for the NEW_EP efficacy tracker — one row per original NEW_EP
event (generation 1 only), now tracking classification under MULTIPLE
observation windows on the SAME row (confirmed design: a 10-day window and
a 20-day window, same buckets, same priority rules, just more patience
before locking in — not two separate disconnected tracker tables).

Column naming: every window-specific field is prefixed "{WINDOW_KEY}__",
e.g. "10D__BUCKET", "20D__ANCHOR_CLOSE", "10D__EXCESS_RETURN_NIFTY500_10".
Both windows share the same NEW_EP_DATE/NEW_EP_CLOSE (the episode itself
is one thing; only how patiently it gets classified differs).

MIGRATION: this replaces an earlier single-window schema (plain "BUCKET",
"ANCHOR_DATE", "RETURN_10", etc. with no window prefix) that real
production data has already accumulated under. That old schema WAS
effectively the 10-day window (the only one that existed) — load_tracker()
detects it and renames those columns into "10D__..." on load, so existing
classified events and matured returns carry forward rather than being
silently reset. The 20D__ columns start fresh from that point, same as any
brand-new column would.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from src import config

BASE_COLUMNS = ["SYMBOL", "NEW_EP_DATE", "NEW_EP_CLOSE"]

# Old (pre-dual-window) column names -> what they migrate to.
_LEGACY_RENAME_MAP = {
    "BUCKET": "10D__BUCKET",
    "ANCHOR_DATE": "10D__ANCHOR_DATE",
    "ANCHOR_CLOSE": "10D__ANCHOR_CLOSE",
}


def _window_horizon_columns(window_key: str) -> list[str]:
    cols = []
    for h in config.EFFICACY_RETURN_HORIZONS:
        cols.append(f"{window_key}__RETURN_{h}")
        for short_key in config.BENCHMARK_INDICES.keys():
            cols.append(f"{window_key}__{short_key}_RETURN_{h}")
            cols.append(f"{window_key}__EXCESS_RETURN_{short_key}_{h}")
    return cols


def _legacy_horizon_rename_map() -> dict[str, str]:
    """Old un-prefixed horizon column names -> their 10D__-prefixed equivalents."""
    mapping = {}
    for h in config.EFFICACY_RETURN_HORIZONS:
        mapping[f"RETURN_{h}"] = f"10D__RETURN_{h}"
        for short_key in config.BENCHMARK_INDICES.keys():
            mapping[f"{short_key}_RETURN_{h}"] = f"10D__{short_key}_RETURN_{h}"
            mapping[f"EXCESS_RETURN_{short_key}_{h}"] = f"10D__EXCESS_RETURN_{short_key}_{h}"
    return mapping


def window_columns(window_key: str) -> list[str]:
    return [f"{window_key}__BUCKET", f"{window_key}__ANCHOR_DATE", f"{window_key}__ANCHOR_CLOSE"] \
        + _window_horizon_columns(window_key)


def tracker_columns() -> list[str]:
    cols = list(BASE_COLUMNS)
    for window_key in config.EFFICACY_CLASSIFICATION_WINDOWS.keys():
        cols += window_columns(window_key)
    return cols


def load_tracker() -> pd.DataFrame:
    if not config.EFFICACY_TRACKER_PATH.exists():
        return pd.DataFrame(columns=tracker_columns())

    df = pd.read_parquet(config.EFFICACY_TRACKER_PATH)

    # --- Migration: old single-window schema -> 10D__-prefixed ---
    if "BUCKET" in df.columns and "10D__BUCKET" not in df.columns:
        rename_map = dict(_LEGACY_RENAME_MAP)
        rename_map.update(_legacy_horizon_rename_map())
        rename_map = {old: new for old, new in rename_map.items() if old in df.columns}
        df = df.rename(columns=rename_map)

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
    }
    for window_key in config.EFFICACY_CLASSIFICATION_WINDOWS.keys():
        base[f"{window_key}__BUCKET"] = None
        base[f"{window_key}__ANCHOR_DATE"] = pd.NaT
        base[f"{window_key}__ANCHOR_CLOSE"] = None
        for col in _window_horizon_columns(window_key):
            base[col] = None
    additions = pd.DataFrame(base)

    combined = pd.concat([tracker, additions], ignore_index=True)
    save_tracker(combined)
