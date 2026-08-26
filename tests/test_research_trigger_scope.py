"""
Confirms the research trigger scope decision: fundamental research queues
ONLY on a fresh PERSISTENT_EP label. SUSTAINED_EP, NEW_EP, RETRO_NEW_EP,
and FIZZLE_OUT_EP must never appear in the queue, even when LABEL_CHANGED
is True for them.
"""
from datetime import date

import pandas as pd

from src.research.trigger import build_research_queue


def _row(symbol, label, changed=True):
    return {
        "SYMBOL": symbol, "LABEL": label, "LABEL_CHANGED": changed,
        "ANCHOR_DATE": pd.Timestamp(date(2026, 8, 14)), "ANCHOR_CLOSE": 100.0,
        "SESSIONS_SINCE_ANCHOR": 1, "CLOSE": 106.0, "PCT_MOVE_VS_PREV": 6.0,
        "VOLUME_MULTIPLE": 6.0,
    }


def test_only_persistent_is_queued():
    daily_output = pd.DataFrame([
        _row("AAA", "NEW_EP"),
        _row("BBB", "PERSISTENT_EP"),
        _row("CCC", "RETRO_NEW_EP"),
        _row("DDD", "SUSTAINED_EP"),
        _row("EEE", "FIZZLE_OUT_EP"),
    ])
    queue = build_research_queue(daily_output, date(2026, 8, 14))
    assert set(queue["SYMBOL"]) == {"BBB"}
    assert set(queue["LABEL"]) == {"PERSISTENT_EP"}


def test_sustained_ep_no_longer_queues():
    daily_output = pd.DataFrame([_row("DDD", "SUSTAINED_EP")])
    queue = build_research_queue(daily_output, date(2026, 8, 14))
    assert queue.empty


def test_unchanged_persistent_does_not_requeue():
    daily_output = pd.DataFrame([_row("BBB", "PERSISTENT_EP", changed=False)])
    queue = build_research_queue(daily_output, date(2026, 8, 14))
    assert queue.empty


def test_empty_daily_output_returns_empty_queue():
    queue = build_research_queue(pd.DataFrame(), date(2026, 8, 14))
    assert queue.empty
    assert list(queue.columns) == [
        "SYMBOL", "TRIGGER_REASON_FOR_RESEARCH", "LABEL",
        "ANCHOR_DATE", "ANCHOR_CLOSE", "SESSIONS_SINCE_ANCHOR",
        "CLOSE", "PCT_MOVE_VS_PREV", "VOLUME_MULTIPLE",
    ]
