"""
One-time (or occasional) historical backfill: downloads bhavcopy for every
trading day in a date range and builds the price history.

Usage:
    python -m scripts.backfill_historical --start 2024-01-01 --end 2026-08-13

IMPORTANT — what backfill actually populates:
  * data/raw_bhavcopy/  — gets ALL days in the range, permanently (this is
    the real archive; nothing here is ever trimmed).
  * data/prices/rolling_window.parquet — only ends up holding the LAST
    ~100 trading days (config.ROLLING_WINDOW_TRADING_DAYS), regardless of
    how far back you backfill. That's intentional: this file is an
    operational cache for the daily detector, not a historical archive.
    If you later want a multi-year consolidated dataset (e.g. for
    backtesting), write a separate script that reads the raw_bhavcopy
    archive over your date range and builds it fresh — don't try to make
    the rolling window carry that job too, it'll defeat the point of
    keeping it small and fast.

Notes:
  * NSE archives currently go back to roughly 2024-07-08 in this UDiFF format.
    For history before that you need the old sec_bhavdata_full format (a
    separate downloader) — not included here since it's discontinued and
    only useful for cold-start backfill. Flag it if you need pre-2024 history
    and we'll add a legacy-format branch.
  * This is rate-limited (SLEEP_BETWEEN_REQUESTS) to avoid hammering NSE.
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import date, timedelta

from src.data.bhavcopy_downloader import download_bhavcopy, BhavcopyDownloadError
from src.data.price_store import append_daily_bhavcopy

logger = logging.getLogger(__name__)
SLEEP_BETWEEN_REQUESTS = 1.5  # seconds, be a polite citizen of nseindia.com


def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def backfill(start: date, end: date) -> None:
    total_days, ok_days, skipped, failed = 0, 0, 0, 0
    for d in daterange(start, end):
        if d.weekday() >= 5:  # skip Sat/Sun outright, saves a request
            continue
        total_days += 1
        try:
            df = download_bhavcopy(d)
        except BhavcopyDownloadError as exc:
            logger.error("Giving up on %s: %s", d, exc)
            failed += 1
            time.sleep(SLEEP_BETWEEN_REQUESTS)
            continue

        if df is None:
            skipped += 1  # holiday
        else:
            append_daily_bhavcopy(df)
            ok_days += 1
            logger.info("Backfilled %s: %d symbols", d, len(df))

        time.sleep(SLEEP_BETWEEN_REQUESTS)

    logger.info(
        "Backfill complete. trading-days-attempted=%d ok=%d holidays_skipped=%d failed=%d",
        total_days, ok_days, skipped, failed,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    args = parser.parse_args()
    backfill(date.fromisoformat(args.start), date.fromisoformat(args.end))
