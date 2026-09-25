"""
Per-stock audit view for the efficacy study.

Purpose: let a person look up ONE symbol and see, in one place, everything
the system has decided about it — which window(s) it's classified under,
what bucket each window landed on, and (new in this version) the stock's
OWN matured return and excess-return numbers for every horizon/benchmark
combination the pipeline has computed so far. Before this, the only place
those numbers existed was blended into Track Record's monthly cohort
aggregates, which made it impossible to check a single stock's number
without guessing which cohort row it fell into. This view removes that
guesswork: every figure here is read directly off that stock's own tracker
row, nothing is blended or inferred.

Design note on "matured": a window field is only ever written by
pipeline.py once enough trading sessions have actually elapsed — so
"matured" here just means "the value is present, not NaN." We also try to
compute an expected maturity date (anchor_date + horizon trading sessions)
for anything still pending, purely as a courtesy for the UI; if the trading
calendar isn't available for some reason, we just omit that field rather
than fail the whole audit build.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any

import pandas as pd

from src import config
from src.efficacy import events_log, tracker_store

BUCKET_LABELS = {
    config.BUCKET_PURE_NEW: "Pure New",
    config.BUCKET_PERSISTENT_1: "Persistent x1",
    config.BUCKET_PERSISTENT_2: "Persistent x2",
    config.BUCKET_PERSISTENT_3PLUS: "Persistent x3+",
    config.BUCKET_SUSTAINED_1: "Sustained x1",
    config.BUCKET_SUSTAINED_2: "Sustained x2",
    config.BUCKET_SUSTAINED_3PLUS: "Sustained x3+",
    config.BUCKET_FIZZLE: "Fizzle Out",
    config.BUCKET_MIXED: "Mixed",
}

TRIGGER_LABELS = [config.STATUS_PERSISTENT, config.STATUS_SUSTAINED, config.STATUS_FIZZLE]

AUDIT_OUTPUT_PATH = config.SITE_DATA_DIR / "efficacy_audit.json"


def _detect_windows(tracker: pd.DataFrame) -> list[str]:
    """Which configured windows actually have columns present on this tracker."""
    return [
        window_key
        for window_key in config.EFFICACY_CLASSIFICATION_WINDOWS.keys()
        if f"{window_key}__BUCKET" in tracker.columns
    ]


def _iso(value) -> str | None:
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def _num(value) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _expected_maturity_date(anchor_date, horizon: int) -> str | None:
    """Best-effort: anchor_date + `horizon` trading sessions, for a pending horizon."""
    try:
        from src.data import price_store  # local import: optional dependency for this feature
    except Exception:
        return None
    try:
        sorted_dates = price_store.list_trading_sessions()
        anchor_idx = sorted_dates.index(pd.Timestamp(anchor_date))
        return _iso(sorted_dates[anchor_idx + horizon])
    except Exception:
        return None


def _build_window_returns(row: pd.Series, window_key: str) -> dict[str, Any]:
    anchor_date = row.get(f"{window_key}__ANCHOR_DATE")
    has_anchor = anchor_date is not None and not pd.isna(anchor_date)

    returns: dict[str, Any] = {}
    for horizon in config.EFFICACY_RETURN_HORIZONS:
        ret_col = f"{window_key}__RETURN_{horizon}"
        stock_return = _num(row.get(ret_col)) if ret_col in row else None
        matured = stock_return is not None

        benchmarks: dict[str, Any] = {}
        for short_key, display_name in config.BENCHMARK_INDICES.items():
            b_ret_col = f"{window_key}__{short_key}_RETURN_{horizon}"
            excess_col = f"{window_key}__EXCESS_RETURN_{short_key}_{horizon}"
            benchmarks[short_key] = {
                "label": display_name,
                "benchmark_return": _num(row.get(b_ret_col)) if b_ret_col in row else None,
                "excess_return": _num(row.get(excess_col)) if excess_col in row else None,
            }

        entry: dict[str, Any] = {
            "matured": matured,
            "stock_return": stock_return,
            "benchmarks": benchmarks,
        }
        if not matured and has_anchor:
            expected = _expected_maturity_date(anchor_date, horizon)
            if expected is not None:
                entry["expected_maturity_date"] = expected
        returns[str(horizon)] = entry

    return returns


def _build_stock_windows(row: pd.Series, window_keys: list[str]) -> dict[str, Any]:
    windows: dict[str, Any] = {}
    for window_key in window_keys:
        bucket_val = row.get(f"{window_key}__BUCKET")
        bucket = None if bucket_val is None or pd.isna(bucket_val) else int(bucket_val)
        windows[window_key] = {
            "bucket": bucket,
            "bucket_label": BUCKET_LABELS.get(bucket) if bucket is not None else None,
            "anchor_date": _iso(row.get(f"{window_key}__ANCHOR_DATE")),
            "anchor_close": _num(row.get(f"{window_key}__ANCHOR_CLOSE")),
            "returns": _build_window_returns(row, window_key),
        }
    return windows


def _build_stock_events(symbol: str, events: pd.DataFrame, new_ep_date, window_keys: list[str],
                         window_sessions: dict[str, int], anchor_dates: dict[str, Any]) -> list[dict[str, Any]]:
    if events.empty:
        return []
    stock_events = events[
        (events["SYMBOL"] == symbol) & (events["LABEL"].isin(TRIGGER_LABELS))
    ].sort_values("DATE")

    out = []
    for _, ev in stock_events.iterrows():
        ev_date = ev["DATE"]
        inside_windows = []
        for window_key in window_keys:
            anchor_date = anchor_dates.get(window_key)
            # An event is "inside" a window if it falls within that window's
            # classification span, i.e. between NEW_EP_DATE (exclusive) and
            # the window's own anchor date (inclusive) — mirrors the boundary
            # pipeline.py itself uses when it classifies each window.
            if pd.isna(new_ep_date):
                continue
            if ev_date <= pd.Timestamp(new_ep_date):
                continue
            if anchor_date is not None and not pd.isna(anchor_date) and ev_date <= pd.Timestamp(anchor_date):
                inside_windows.append(window_key)
        out.append({
            "date": _iso(ev_date),
            "label": ev["LABEL"],
            "close": _num(ev.get("CLOSE")),
            "inside_windows": inside_windows,
        })
    return out


def build_audit(tracker: pd.DataFrame | None = None, events: pd.DataFrame | None = None) -> dict[str, Any]:
    if tracker is None:
        tracker = tracker_store.load_tracker()
    if events is None:
        events = events_log.load_events()

    window_keys = _detect_windows(tracker)
    window_sessions = {w: config.EFFICACY_CLASSIFICATION_WINDOWS[w] for w in window_keys}

    stocks: dict[str, Any] = {}
    for _, row in tracker.iterrows():
        symbol = row["SYMBOL"]
        anchor_dates = {w: row.get(f"{w}__ANCHOR_DATE") for w in window_keys}
        stocks[symbol] = {
            "new_ep_date": _iso(row.get("NEW_EP_DATE")),
            "new_ep_close": _num(row.get("NEW_EP_CLOSE")),
            "windows": _build_stock_windows(row, window_keys),
            "events": _build_stock_events(
                symbol, events, row.get("NEW_EP_DATE"), window_keys, window_sessions, anchor_dates,
            ),
        }

    return {"windows": window_keys, "stocks": stocks}


def save_audit(tracker: pd.DataFrame | None = None, events: pd.DataFrame | None = None,
                path=None) -> None:
    audit = build_audit(tracker=tracker, events=events)
    out_path = path or AUDIT_OUTPUT_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(audit, f, indent=2)
