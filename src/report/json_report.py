"""
Builds the JSON data files consumed by the GitHub Pages calendar site
(docs/index.html):

  docs/data/<date>.json  — full detail for one day, all 5 sections
  docs/data/index.json   — running summary (counts per category, per day)
                            across every day the pipeline has processed,
                            so the calendar can render instantly without
                            fetching every individual day's file.

Derived FROM the engineering daily_output DataFrame — same source of truth
as text_report.py, just a different shape for the website to consume.
"""
from __future__ import annotations

import json
from datetime import date

import pandas as pd

from src import config

LABEL_TO_KEY = {
    config.STATUS_NEW: "new",
    config.STATUS_PERSISTENT: "persistent",
    config.STATUS_RETRO_NEW: "retro",
    config.STATUS_SUSTAINED: "sustained",
    config.STATUS_FIZZLE: "fizzle",
}


def _entry(row: pd.Series) -> dict:
    pct = row.get("PCT_MOVE_VS_PREV")
    anchor_date = row.get("ANCHOR_DATE")
    entry = {
        "ticker": row["SYMBOL"],
        "pct": None if pct is None or pd.isna(pct) else round(float(pct), 2),
        "anchor_date": None if anchor_date is None or pd.isna(anchor_date)
        else pd.Timestamp(anchor_date).strftime("%d-%b-%y"),
    }
    label = row["LABEL"]
    if label == config.STATUS_PERSISTENT:
        entry["count"] = int(row["PERSISTENT_COUNT"])
    elif label == config.STATUS_SUSTAINED:
        entry["count"] = int(row["SUSTAINED_COUNT"])

    if label in config.RESULTS_ELIGIBLE_LABELS:
        context = row.get("CATALYST_CONTEXT")
        if context is not None and not (isinstance(context, float) and pd.isna(context)):
            result_date = row.get("RESULT_DATE")
            days = row.get("DAYS_TO_OR_SINCE_RESULTS")
            entry["results"] = {
                "context": context,
                "result_date": None if result_date is None or pd.isna(result_date) else result_date,
                "days": None if days is None or pd.isna(days) else int(days),
            }
    return entry


def build_day_detail(daily_output: pd.DataFrame, as_of: date) -> dict:
    sections = {key: [] for key in LABEL_TO_KEY.values()}
    if not daily_output.empty:
        for _, row in daily_output.iterrows():
            key = LABEL_TO_KEY.get(row["LABEL"])
            if key:
                sections[key].append(_entry(row))
    return {"date": as_of.isoformat(), "sections": sections}


def build_day_summary(day_detail: dict) -> dict:
    return {key: len(entries) for key, entries in day_detail["sections"].items()}


def save_day_json(day_detail: dict, as_of: date) -> None:
    config.SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = config.SITE_DATA_DIR / f"{as_of.isoformat()}.json"
    path.write_text(json.dumps(day_detail, indent=2))


def update_index(day_summary: dict, as_of: date) -> None:
    config.SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    index_path = config.SITE_DATA_DIR / "index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {}
    index[as_of.isoformat()] = day_summary
    index_path.write_text(json.dumps(index, indent=2, sort_keys=True))


def build_and_save_site_data(daily_output: pd.DataFrame, as_of: date) -> None:
    """One call from run_daily.py does all three steps."""
    detail = build_day_detail(daily_output, as_of)
    save_day_json(detail, as_of)
    update_index(build_day_summary(detail), as_of)
