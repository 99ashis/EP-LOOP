"""
Daily orchestration for the efficacy study: records new NEW_EP events, then
for EACH configured classification window (10D, 20D — see config.py)
independently classifies events whose window has matured and computes
their matured excess returns. Same event, same row, two parallel
classifications — not two separate tracker tables. Called once a day from
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
    """How many trading sessions have elapsed from d1 to d2 (0 = same day)."""
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

        for window_key, window_sessions in config.EFFICACY_CLASSIFICATION_WINDOWS.items():
            bucket_col = f"{window_key}__BUCKET"
            anchor_date_col = f"{window_key}__ANCHOR_DATE"
            anchor_close_col = f"{window_key}__ANCHOR_CLOSE"

            # --- Step 1: classify THIS window, if not already, once it has matured ---
            if pd.isna(row[bucket_col]):
                offset = _session_offset(sorted_dates, row["NEW_EP_DATE"], as_of)
                if offset is None or offset < window_sessions:
                    continue  # this window isn't ready yet — other window may still proceed below

                window_events = events_log.events_for_symbol_between(
                    row["SYMBOL"], row["NEW_EP_DATE"] + pd.Timedelta(days=1), as_of,
                )
                window_events = window_events[window_events["LABEL"].isin(
                    [config.STATUS_PERSISTENT, config.STATUS_SUSTAINED, config.STATUS_FIZZLE]
                )]
                result = classify(row["SYMBOL"], row["NEW_EP_DATE"], row["NEW_EP_CLOSE"], window_events)
                tracker.at[idx, bucket_col] = result.bucket
                tracker.at[idx, anchor_date_col] = pd.Timestamp(result.anchor_date)
                tracker.at[idx, anchor_close_col] = result.anchor_close
                changed = True
                row = tracker.loc[idx]  # refresh so the returns step below sees the new anchor

            # --- Step 2: compute matured excess returns for each horizon, for THIS window ---
            if pd.isna(row[anchor_date_col]):
                continue

            for horizon in config.EFFICACY_RETURN_HORIZONS:
                ret_col = f"{window_key}__RETURN_{horizon}"
                if pd.notna(tracker.at[idx, ret_col]):
                    continue

                offset = _session_offset(sorted_dates, row[anchor_date_col], as_of)
                if offset is None or offset < horizon:
                    continue

                anchor_idx = sorted_dates.index(pd.Timestamp(row[anchor_date_col]))
                target_date = sorted_dates[anchor_idx + horizon]

                hist = price_store.load_symbol_history(row["SYMBOL"], as_of=target_date)
                match = hist[hist["DATE"] == target_date]
                if match.empty:
                    continue  # halted/no data that day — leave pending, retry tomorrow
                target_close = float(match.iloc[0]["CLOSE"])
                anchor_close = float(row[anchor_close_col])
                stock_return = (target_close - anchor_close) / anchor_close * 100.0

                benchmark_returns: dict[str, float] = {}
                all_available = True
                for short_key in config.BENCHMARK_INDICES.keys():
                    anchor_b = get_benchmark_close_on(row[anchor_date_col], short_key)
                    target_b = get_benchmark_close_on(target_date, short_key)
                    if anchor_b is None or target_b is None:
                        all_available = False
                        break
                    benchmark_returns[short_key] = (target_b - anchor_b) / anchor_b * 100.0

                if not all_available:
                    continue  # a benchmark hasn't caught up yet — retry tomorrow

                tracker.at[idx, ret_col] = round(stock_return, 2)
                for short_key, b_return in benchmark_returns.items():
                    tracker.at[idx, f"{window_key}__{short_key}_RETURN_{horizon}"] = round(b_return, 2)
                    tracker.at[idx, f"{window_key}__EXCESS_RETURN_{short_key}_{horizon}"] = round(stock_return - b_return, 2)
                changed = True

    if changed:
        tracker_store.save_tracker(tracker)
        logger.info("Efficacy tracker updated: %d events tracked.", len(tracker))
