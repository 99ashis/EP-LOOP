"""Small helper used by the EP engine: the 50-trading-session volume baseline."""
from __future__ import annotations

import pandas as pd


def avg_volume(history: pd.DataFrame, lookback_sessions: int) -> float:
    """
    Average volume over the trailing `lookback_sessions`, EXCLUDING the most
    recent row (today) — so today's own spike can never inflate its own
    baseline. `history` must already be sorted by DATE ascending and include
    today's row as the last one.
    """
    if len(history) < 2:
        return float("nan")
    window = history.iloc[:-1].tail(lookback_sessions)
    if window.empty:
        return float("nan")
    return float(window["VOLUME"].mean())
