"""
Local-parquet persistence for the anchor tracking table (see classifier.py
for what an "anchor" is). This is the zero-setup default backend.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from src import config
from src.ep.classifier import ANCHOR_COLUMNS

STATE_COLUMNS = ANCHOR_COLUMNS  # kept as an alias so other modules can import one stable name


def load_state_as_of(as_of: date) -> pd.DataFrame:
    """
    Returns the anchor state as it stood immediately BEFORE `as_of` — the
    correct starting point for processing that date.

    Deliberately does NOT just read ep_state_latest.parquet — that file is
    "whatever was saved most recently," which is only correct if runs
    always happen in strict chronological order. Re-processing an earlier
    date after later ones have already run (exactly what happened during
    testing) would otherwise silently load a "future" state and corrupt
    the timeline. Instead, this looks up the most recent dated snapshot in
    history/ that's strictly before `as_of` — save_state() has always
    written one of those on every run, so this works retroactively too.
    """
    if not config.EP_STATE_HISTORY_DIR.exists():
        return pd.DataFrame(columns=ANCHOR_COLUMNS)

    as_of_str = as_of.isoformat()
    candidates = sorted(
        p for p in config.EP_STATE_HISTORY_DIR.glob("*.parquet")
        if p.stem < as_of_str
    )
    if not candidates:
        return pd.DataFrame(columns=ANCHOR_COLUMNS)

    return pd.read_parquet(candidates[-1])


def save_state(anchors_df: pd.DataFrame, as_of: date) -> None:
    anchors_df = anchors_df[ANCHOR_COLUMNS].copy() if not anchors_df.empty \
        else pd.DataFrame(columns=ANCHOR_COLUMNS)

    # ep_state_latest.parquet is kept only as a quick-glance convenience
    # file — nothing in the pipeline reads it for classification anymore.
    anchors_df.to_parquet(config.EP_STATE_LATEST, index=False)

    history_path = config.EP_STATE_HISTORY_DIR / f"{as_of.isoformat()}.parquet"
    anchors_df.to_parquet(history_path, index=False)
