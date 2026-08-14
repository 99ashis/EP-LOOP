"""Tests the finalized 5-section daily TXT report against the exact spec confirmed with the user."""
from datetime import date

import pandas as pd

from src import config
from src.report.text_report import build_daily_text_report


def _row(symbol, label, pct, anchor_date, persistent_count=0, sustained_count=0):
    return {
        "SYMBOL": symbol, "AS_OF_DATE": pd.Timestamp(date(2026, 8, 14)), "LABEL": label,
        "LABEL_CHANGED": True, "GENERATION": 1,
        "ANCHOR_DATE": pd.Timestamp(anchor_date), "ANCHOR_CLOSE": 100.0, "ANCHOR_PREV_CLOSE": 95.0,
        "SESSIONS_SINCE_ANCHOR": 1, "CLOSE": 106.0, "PREV_CLOSE": 100.0, "VOLUME": 300_000,
        "AVG_VOLUME_50": 50_000, "VOLUME_MULTIPLE": 6.0, "PCT_MOVE_VS_PREV": pct,
        "PERSISTENT_COUNT": persistent_count, "SUSTAINED_COUNT": sustained_count,
    }


def test_report_has_all_five_sections_in_order():
    report = build_daily_text_report(pd.DataFrame(), date(2026, 8, 14))
    order = [report.index(s) for s in
             ["NEW EP", "PERSISTENT EP", "RETRO NEW EP", "SUSTAINED EP", "FIZZLE OUT EP"]]
    assert order == sorted(order), "sections must appear in the confirmed order"


def test_empty_sections_show_none_today():
    report = build_daily_text_report(pd.DataFrame(), date(2026, 8, 14))
    assert report.count("None today") == 5


def test_new_ep_section_has_no_count_column():
    rows = pd.DataFrame([_row("RELIANCE", config.STATUS_NEW, 6.2, date(2026, 8, 14))])
    report = build_daily_text_report(rows, date(2026, 8, 14))
    new_section = report.split("PERSISTENT EP")[0]
    assert "RELIANCE" in new_section
    assert "COUNT" not in new_section
    assert "+6.20%" in new_section
    header_line = next(line for line in new_section.splitlines() if "TICKER" in line)
    assert header_line.strip().startswith("TICKER"), \
        "the row header must start with TICKER, not a separate per-row DATE column"


def test_persistent_section_shows_persistent_count():
    rows = pd.DataFrame([_row("INFY", config.STATUS_PERSISTENT, 4.5, date(2026, 8, 2), persistent_count=3)])
    report = build_daily_text_report(rows, date(2026, 8, 14))
    section = report.split("PERSISTENT EP")[1].split("RETRO NEW EP")[0]
    assert "INFY" in section
    assert "PERSISTENT COUNT" in section
    # the count value itself appears as a standalone cell
    assert any(line.strip().endswith("3") for line in section.splitlines() if "INFY" in line)


def test_sustained_section_shows_sustained_count_not_persistent_count():
    rows = pd.DataFrame([_row("TCS", config.STATUS_SUSTAINED, 1.8, date(2026, 8, 2), sustained_count=5)])
    report = build_daily_text_report(rows, date(2026, 8, 14))
    section = report.split("SUSTAINED EP")[1].split("FIZZLE OUT EP")[0]
    assert "SUSTAINED COUNT" in section
    assert "PERSISTENT COUNT" not in section
    assert "TCS" in section


def test_date_format_is_dd_mmm_yy():
    rows = pd.DataFrame([_row("WIPRO", config.STATUS_NEW, 5.5, date(2026, 8, 14))])
    report = build_daily_text_report(rows, date(2026, 8, 14))
    assert "14-Aug-26" in report


def test_pct_change_uses_previous_close_field_with_explicit_sign():
    rows = pd.DataFrame([
        _row("UP_STOCK", config.STATUS_NEW, 6.2, date(2026, 8, 14)),
        _row("DOWN_STOCK", config.STATUS_FIZZLE, -3.1, date(2026, 8, 1)),
    ])
    report = build_daily_text_report(rows, date(2026, 8, 14))
    assert "+6.20%" in report
    assert "-3.10%" in report
