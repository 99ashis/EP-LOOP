"""
Permanent, append-only log of every labeled EP day — (symbol, date, label,
close), for every row the classifier has ever produced. This is what the
efficacy classifier queries to answer "what happened to this symbol in the
N days after its New_EP" without re-reading old CSV/JSON report files.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from src import config

EVENTS_LOG_COLUMNS = ["SYMBOL", "DATE", "LABEL", "CLOSE"]


def load_events_log() -> pd.DataFrame:
    if not config.EFFICACY_EVENTS_LOG_PATH.exists():
        return pd.DataFrame(columns=EVENTS_LOG_COLUMNS)
    return pd.read_parquet(config.EFFICACY_EVENTS_LOG_PATH)


def append_daily_events(daily_output: pd.DataFrame, as_of: date) -> None:
    if daily_output.empty:
        return
    new_rows = pd.DataFrame({
        "SYMBOL": daily_output["SYMBOL"],
        "DATE": pd.Timestamp(as_of),
        "LABEL": daily_output["LABEL"],
        "CLOSE": daily_output["CLOSE"],
    })
    existing = load_events_log()
    combined = pd.concat([existing, new_rows], ignore_index=True)
    combined = combined.drop_duplicates(subset=["SYMBOL", "DATE", "LABEL"], keep="last")
    combined.to_parquet(config.EFFICACY_EVENTS_LOG_PATH, index=False)


def events_for_symbol_between(symbol: str, start_date, end_date) -> pd.DataFrame:
    """Inclusive on both ends, sorted by DATE ascending."""
    log = load_events_log()
    if log.empty:
        return log
    mask = (
        (log["SYMBOL"] == symbol)
        & (log["DATE"] >= pd.Timestamp(start_date))
        & (log["DATE"] <= pd.Timestamp(end_date))
    )
    return log[mask].sort_values("DATE").reset_index(drop=True)
