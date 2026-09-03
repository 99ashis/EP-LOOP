"""
Confirms the research trigger scope decision: fundamental research fires
on exactly two count-based events per anchor generation — the 1st
PERSISTENT_EP occurrence and the 3rd SUSTAINED_EP occurrence. Every other
Persistent/Sustained day, plus NEW_EP/RETRO_NEW_EP/FIZZLE_OUT_EP, must
never appear in the queue.
"""
from datetime import date

import pandas as pd

from src.research.trigger import build_research_queue


def _row(symbol, label, persistent_count=0, sustained_count=0):
    return {
        "SYMBOL": symbol, "LABEL": label,
        "PERSISTENT_COUNT": persistent_count, "SUSTAINED_COUNT": sustained_count,
        "ANCHOR_DATE": pd.Timestamp(date(2026, 8, 14)), "ANCHOR_CLOSE": 100.0,
        "SESSIONS_SINCE_ANCHOR": 1, "CLOSE": 106.0, "PCT_MOVE_VS_PREV": 6.0,
        "VOLUME_MULTIPLE": 6.0,
    }


def test_first_persistent_occurrence_triggers():
    daily_output = pd.DataFrame([_row("AAA", "PERSISTENT_EP", persistent_count=1)])
    queue = build_research_queue(daily_output, date(2026, 8, 14))
    assert set(queue["SYMBOL"]) == {"AAA"}


def test_second_and_later_persistent_occurrences_do_not_trigger():
    daily_output = pd.DataFrame([
        _row("AAA", "PERSISTENT_EP", persistent_count=2),
        _row("BBB", "PERSISTENT_EP", persistent_count=5),
    ])
    queue = build_research_queue(daily_output, date(2026, 8, 14))
    assert queue.empty


def test_third_sustained_occurrence_triggers():
    daily_output = pd.DataFrame([_row("CCC", "SUSTAINED_EP", sustained_count=3)])
    queue = build_research_queue(daily_output, date(2026, 8, 14))
    assert set(queue["SYMBOL"]) == {"CCC"}


def test_first_second_and_fourth_sustained_occurrences_do_not_trigger():
    daily_output = pd.DataFrame([
        _row("AAA", "SUSTAINED_EP", sustained_count=1),
        _row("BBB", "SUSTAINED_EP", sustained_count=2),
        _row("CCC", "SUSTAINED_EP", sustained_count=4),
    ])
    queue = build_research_queue(daily_output, date(2026, 8, 14))
    assert queue.empty


def test_other_labels_never_trigger_regardless_of_counts():
    daily_output = pd.DataFrame([
        _row("AAA", "NEW_EP"),
        _row("BBB", "RETRO_NEW_EP"),
        _row("CCC", "FIZZLE_OUT_EP"),
    ])
    queue = build_research_queue(daily_output, date(2026, 8, 14))
    assert queue.empty


def test_multiple_qualifying_symbols_same_day():
    daily_output = pd.DataFrame([
        _row("AAA", "PERSISTENT_EP", persistent_count=1),
        _row("BBB", "SUSTAINED_EP", sustained_count=3),
        _row("CCC", "PERSISTENT_EP", persistent_count=2),  # should NOT be included
    ])
    queue = build_research_queue(daily_output, date(2026, 8, 14))
    assert set(queue["SYMBOL"]) == {"AAA", "BBB"}


def test_trigger_reason_text_distinguishes_the_two_events():
    daily_output = pd.DataFrame([
        _row("AAA", "PERSISTENT_EP", persistent_count=1),
        _row("BBB", "SUSTAINED_EP", sustained_count=3),
    ])
    queue = build_research_queue(daily_output, date(2026, 8, 14))
    aaa_reason = queue[queue["SYMBOL"] == "AAA"]["TRIGGER_REASON_FOR_RESEARCH"].iloc[0]
    bbb_reason = queue[queue["SYMBOL"] == "BBB"]["TRIGGER_REASON_FOR_RESEARCH"].iloc[0]
    assert "1st Persistent" in aaa_reason
    assert "3rd Sustained" in bbb_reason


def test_empty_daily_output_returns_empty_queue():
    queue = build_research_queue(pd.DataFrame(), date(2026, 8, 14))
    assert queue.empty
    assert list(queue.columns) == [
        "SYMBOL", "TRIGGER_REASON_FOR_RESEARCH", "LABEL",
        "ANCHOR_DATE", "ANCHOR_CLOSE", "SESSIONS_SINCE_ANCHOR",
        "CLOSE", "PCT_MOVE_VS_PREV", "VOLUME_MULTIPLE",
    ]
