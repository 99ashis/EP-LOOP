"""
Tests the final anchor-generation EP engine: NEW_EP's $100 floor, Persistent's
2x threshold, Sustained's move band, Fizzle anchored to whichever generation
is active, and the RETRO_NEW_EP promotion/chain mechanic end-to-end. No
network — price history is seeded synthetically.
"""
import shutil
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
import pytest

from src import config
from src.data import price_store
from src.ep.classifier import run_daily_classification


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


def _next_session(d: date) -> date:
    d = d + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _bar(symbol, d, close, prev_close, volume):
    return pd.DataFrame([{
        "SYMBOL": symbol, "DATE": pd.Timestamp(d),
        "OPEN": close, "HIGH": close, "LOW": close, "CLOSE": close,
        "PREV_CLOSE": prev_close, "VOLUME": volume, "TURNOVER": close * volume,
    }])


def _seed_flat_baseline(symbol: str, start: date, sessions: int, price: float, volume: float) -> list[date]:
    days = _trading_days(start, sessions)
    for d in days:
        price_store.append_daily_bhavcopy(_bar(symbol, d, price, price, volume))
    return days


def test_new_ep_requires_close_above_100():
    days = _seed_flat_baseline("MMM", date(2026, 1, 1), 55, price=50.0, volume=50_000)  # baseline close < 100
    trigger_day = _next_session(days[-1] + timedelta(days=3))

    # Clears volume/price/abs-volume, but close (52.5) is still under the 100 floor.
    today = _bar("MMM", trigger_day, close=52.5, prev_close=50.0, volume=300_000)
    price_store.append_daily_bhavcopy(today)

    _, output = run_daily_classification(pd.DataFrame(), today, trigger_day)
    assert output.empty, "NEW_EP must not fire when close is under the ₹100 floor, regardless of other conditions"


def test_persistent_fires_at_2x_volume_not_4x():
    days = _seed_flat_baseline("NNN", date(2026, 1, 1), 55, price=100.0, volume=60_000)
    trigger_day = _next_session(days[-1] + timedelta(days=3))
    anchor_close = 106.0
    today = _bar("NNN", trigger_day, close=anchor_close, prev_close=100.0, volume=360_000)
    price_store.append_daily_bhavcopy(today)
    anchors, output = run_daily_classification(pd.DataFrame(), today, trigger_day)
    assert output.iloc[0]["LABEL"] == config.STATUS_NEW

    next_day = _next_session(trigger_day)
    # Exactly 2.5x baseline volume — would have failed the old 4x bar, must pass the new 2x bar.
    prev_close = anchor_close
    close = prev_close * 1.05  # clears both anchor-close and +4%-vs-prev
    today2 = _bar("NNN", next_day, close=close, prev_close=prev_close, volume=150_000)  # 2.5x of 60,000
    price_store.append_daily_bhavcopy(today2)
    _, output2 = run_daily_classification(anchors, today2, next_day)

    assert output2.iloc[0]["LABEL"] == config.STATUS_PERSISTENT


def test_sustained_move_band_excludes_flat_holding_days():
    days = _seed_flat_baseline("OOO", date(2026, 1, 1), 55, price=100.0, volume=60_000)
    trigger_day = _next_session(days[-1] + timedelta(days=3))
    anchor_close = 106.0
    today = _bar("OOO", trigger_day, close=anchor_close, prev_close=100.0, volume=360_000)
    price_store.append_daily_bhavcopy(today)
    anchors, output = run_daily_classification(pd.DataFrame(), today, trigger_day)

    next_day = _next_session(trigger_day)
    # Comfortably above 97% of anchor (105 >= 102.82) but essentially FLAT vs
    # yesterday (only +0.5%) — under the new move-band rule this must NOT be
    # Sustained, even though it would have qualified under the old rule.
    today2 = _bar("OOO", next_day, close=105.0, prev_close=104.5, volume=50_000)
    price_store.append_daily_bhavcopy(today2)
    _, output2 = run_daily_classification(anchors, today2, next_day)
    assert output2.empty, "a flat day near the anchor must land in the gap zone under the new move-band rule"


