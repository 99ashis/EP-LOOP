"""
Turns today's EP classification output (classifier.py's daily_output
DataFrame — only symbols that got a label today) into a queue of symbols
needing fundamental/news research.

Trigger rule: research fires whenever LABEL_CHANGED is True — i.e. today's
label is different from the last label that fired for this anchor (New,
or an upgrade to Sustained, or a fresh Persistent pivot, or a Fizzle).
A repeat of the same label two days running (e.g. Sustained again) does not
re-trigger — that's "still going," not new information.
"""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd

logger = logging.getLogger(__name__)

QUEUE_COLUMNS = [
    "SYMBOL", "TRIGGER_REASON_FOR_RESEARCH", "LABEL",
    "ANCHOR_DATE", "ANCHOR_CLOSE", "SESSIONS_SINCE_ANCHOR",
    "CLOSE", "PCT_MOVE_VS_PREV", "VOLUME_MULTIPLE",
]

_REASONS = {
    "NEW_EP": "New EP triggered — establish baseline fundamentals/news context",
    "PERSISTENT_EP": "Fresh Persistent pivot — check if fundamentals still support continued conviction",
    "RETRO_NEW_EP": "Episode re-anchored to an earlier pivot that outlasted the original — worth a fresh fundamentals check",
    "SUSTAINED_EP": "Upgraded to Sustained — check if fundamentals confirm the move is holding",
    "FIZZLE_OUT_EP": "Fizzled — check if the catalyst was real or noise",
}


def build_research_queue(daily_output: pd.DataFrame, as_of: date) -> pd.DataFrame:
    if daily_output.empty:
        return pd.DataFrame(columns=QUEUE_COLUMNS)

    changed = daily_output[daily_output["LABEL_CHANGED"] == True]  # noqa: E712
    if changed.empty:
        return pd.DataFrame(columns=QUEUE_COLUMNS)

    rows = []
    for _, row in changed.iterrows():
        rows.append({
            "SYMBOL": row["SYMBOL"],
            "TRIGGER_REASON_FOR_RESEARCH": _REASONS.get(row["LABEL"], "Status changed — review"),
            "LABEL": row["LABEL"],
            "ANCHOR_DATE": row["ANCHOR_DATE"],
            "ANCHOR_CLOSE": row["ANCHOR_CLOSE"],
            "SESSIONS_SINCE_ANCHOR": row["SESSIONS_SINCE_ANCHOR"],
            "CLOSE": row["CLOSE"],
            "PCT_MOVE_VS_PREV": row["PCT_MOVE_VS_PREV"],
            "VOLUME_MULTIPLE": row["VOLUME_MULTIPLE"],
        })

    queue = pd.DataFrame(rows, columns=QUEUE_COLUMNS)
    logger.info("Research queue built for %s: %d symbols flagged.", as_of, len(queue))
    return queue


def save_research_queue(queue: pd.DataFrame, as_of: date) -> None:
    from src import config
    path = config.RESEARCH_QUEUE_DIR / f"research_queue_{as_of.isoformat()}.csv"
    queue.to_csv(path, index=False)
    logger.info("Wrote research queue: %s", path)
