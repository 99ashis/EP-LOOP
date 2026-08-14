"""
Builds the finalized daily EP report: a single plain-text file, five
sections stacked in order (New EP / Persistent EP / Retro New EP /
Sustained EP / Fizzle Out EP), each with its own column set:

  New EP, Retro New EP, Fizzle Out EP  -> Date, Ticker, % Change, Anchor Date
  Persistent EP                         -> ... + Persistent Count
  Sustained EP                          -> ... + Sustained Count

This is the human-facing view, generated FROM the engineering
daily_output DataFrame (src/ep/classifier.py's OUTPUT_COLUMNS), which stays
untouched as the audit-grade source of truth. % Change is PCT_MOVE_VS_PREV
— vs. the previous trading session's close, confirmed with the user.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from src import config

DATE_FMT = "%d-%b-%y"  # e.g. 14-Aug-26

SECTION_ORDER = [
    (config.STATUS_NEW, "NEW EP", None),
    (config.STATUS_PERSISTENT, "PERSISTENT EP", ("PERSISTENT_COUNT", "PERSISTENT COUNT")),
    (config.STATUS_RETRO_NEW, "RETRO NEW EP", None),
    (config.STATUS_SUSTAINED, "SUSTAINED EP", ("SUSTAINED_COUNT", "SUSTAINED COUNT")),
    (config.STATUS_FIZZLE, "FIZZLE OUT EP", None),
]

_COL_DATE_W = 11
_COL_TICKER_W = 16
_COL_PCT_W = 10
_COL_ANCHOR_W = 13
_COL_COUNT_W = 16


def _fmt_date(value) -> str:
    if value is None or pd.isna(value):
        return ""
    return pd.Timestamp(value).strftime(DATE_FMT)


def _fmt_pct(value) -> str:
    if value is None or pd.isna(value):
        return ""
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.2f}%"


def _section_table(rows: pd.DataFrame, extra_col: tuple[str, str] | None) -> str:
    headers = ["TICKER", "% CHANGE", "ANCHOR DATE"]
    widths = [_COL_TICKER_W, _COL_PCT_W, _COL_ANCHOR_W]
    if extra_col:
        headers.append(extra_col[1])
        widths.append(_COL_COUNT_W)

    lines = ["  ".join(h.ljust(w) for h, w in zip(headers, widths))]
    lines.append("-" * (sum(widths) + 2 * (len(widths) - 1)))

    if rows.empty:
        lines.append("None today")
        return "\n".join(lines)

    for _, row in rows.iterrows():
        cells = [
            str(row["SYMBOL"]).ljust(_COL_TICKER_W),
            _fmt_pct(row["PCT_MOVE_VS_PREV"]).ljust(_COL_PCT_W),
            _fmt_date(row["ANCHOR_DATE"]).ljust(_COL_ANCHOR_W),
        ]
        if extra_col:
            cells.append(str(int(row[extra_col[0]])).ljust(_COL_COUNT_W))
        lines.append("  ".join(cells))

    return "\n".join(lines)


def build_daily_text_report(daily_output: pd.DataFrame, as_of: date) -> str:
    header = f"EP DAILY ALERT — {as_of.strftime(DATE_FMT)}"
    parts = [header, "=" * max(len(header), 60), ""]

    for status, title, extra_col in SECTION_ORDER:
        section_rows = daily_output[daily_output["LABEL"] == status] if not daily_output.empty \
            else pd.DataFrame()
        parts.append(title)
        parts.append("-" * max(len(title), 60))
        parts.append(_section_table(section_rows, extra_col))
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"


def save_text_report(report_text: str, as_of: date):
    path = config.EP_OUTPUT_DIR / f"ep_report_{as_of.isoformat()}.txt"
    path.write_text(report_text)
    return path
