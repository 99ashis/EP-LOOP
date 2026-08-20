"""
Computes the EP efficacy Track Record: all-time aggregate stats per bucket
per horizon per benchmark, plus a cohort breakdown by ANCHOR_DATE's month
(confirmed with the user: cohorts group by ANCHOR_DATE, not NEW_EP_DATE —
the anchor is when each bucket's return clock actually starts, and for
buckets 2-9 that can land well after the original NEW_EP).

Reads only MATURED rows — an event with no computed return for a given
horizon is silently excluded from that horizon's stats, exactly as it
should be for a forward-test track record (no partial/pending numbers
leaking into an aggregate).
"""
from __future__ import annotations

import json

import pandas as pd

from src import config
from src.efficacy import tracker_store

BUCKET_ORDER = [1, 2, 3, 4, 5, 6, 7, 8, 9]


def _stats_for_group(df: pd.DataFrame, horizon: int, short_key: str) -> dict:
    col = f"EXCESS_RETURN_{short_key}_{horizon}"
    matured = df[df[col].notna()] if col in df.columns else df.iloc[0:0]
    n = len(matured)
    if n == 0:
        return {"n": 0, "mean_excess": None, "median_excess": None, "hit_rate": None}
    return {
        "n": n,
        "mean_excess": round(float(matured[col].mean()), 2),
        "median_excess": round(float(matured[col].median()), 2),
        "hit_rate": round(float((matured[col] > 0).mean()) * 100, 1),
    }


def build_track_record(tracker: pd.DataFrame | None = None) -> dict:
    """`tracker` can be passed directly for testing; defaults to loading
    from disk (the real daily-run path)."""
    if tracker is None:
        tracker = tracker_store.load_tracker()

    result = {
        "horizons": list(config.EFFICACY_RETURN_HORIZONS),
        "benchmarks": list(config.BENCHMARK_INDICES.keys()),
        "all_time": {},
        "cohorts": {},
    }

    if tracker.empty:
        return result

    tracker = tracker.copy()
    tracker["ANCHOR_MONTH"] = pd.to_datetime(tracker["ANCHOR_DATE"]).dt.strftime("%Y-%m")

    for horizon in config.EFFICACY_RETURN_HORIZONS:
        result["all_time"][str(horizon)] = {}
        for bucket in BUCKET_ORDER:
            bucket_df = tracker[tracker["BUCKET"] == bucket]
            result["all_time"][str(horizon)][str(bucket)] = {
                short_key: _stats_for_group(bucket_df, horizon, short_key)
                for short_key in config.BENCHMARK_INDICES.keys()
            }

    for horizon in config.EFFICACY_RETURN_HORIZONS:
        result["cohorts"][str(horizon)] = {}
        for bucket in BUCKET_ORDER:
            bucket_df = tracker[tracker["BUCKET"] == bucket]
            if bucket_df.empty:
                continue
            months = sorted(bucket_df["ANCHOR_MONTH"].dropna().unique())
            bucket_cohorts = {}
            for month in months:
                month_df = bucket_df[bucket_df["ANCHOR_MONTH"] == month]
                bucket_cohorts[month] = {
                    short_key: _stats_for_group(month_df, horizon, short_key)
                    for short_key in config.BENCHMARK_INDICES.keys()
                }
            if bucket_cohorts:
                result["cohorts"][str(horizon)][str(bucket)] = bucket_cohorts

    return result


def save_track_record() -> None:
    data = build_track_record()
    config.SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = config.SITE_DATA_DIR / "track_record.json"
    path.write_text(json.dumps(data, indent=2))
