"""
Daily orchestration for the efficacy study: records new NEW_EP events,
classifies ones whose 10-session observation window has elapsed, and
computes matured excess returns at each horizon. Called once a day from
run_daily.py, entirely separate from — and after — the core EP
classification. Never touches src/ep/.
"""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from src import config
from src.data import price_store
from src.data.nifty_downloader import get_benchmark_close_on
from src.efficacy import tracker_store, events_log
from src.efficacy.classifier import classify

logger = logging.getLogger(__name__)


def _session_offset(sorted_dates: list[pd.Timestamp], d1, d2) -> int | None:
    """How many trading sessions have elapsed from d1 to d2 (0 = same day).
    Not the same as classifier.py's inclusive-span logic in src/ep/ —
    this is a plain offset, used purely to check "has N sessions passed"."""
    d1_ts, d2_ts = pd.Timestamp(d1), pd.Timestamp(d2)
    try:
        return sorted_dates.index(d2_ts) - sorted_dates.index(d1_ts)
    except ValueError:
        return None


def run_efficacy_daily(daily_output: pd.DataFrame, as_of: date) -> None:
    events_log.append_daily_events(daily_output, as_of)
    tracker_store.register_new_events(daily_output, as_of)

    tracker = tracker_store.load_tracker()
    if tracker.empty:
        return

    sorted_dates = price_store.list_trading_sessions()
    changed = False

    for idx in tracker.index:
        row = tracker.loc[idx]

        # --- Step 1: classify, if not already, once the window has matured ---
        if pd.isna(row["BUCKET"]):
            offset = _session_offset(sorted_dates, row["NEW_EP_DATE"], as_of)
            if offset is None or offset < config.EFFICACY_CLASSIFICATION_WINDOW_SESSIONS:
                continue
            window = events_log.events_for_symbol_between(
                row["SYMBOL"], row["NEW_EP_DATE"] + pd.Timedelta(days=1), as_of,
            )
            window = window[window["LABEL"].isin(
                [config.STATUS_PERSISTENT, config.STATUS_SUSTAINED, config.STATUS_FIZZLE]
            )]
            result = classify(row["SYMBOL"], row["NEW_EP_DATE"], row["NEW_EP_CLOSE"], window)
            tracker.at[idx, "BUCKET"] = result.bucket
            tracker.at[idx, "ANCHOR_DATE"] = pd.Timestamp(result.anchor_date)
            tracker.at[idx, "ANCHOR_CLOSE"] = result.anchor_close
            changed = True
            row = tracker.loc[idx]

        # --- Step 2: compute matured excess returns for each horizon ---
        if pd.isna(row["ANCHOR_DATE"]):
            continue
        for horizon in config.EFFICACY_RETURN_HORIZONS:
            col = f"RETURN_{horizon}"
            if pd.notna(tracker.at[idx, col]):
                continue

            offset = _session_offset(sorted_dates, row["ANCHOR_DATE"], as_of)
            if offset is None or offset < horizon:
                continue

            anchor_idx = sorted_dates.index(pd.Timestamp(row["ANCHOR_DATE"]))
            target_date = sorted_dates[anchor_idx + horizon]

            hist = price_store.load_symbol_history(row["SYMBOL"], as_of=target_date)
            match = hist[hist["DATE"] == target_date]
            if match.empty:
                continue  # halted/no data that day — leave pending, retry tomorrow
            target_close = float(match.iloc[0]["CLOSE"])
            stock_return = (target_close - row["ANCHOR_CLOSE"]) / row["ANCHOR_CLOSE"] * 100.0

            # Require ALL benchmarks available before committing any of this
            # horizon's numbers — keeps each horizon's row atomic rather
            # than partially filled while waiting on one benchmark.
            benchmark_returns: dict[str, float] = {}
            all_available = True
            for short_key in config.BENCHMARK_INDICES.keys():
                anchor_b = get_benchmark_close_on(row["ANCHOR_DATE"], short_key)
                target_b = get_benchmark_close_on(target_date, short_key)
                if anchor_b is None or target_b is None:
                    all_available = False
                    break
                benchmark_returns[short_key] = (target_b - anchor_b) / anchor_b * 100.0

            if not all_available:
                continue  # a benchmark hasn't caught up yet — retry tomorrow

            tracker.at[idx, col] = round(stock_return, 2)
            for short_key, b_return in benchmark_returns.items():
                tracker.at[idx, f"{short_key}_RETURN_{horizon}"] = round(b_return, 2)
                tracker.at[idx, f"EXCESS_RETURN_{short_key}_{horizon}"] = round(stock_return - b_return, 2)
            changed = True

    if changed:
        tracker_store.save_tracker(tracker)
        logger.info("Efficacy tracker updated: %d events tracked.", len(tracker))
