"""
Proves the rolling-window trim behavior explicitly: once the window size is
exceeded, the oldest trading day is dropped for EVERY symbol in a single
operation — not per-symbol, not gradually. Uses a shrunk window (5 days
instead of the real 100) so the test runs in milliseconds while exercising
the exact same code path.
"""
import shutil
from datetime import date, timedelta

import pandas as pd
import pytest

from src import config
from src.data import price_store


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "PRICE_DIR", tmp_path / "prices")
    monkeypatch.setattr(config, "PRICE_ROLLING_WINDOW_PATH", tmp_path / "prices" / "rolling_window.parquet")
    config.PRICE_DIR.mkdir(parents=True, exist_ok=True)
    yield
    shutil.rmtree(tmp_path, ignore_errors=True)


def _trading_days(start: date, count: int) -> list[date]:
    days, d = [], start
    while len(days) < count:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def test_101st_day_drops_oldest_day_for_all_symbols(monkeypatch):
    monkeypatch.setattr(config, "ROLLING_WINDOW_TRADING_DAYS", 100)  # the real default, explicitly

    symbols = ["AAA", "BBB", "CCC"]
    days = _trading_days(date(2026, 1, 1), 101)  # one MORE than the window

    for i, d in enumerate(days):
        day_df = pd.DataFrame([
            {
                "SYMBOL": s, "DATE": pd.Timestamp(d),
                "OPEN": 100 + i, "HIGH": 101 + i, "LOW": 99 + i, "CLOSE": 100 + i,
                "PREV_CLOSE": 99 + i, "VOLUME": 10_000, "TURNOVER": (100 + i) * 10_000,
            }
            for s in symbols
        ])
        price_store.append_daily_bhavcopy(day_df)

    window = price_store.load_rolling_window()
    unique_dates = sorted(window["DATE"].unique())

    # Exactly 100 unique trading dates remain, never 101.
    assert len(unique_dates) == 100

    oldest_day_sent_in = pd.Timestamp(days[0])
    newest_day_sent_in = pd.Timestamp(days[-1])

    # Day 1 (the 101st-oldest once day 101 arrived) is gone entirely.
    assert oldest_day_sent_in not in unique_dates
    # The most recent day is present.
    assert newest_day_sent_in in unique_dates

    # And it's gone for EVERY symbol, not just some — the whole point of
    # trimming on the combined table rather than per-symbol.
    for s in symbols:
        sym_dates = set(window[window["SYMBOL"] == s]["DATE"])
        assert oldest_day_sent_in not in sym_dates, f"{s} still has the dropped day"
        assert len(sym_dates) == 100, f"{s} has {len(sym_dates)} days, expected 100"

    # Total row count = 100 days x 3 symbols, not 101 x 3.
    assert len(window) == 100 * len(symbols)
