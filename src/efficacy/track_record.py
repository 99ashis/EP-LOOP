"""
Computes the EP efficacy Track Record: all-time aggregate stats AND monthly
cohort stats, per classification window (10D/20D), per return horizon, per
bucket, per benchmark. Cohorts group by each window's OWN ANCHOR_DATE — the
10D and 20D windows can (and often do) anchor to different dates for the
same underlying event, so their cohort placement can differ too.

Reads only MATURED rows — an event with no computed return for a given
window+horizon combination is silently excluded from those stats.
"""
from __future__ import annotations

import json

import pandas as pd

from src import config
from src.efficacy import tracker_store

BUCKET_ORDER = [1, 2, 3, 4, 5, 6, 7, 8, 9]


def _stats_for_group(df: pd.DataFrame, window_key: str, horizon: int, short_key: str) -> dict:
    col = f"{window_key}__EXCESS_RETURN_{short_key}_{horizon}"
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
        "windows": list(config.EFFICACY_CLASSIFICATION_WINDOWS.keys()),
        "horizons": list(config.EFFICACY_RETURN_HORIZONS),
        "benchmarks": list(config.BENCHMARK_INDICES.keys()),
        "all_time": {},
        "cohorts": {},
    }

    if tracker.empty:
        return result

    tracker = tracker.copy()

    for window_key in config.EFFICACY_CLASSIFICATION_WINDOWS.keys():
        bucket_col = f"{window_key}__BUCKET"
        anchor_col = f"{window_key}__ANCHOR_DATE"
        month_col = f"_{window_key}_ANCHOR_MONTH"
        tracker[month_col] = pd.to_datetime(tracker[anchor_col]).dt.strftime("%Y-%m")

        result["all_time"][window_key] = {}
        result["cohorts"][window_key] = {}

        for horizon in config.EFFICACY_RETURN_HORIZONS:
            result["all_time"][window_key][str(horizon)] = {}
            for bucket in BUCKET_ORDER:
                bucket_df = tracker[tracker[bucket_col] == bucket]
                result["all_time"][window_key][str(horizon)][str(bucket)] = {
                    short_key: _stats_for_group(bucket_df, window_key, horizon, short_key)
                    for short_key in config.BENCHMARK_INDICES.keys()
                }

        for horizon in config.EFFICACY_RETURN_HORIZONS:
            result["cohorts"][window_key][str(horizon)] = {}
            for bucket in BUCKET_ORDER:
                bucket_df = tracker[tracker[bucket_col] == bucket]
                if bucket_df.empty:
                    continue
                months = sorted(bucket_df[month_col].dropna().unique())
                bucket_cohorts = {}
                for month in months:
                    month_df = bucket_df[bucket_df[month_col] == month]
                    bucket_cohorts[month] = {
                        short_key: _stats_for_group(month_df, window_key, horizon, short_key)
                        for short_key in config.BENCHMARK_INDICES.keys()
                    }
                if bucket_cohorts:
                    result["cohorts"][window_key][str(horizon)][str(bucket)] = bucket_cohorts

    return result


def save_track_record() -> None:
    data = build_track_record()
    config.SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = config.SITE_DATA_DIR / "track_record.json"
    path.write_text(json.dumps(data, indent=2))
