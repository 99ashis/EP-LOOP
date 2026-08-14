"""
Storage layer for the OPERATIONAL price window used by daily EP detection.

Design: ONE consolidated Parquet file (all symbols, trailing
`config.ROLLING_WINDOW_TRADING_DAYS` trading days) rather than one file per
symbol. Reasons:

  * EP detection is a cross-sectional scan (every symbol, every day) — bulk
    reads of one file beat 2,500 individual file reads.
  * The detector only ever needs a ~45-trading-day trailing baseline
    (20-day avg volume, 14-day ATR + margin). Storing unbounded per-symbol
    history to answer a 45-day question was solving a problem this loop
    doesn't have.
  * Bounded file size -> bounded git growth, if you commit this to the repo.

Permanent, unbounded history lives separately in data/raw_bhavcopy/ (one
immutable file per trading day, append-only — the actual long-term archive).
This module is deliberately NOT that archive; it's a fast operational cache
that forgets anything older than the window.
"""
from __future__ import annotations

import logging

import pandas as pd

from src import config

logger = logging.getLogger(__name__)

PRICE_COLUMNS = ["SYMBOL", "DATE", "OPEN", "HIGH", "LOW", "CLOSE", "PREV_CLOSE", "VOLUME", "TURNOVER"]


def load_rolling_window() -> pd.DataFrame:
    if not config.PRICE_ROLLING_WINDOW_PATH.exists():
        return pd.DataFrame(columns=PRICE_COLUMNS)
    return pd.read_parquet(config.PRICE_ROLLING_WINDOW_PATH)


def _trim_to_window(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only the most recent N unique trading dates present in the data.
    Date-based (not row-count-based) so it trims correctly even for symbols
    with gaps (halts, late listings, etc.)."""
    if df.empty:
        return df
    recent_dates = pd.Series(df["DATE"].unique()).sort_values().tail(config.ROLLING_WINDOW_TRADING_DAYS)
    return df[df["DATE"].isin(recent_dates)]


def append_daily_bhavcopy(daily_df: pd.DataFrame) -> int:
    """
    Merge one day's bhavcopy into the rolling window, then trim back down to
    the configured window size. Idempotent: re-running for a date already
    present replaces that date's rows rather than duplicating them.
    Returns the number of symbols present in today's update.
    """
    if daily_df.empty:
        return 0

    new_rows = daily_df[[c for c in PRICE_COLUMNS if c in daily_df.columns]].copy()

    existing = load_rolling_window()
    combined = pd.concat([existing, new_rows], ignore_index=True)
    combined = combined.drop_duplicates(subset=["SYMBOL", "DATE"], keep="last")
    combined = combined.sort_values(["SYMBOL", "DATE"]).reset_index(drop=True)
    combined = _trim_to_window(combined)

    config.PRICE_DIR.mkdir(parents=True, exist_ok=True)
    combined.to_parquet(config.PRICE_ROLLING_WINDOW_PATH, index=False)

    logger.info(
        "Rolling window updated: %d symbols today, %d total rows, %d unique dates in window.",
        new_rows["SYMBOL"].nunique(), len(combined), combined["DATE"].nunique(),
    )
    return new_rows["SYMBOL"].nunique()


def load_symbol_history(symbol: str, as_of=None) -> pd.DataFrame:
    """History for one symbol from the rolling window, optionally capped at `as_of`."""
    window = load_rolling_window()
    if window.empty:
        return window
    hist = window[window["SYMBOL"] == symbol]
    if as_of is not None:
        hist = hist[hist["DATE"] <= pd.Timestamp(as_of)]
    return hist.sort_values("DATE").reset_index(drop=True)


def list_available_symbols() -> list[str]:
    window = load_rolling_window()
    if window.empty:
        return []
    return sorted(window["SYMBOL"].unique().tolist())


def list_trading_sessions() -> list[pd.Timestamp]:
    """Sorted list of every unique trading date currently in the rolling
    window. Used to count actual trading sessions between two dates (e.g.
    an anchor date and today) — needed because RETRO_NEW_EP promotion can
    jump an anchor's date backward in time, so a simple day-counter can't
    be trusted; the real calendar has to be consulted."""
    window = load_rolling_window()
    if window.empty:
        return []
    return sorted(pd.Timestamp(d) for d in window["DATE"].unique())
