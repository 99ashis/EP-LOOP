"""
Turns today's EP classification output (classifier.py's daily_output
DataFrame — only symbols that got a label today) into a queue of symbols
needing fundamental research.

Trigger rule (confirmed): research fires on exactly two count-based
events, per anchor GENERATION (not per calendar day):
  - the 1st PERSISTENT_EP occurrence for this generation (PERSISTENT_COUNT == 1)
  - the 3rd SUSTAINED_EP occurrence for this generation (SUSTAINED_COUNT == 3)
PERSISTENT_COUNT / SUSTAINED_COUNT are running counts classifier.py
already tracks per anchor generation — they increment every day that
label fires and never reset mid-generation (only reset to 0 when a fresh
generation starts, including a Retro_New_EP promotion), so each of these
conditions is a one-time event within a given generation by construction.
A promoted generation gets its own fresh counts and can independently
trigger its own 1st-Persistent / 3rd-Sustained, separate from the
original generation's.
NEW_EP, RETRO_NEW_EP, and FIZZLE_OUT_EP never trigger fundamental
research (deliberately narrower than results-timing enrichment's
eligible-labels set in config.py, which is a separate, unrelated scope
decision — don't conflate the two).
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

# The exact count each label must hit to trigger — kept as named
# constants (rather than inline literals) so this is easy to find/change
# in one place if the trigger points ever get revisited again.
PERSISTENT_TRIGGER_COUNT = 1
SUSTAINED_TRIGGER_COUNT = 3

_REASONS = {
    "PERSISTENT_EP": "1st Persistent pivot for this anchor — check if fundamentals support continued conviction",
    "SUSTAINED_EP": "3rd Sustained occurrence for this anchor — check if fundamentals confirm the move is holding",
}


def build_research_queue(daily_output: pd.DataFrame, as_of: date) -> pd.DataFrame:
    if daily_output.empty:
        return pd.DataFrame(columns=QUEUE_COLUMNS)

    changed = daily_output[
        ((daily_output["LABEL"] == config.STATUS_PERSISTENT) & (daily_output["PERSISTENT_COUNT"] == PERSISTENT_TRIGGER_COUNT))
        | ((daily_output["LABEL"] == config.STATUS_SUSTAINED) & (daily_output["SUSTAINED_COUNT"] == SUSTAINED_TRIGGER_COUNT))
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