def test_sustained_fires_within_the_move_band():
    days = _seed_flat_baseline("PPP", date(2026, 1, 1), 55, price=100.0, volume=60_000)
    trigger_day = _next_session(days[-1] + timedelta(days=3))
    anchor_close = 106.0
    today = _bar("PPP", trigger_day, close=anchor_close, prev_close=100.0, volume=360_000)
    price_store.append_daily_bhavcopy(today)
    anchors, output = run_daily_classification(pd.DataFrame(), today, trigger_day)

    next_day = _next_session(trigger_day)
    prev_close = 104.0
    close = prev_close * 1.02  # +2%, inside (1.01, 1.04) and >= 97% of anchor_close
    assert close >= anchor_close * 0.97
    today2 = _bar("PPP", next_day, close=close, prev_close=prev_close, volume=50_000)
    price_store.append_daily_bhavcopy(today2)
    _, output2 = run_daily_classification(anchors, today2, next_day)
    assert output2.iloc[0]["LABEL"] == config.STATUS_SUSTAINED


def test_retro_new_ep_promotion_and_chain(monkeypatch):
    monkeypatch.setattr(config, "ANCHOR_ELIGIBILITY_WINDOW_SESSIONS", 6)  # shrink for a fast test

    symbol = "QQQ"
    days = _seed_flat_baseline(symbol, date(2026, 1, 1), 55, price=100.0, volume=60_000)

    # D1: NEW_EP. Generation 1 anchor: close=106, prev_close=100.
    d1 = _next_session(days[-1] + timedelta(days=3))
    day1 = _bar(symbol, d1, close=106.0, prev_close=100.0, volume=360_000)
    price_store.append_daily_bhavcopy(day1)
    anchors, out1 = run_daily_classification(pd.DataFrame(), day1, d1)
    assert out1.iloc[0]["LABEL"] == config.STATUS_NEW

    # D2-D5: four clean gap-zone days under generation 1 (low volume rules out
    # Persistent regardless of price; each day's own move stays outside the
    # Sustained band; none breach the Fizzle floor).
    prev_close = 106.0
    d = d1
    gap_closes = [101.0, 100.0, 100.5, 101.0]
    for gap_close in gap_closes:
        d = _next_session(d)
        day = _bar(symbol, d, close=gap_close, prev_close=prev_close, volume=50_000)
        price_store.append_daily_bhavcopy(day)
        anchors, out = run_daily_classification(anchors, day, d)
        assert out.empty, f"expected gap zone on {d}, got {out.to_dict('records') if not out.empty else None}"
        prev_close = gap_close
    d5 = d  # last gap day, close=101.0

    # D6: a genuine Persistent under generation 1 — the LAST valid day of its
    # (shrunk) 6-session window. Becomes the promotion candidate.
    d6 = _next_session(d5)
    day6 = _bar(symbol, d6, close=110.0, prev_close=101.0, volume=150_000)  # 2.5x baseline, >106 anchor, >=1.04*101
    price_store.append_daily_bhavcopy(day6)
    anchors, out6 = run_daily_classification(anchors, day6, d6)
    assert out6.iloc[0]["LABEL"] == config.STATUS_PERSISTENT
    assert anchors.iloc[0]["PROMOTION_CANDIDATE_DATE"] == pd.Timestamp(d6)

    # D7: generation 1 expires (session 7 > shrunk limit of 6). D6 gets promoted.
    d7 = _next_session(d6)
    day7 = _bar(symbol, d7, close=108.0, prev_close=110.0, volume=50_000)
    price_store.append_daily_bhavcopy(day7)
    anchors, out7 = run_daily_classification(anchors, day7, d7)

    assert out7.iloc[0]["LABEL"] == config.STATUS_RETRO_NEW
    assert out7.iloc[0]["GENERATION"] == 2
    assert out7.iloc[0]["ANCHOR_DATE"] == pd.Timestamp(d6)
    assert out7.iloc[0]["ANCHOR_CLOSE"] == 110.0
    assert out7.iloc[0]["ANCHOR_PREV_CLOSE"] == 101.0  # D6's own prev-close, NOT D1's (100)
    assert anchors.iloc[0]["GENERATION"] == 2
    assert anchors.iloc[0]["PERSISTENT_COUNT"] == 0, "counts must reset for the new generation"
    assert anchors.iloc[0]["ORIGIN_ANCHOR_DATE"] == pd.Timestamp(d1), "lineage back to the original NEW_EP must be preserved"

    # D8: Persistent under generation 2, anchored to D6's close (110), not D1's (106).
    d8 = _next_session(d7)
    day8 = _bar(symbol, d8, close=115.0, prev_close=108.0, volume=150_000)  # >110, >=1.04*108, 2.5x baseline
    price_store.append_daily_bhavcopy(day8)
    anchors, out8 = run_daily_classification(anchors, day8, d8)
    assert out8.iloc[0]["LABEL"] == config.STATUS_PERSISTENT
    assert out8.iloc[0]["GENERATION"] == 2
    assert out8.iloc[0]["PERSISTENT_COUNT"] == 1, "generation 2's count starts fresh, not carried over from generation 1"

    # D9: FIZZLE test proving it uses generation 2's OWN prev-close (101, D6's
    # prev-close), not the original generation 1 anchor's prev-close (100).
    # Close of 98 sits ABOVE the old (wrong) threshold of 0.98*100=98 exactly
    # (98 < 98 is False) but BELOW the correct threshold of 0.98*101=98.98.
    d9 = _next_session(d8)
    day9 = _bar(symbol, d9, close=98.0, prev_close=115.0, volume=50_000)
    price_store.append_daily_bhavcopy(day9)
    anchors, out9 = run_daily_classification(anchors, day9, d9)
    assert out9.iloc[0]["LABEL"] == config.STATUS_FIZZLE, \
        "fizzle must compare against generation 2's own pre-anchor close, not the original generation 1's"


