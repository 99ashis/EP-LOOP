"""
Classifies a matured NEW_EP event into one of 9 buckets, based on what
happened to that symbol in the 10 trading sessions afterward.

Deliberately NOT re-derivable from first principles by reading code alone —
the priority rules came from specific confirmed decisions, not obvious
defaults:
  - A Fizzle occurring ANYWHERE in the 10-day window overrides everything
    else, even a Persistent/Sustained that fired earlier.
  - Both Persistent AND Sustained occurring (no Fizzle) is its own bucket
    (9, "mixed"), anchored to whichever of the two happened LAST.
  - "Up to two" means EXACTLY two — the 9 buckets are mutually exclusive,
    not cumulative/overlapping.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from src import config


@dataclass
class ClassificationResult:
    bucket: int
    anchor_date: pd.Timestamp
    anchor_close: float


def classify(
    symbol: str,
    new_ep_date: date,
    new_ep_close: float,
    events_in_window: pd.DataFrame,
) -> ClassificationResult:
    """
    `events_in_window` must already be filtered to this symbol's
    PERSISTENT_EP / SUSTAINED_EP / FIZZLE_OUT_EP rows strictly within the
    10-trading-session window after new_ep_date, sorted by DATE ascending,
    with SYMBOL/DATE/LABEL/CLOSE columns. Filtering is the caller's job
    (see efficacy/pipeline.py) — this function only implements the
    priority logic once that window is correctly assembled.
    """
    fizzles = events_in_window[events_in_window["LABEL"] == "FIZZLE_OUT_EP"]
    if not fizzles.empty:
        first = fizzles.iloc[0]
        return ClassificationResult(config.BUCKET_FIZZLE, first["DATE"], float(first["CLOSE"]))

    persistents = events_in_window[events_in_window["LABEL"] == "PERSISTENT_EP"]
    sustaineds = events_in_window[events_in_window["LABEL"] == "SUSTAINED_EP"]
    p, s = len(persistents), len(sustaineds)

    if p > 0 and s > 0:
        last_p, last_s = persistents.iloc[-1], sustaineds.iloc[-1]
        last = last_p if last_p["DATE"] >= last_s["DATE"] else last_s
        return ClassificationResult(config.BUCKET_MIXED, last["DATE"], float(last["CLOSE"]))

    if p > 0:
        if p == 1:
            row = persistents.iloc[0]
            return ClassificationResult(config.BUCKET_PERSISTENT_1, row["DATE"], float(row["CLOSE"]))
        if p == 2:
            row = persistents.iloc[1]
            return ClassificationResult(config.BUCKET_PERSISTENT_2, row["DATE"], float(row["CLOSE"]))
        row = persistents.iloc[2]
        return ClassificationResult(config.BUCKET_PERSISTENT_3PLUS, row["DATE"], float(row["CLOSE"]))

    if s > 0:
        if s == 1:
            row = sustaineds.iloc[0]
            return ClassificationResult(config.BUCKET_SUSTAINED_1, row["DATE"], float(row["CLOSE"]))
        if s == 2:
            row = sustaineds.iloc[1]
            return ClassificationResult(config.BUCKET_SUSTAINED_2, row["DATE"], float(row["CLOSE"]))
        row = sustaineds.iloc[2]
        return ClassificationResult(config.BUCKET_SUSTAINED_3PLUS, row["DATE"], float(row["CLOSE"]))

    return ClassificationResult(config.BUCKET_PURE_NEW, pd.Timestamp(new_ep_date), new_ep_close)
