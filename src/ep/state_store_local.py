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


def load_latest_state() -> pd.DataFrame:
    if not config.EP_STATE_LATEST.exists():
        return pd.DataFrame(columns=ANCHOR_COLUMNS)
    return pd.read_parquet(config.EP_STATE_LATEST)


def save_state(anchors_df: pd.DataFrame, as_of: date) -> None:
    anchors_df = anchors_df[ANCHOR_COLUMNS].copy() if not anchors_df.empty \
        else pd.DataFrame(columns=ANCHOR_COLUMNS)
    anchors_df.to_parquet(config.EP_STATE_LATEST, index=False)

    history_path = config.EP_STATE_HISTORY_DIR / f"{as_of.isoformat()}.parquet"
    anchors_df.to_parquet(history_path, index=False)
