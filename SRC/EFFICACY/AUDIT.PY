"""
Per-symbol audit trail: for each tracked NEW_EP, shows its classification
under every window PLUS the actual trigger events (dates, labels, closes)
that drove each classification — with every event marked for exactly which
window(s) it falls inside. Built specifically to answer "why is this stock
in this bucket" precisely, rather than guessing from aggregate stats.

Works with EITHER tracker schema: the old single-window one (plain BUCKET
column) or the new window-prefixed one (10D__BUCKET etc) — auto-detected,
same principle as tracker_store.py's own migration logic.
"""
from __future__ import annotations

import json

import pandas as pd

from src import config
from src.data import price_store
from src.efficacy import tracker_store, events_log

BUCKET_LABELS = {
    1: "Pure New", 2: "Persistent x1", 3: "Persistent x2", 4: "Persistent x3+",
    5: "Sustained x1", 6: "Sustained x2", 7: "Sustained x3+", 8: "Fizzle", 9: "Mixed",
}

TRIGGER_LABELS = ["PERSISTENT_EP", "SUSTAINED_EP", "FIZZLE_OUT_EP"]


def _detect_windows(tracker: pd.DataFrame) -> dict[str, int]:
    """Old schema (plain BUCKET column, no window prefix) is reported as a
    single implicit "10D" window — matching what it always was under the
    hood before multi-window classification existed."""
    if "BUCKET" in tracker.columns:
        return {"10D": 10}
    return dict(config.EFFICACY_CLASSIFICATION_WINDOWS)


def build_audit(tracker: pd.DataFrame | None = None, events: pd.DataFrame | None = None) -> dict:
    """`tracker`/`events` can be passed directly for testing; default to
    loading from disk (the real daily-run path)."""
    if tracker is None:
        tracker = tracker_store.load_tracker()
    if events is None:
        events = events_log.load_events_log()

    sorted_dates = price_store.list_trading_sessions()
    windows = _detect_windows(tracker)
    is_old_schema = "BUCKET" in tracker.columns

    result = {"windows": list(windows.keys()), "stocks": {}}

    for _, row in tracker.iterrows():
        symbol = row["SYMBOL"]
        new_ep_date = pd.Timestamp(row["NEW_EP_DATE"])

        try:
            new_ep_idx = sorted_dates.index(new_ep_date)
        except ValueError:
            new_ep_idx = None

        window_bounds: dict[str, pd.Timestamp | None] = {}
        window_results = {}
        for wkey, wsessions in windows.items():
            if new_ep_idx is not None and new_ep_idx + wsessions < len(sorted_dates):
                window_bounds[wkey] = sorted_dates[new_ep_idx + wsessions]
            else:
                window_bounds[wkey] = None

            if is_old_schema:
                bucket, anchor_date, anchor_close = row.get("BUCKET"), row.get("ANCHOR_DATE"), row.get("ANCHOR_CLOSE")
            else:
                bucket = row.get(f"{wkey}__BUCKET")
                anchor_date = row.get(f"{wkey}__ANCHOR_DATE")
                anchor_close = row.get(f"{wkey}__ANCHOR_CLOSE")

            window_results[wkey] = {
                "bucket": None if pd.isna(bucket) else int(bucket),
                "bucket_label": None if pd.isna(bucket) else BUCKET_LABELS.get(int(bucket)),
                "anchor_date": None if pd.isna(anchor_date) else pd.Timestamp(anchor_date).strftime("%Y-%m-%d"),
                "anchor_close": None if pd.isna(anchor_close) else float(anchor_close),
            }

        sym_events = events[
            (events["SYMBOL"] == symbol)
            & (events["DATE"] > new_ep_date)
            & (events["LABEL"].isin(TRIGGER_LABELS))
        ].sort_values("DATE")

        event_list = []
        for _, e in sym_events.iterrows():
            e_date = pd.Timestamp(e["DATE"])
            inside = [wkey for wkey, bound in window_bounds.items() if bound is not None and e_date <= bound]
            event_list.append({
                "date": e_date.strftime("%Y-%m-%d"),
                "label": e["LABEL"],
                "close": float(e["CLOSE"]),
                "inside_windows": inside,
            })

        result["stocks"][symbol] = {
            "new_ep_date": new_ep_date.strftime("%Y-%m-%d"),
            "new_ep_close": float(row["NEW_EP_CLOSE"]),
            "windows": window_results,
            "events": event_list,
        }

    return result


def save_audit() -> None:
    data = build_audit()
    config.SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = config.SITE_DATA_DIR / "efficacy_audit.json"
    path.write_text(json.dumps(data, indent=2))
