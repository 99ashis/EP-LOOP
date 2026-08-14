"""
Checks the NEW_EP condition for a symbol — only relevant when it has no
currently-active anchor (see classifier.py for what "active" means and how
the anchor lifecycle works).

NEW_EP fires when, on a single day:
  1. volume >= 5x the 50-trading-session average volume (baseline excludes today)
  2. AND close >= 1.05x the previous trading session's close
  3. AND volume > 100,000 shares (absolute floor)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import pandas as pd

from src import config
from src.data import price_store
from src.ep.indicators import avg_volume

logger = logging.getLogger(__name__)


@dataclass
class NewEPTrigger:
    symbol: str
    trade_date: date
    close: float
    prev_close: float
    volume: float
    avg_volume_50: float
    volume_multiple: float
    pct_move_vs_prev: float


def evaluate_new_ep(symbol: str, today_row: pd.Series) -> NewEPTrigger | None:
    history = price_store.load_symbol_history(symbol, as_of=today_row["DATE"])

    if len(history) < config.MIN_HISTORY_SESSIONS_REQUIRED:
        return None  # not enough history to compute a reliable 50-session baseline yet

    vol_avg = avg_volume(history, config.VOLUME_BASELINE_LOOKBACK_SESSIONS)
    if pd.isna(vol_avg) or vol_avg <= 0:
        return None

    close = float(today_row["CLOSE"])
    volume = float(today_row["VOLUME"])
    prev_close = float(today_row.get("PREV_CLOSE", float("nan")))

    if pd.isna(prev_close) or prev_close <= 0:
        prior_rows = history[history["DATE"] < pd.Timestamp(today_row["DATE"])]
        if prior_rows.empty:
            return None
        prev_close = float(prior_rows.iloc[-1]["CLOSE"])

    volume_multiple = volume / vol_avg
    pct_move = (close - prev_close) / prev_close * 100.0

    volume_ok = volume_multiple >= config.NEW_EP_VOLUME_MULTIPLE
    price_ok = close >= prev_close * (1 + config.NEW_EP_PRICE_PCT_VS_PREV_CLOSE / 100.0)
    abs_volume_ok = volume > config.NEW_EP_MIN_ABS_VOLUME
    min_close_ok = close > config.NEW_EP_MIN_CLOSE_PRICE

    if not (volume_ok and price_ok and abs_volume_ok and min_close_ok):
        return None

    return NewEPTrigger(
        symbol=symbol,
        trade_date=today_row["DATE"],
        close=close,
        prev_close=prev_close,
        volume=volume,
        avg_volume_50=round(vol_avg, 2),
        volume_multiple=round(volume_multiple, 2),
        pct_move_vs_prev=round(pct_move, 2),
    )


def scan_for_new_eps(daily_bhavcopy: pd.DataFrame, eligible_symbols: set[str]) -> dict[str, NewEPTrigger]:
    """Only checks symbols in `eligible_symbols` — i.e. no currently-active anchor."""
    triggers: dict[str, NewEPTrigger] = {}
    for _, row in daily_bhavcopy.iterrows():
        symbol = row["SYMBOL"]
        if symbol not in eligible_symbols:
            continue
        try:
            result = evaluate_new_ep(symbol, row)
        except Exception:  # noqa: BLE001
            logger.exception("Failed NEW_EP evaluation for %s — skipping symbol.", symbol)
            continue
        if result is not None:
            triggers[symbol] = result

    logger.info("NEW_EP scan complete: %d triggers out of %d eligible symbols.",
                len(triggers), len(eligible_symbols))
    return triggers
