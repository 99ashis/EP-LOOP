"""
Turns today's EP classification output (classifier.py's daily_output
DataFrame — only symbols that got a label today) into a queue of symbols
needing fundamental research.

Trigger rule (confirmed): research fires only when today's label is
PERSISTENT_EP AND LABEL_CHANGED is True — i.e. a fresh Persistent pivot.
SUSTAINED_EP, NEW_EP, RETRO_NEW_EP, and FIZZLE_OUT_EP do NOT trigger
fundamental research (deliberately narrower than results-timing
enrichment's eligible-labels set in config.py, which is a separate,
unrelated scope decision — don't conflate the two).
A repeat of the same label two days running does not re-trigger — that's
"still going," not new information.
"""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from src import config

logger = logging.getLogger(__name__)

QUEUE_COLUMNS = [
    "SYMBOL", "TRIGGER_REASON_FOR_RESEARCH", "LABEL",
    "ANCHOR_DATE", "ANCHOR_CLOSE", "SESSIONS_SINCE_ANCHOR",
    "CLOSE", "PCT_MOVE_VS_PREV", "VOLUME_MULTIPLE",
]

# Only this label ever queues fundamental research (confirmed: narrowed
# from Persistent+Sustained to Persistent only). Kept as a set (rather
# than inline) so it's easy to find/change in one place if this scope
# decision ever gets revisited again.
RESEARCH_TRIGGER_LABELS = {config.STATUS_PERSISTENT}

_REASONS = {
    "PERSISTENT_EP": "Fresh Persistent pivot — check if fundamentals still support continued conviction",
}


def build_research_queue(daily_output: pd.DataFrame, as_of: date) -> pd.DataFrame:
    if daily_output.empty:
        return pd.DataFrame(columns=QUEUE_COLUMNS)

    changed = daily_output[
        (daily_output["LABEL_CHANGED"] == True)  # noqa: E712
        & (daily_output["LABEL"].isin(RESEARCH_TRIGGER_LABELS))
    ]
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