def test_episode_dies_with_no_promotion_candidate_then_rechecks_fresh(monkeypatch):
    monkeypatch.setattr(config, "ANCHOR_ELIGIBILITY_WINDOW_SESSIONS", 2)  # shrink for a fast test

    symbol = "RRR"
    days = _seed_flat_baseline(symbol, date(2026, 1, 1), 55, price=100.0, volume=60_000)

    d1 = _next_session(days[-1] + timedelta(days=3))
    day1 = _bar(symbol, d1, close=106.0, prev_close=100.0, volume=360_000)
    price_store.append_daily_bhavcopy(day1)
    anchors, out1 = run_daily_classification(pd.DataFrame(), day1, d1)
    assert out1.iloc[0]["LABEL"] == config.STATUS_NEW

    # D2: gap zone, no Persistent ever fires — no promotion candidate gets set.
    d2 = _next_session(d1)
    day2 = _bar(symbol, d2, close=105.0, prev_close=106.0, volume=50_000)
    price_store.append_daily_bhavcopy(day2)
    anchors, out2 = run_daily_classification(anchors, day2, d2)
    assert out2.empty

    # D3: generation 1 expires (session 3 > shrunk limit of 2). No promotion
    # candidate exists -> episode dies outright. Today's own price does NOT
    # independently qualify as a fresh NEW_EP (small move, low volume).
    d3 = _next_session(d2)
    day3 = _bar(symbol, d3, close=104.0, prev_close=105.0, volume=40_000)
    price_store.append_daily_bhavcopy(day3)
    anchors, out3 = run_daily_classification(anchors, day3, d3)

    assert out3.empty
    assert anchors.empty, "the anchor must be dropped entirely, no promotion candidate to fall back on"
