"""
Turns today's EP classification output (classifier.py's daily_output
DataFrame — only symbols that got a label today) into a queue of symbols
needing fundamental research.

Two independent trigger rules feed the same queue, and a symbol can be
queued by either one (or both, in which case it's still only queued once
— see build_research_queue's dedup at the end):

RULE 1 — same-day count-based trigger (original, unchanged):
Research fires on exactly two count-based events, per anchor GENERATION
(not per calendar day):
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

RULE 2 — window-bucket trigger (new): fires when a symbol's 5D or 10D
efficacy-classification window (src/efficacy/pipeline.py) resolves, for
the FIRST time, to one of the "repeat-confirmed" buckets:
  - BUCKET_PERSISTENT_2   (3 — exactly 2 Persistent occurrences in-window)
  - BUCKET_PERSISTENT_3PLUS (4 — 3+ Persistent occurrences in-window)
  - BUCKET_SUSTAINED_3PLUS  (7 — 3+ Sustained occurrences in-window)
This is deliberately narrower than "every Persistent/Sustained event" —
the efficacy Track Record shows BUCKET_PERSISTENT_1 (a symbol's first,
single Persistent occurrence within its window) is consistently the
weakest-performing bucket, while these three repeat-confirmed buckets
are where the (still-thin) positive excess return concentrates. Rule 2
is a LAGGING trigger relative to Rule 1 — a window can only resolve once
window_sessions have elapsed since the anchor (5 or 10 sessions), so it
queues research days after the anchor, once the pattern has actually
repeated, rather than same-day on the first occurrence.

Window-bucket queue rows carry the anchor's real ANCHOR_DATE/ANCHOR_CLOSE
from the efficacy tracker, but — unlike Rule 1's rows, which come from
today's daily_output and so have today's CLOSE/PCT_MOVE_VS_PREV/
VOLUME_MULTIPLE on hand — a window can resolve on a day the symbol has
no price-action event at all. Those three fields are left null for
Rule-2-only rows rather than guessed at; nothing downstream (fetch_queue.py,
fundamental_analysis.py) depends on them being populated.

NEW_EP, RETRO_NEW_EP, and FIZZLE_OUT_EP never trigger fundamental
research under either rule (deliberately narrower than results-timing
enrichment's eligible-labels set in config.py, which is a separate,
unrelated scope decision — don't conflate the two).
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

# --- Rule 2: window-bucket trigger -----------------------------------------
# Only these two windows feed Rule 2 — 15D/20D are excluded on purpose
# (kept as pure measurement windows for now, not wired to research spend).
WINDOW_BUCKET_TRIGGER_WINDOWS = {"5D", "10D"}

# bucket -> (display LABEL, reason template). Display LABEL matches the
# underlying EP status so Telegram/dashboard formatting (which keys off
# LABEL) reads naturally, even though the actual trigger is window-bucket-
# based rather than same-day-count-based.
WINDOW_BUCKET_TRIGGERS = {
    config.BUCKET_PERSISTENT_2: (
        "PERSISTENT_EP",
        "{window} window resolved to Persistent x2 — 2nd Persistent occurrence confirmed within {window} of the anchor",
    ),
    config.BUCKET_PERSISTENT_3PLUS: (
        "PERSISTENT_EP",
        "{window} window resolved to Persistent x3+ — 3rd+ Persistent occurrence confirmed within {window} of the anchor",
    ),
    config.BUCKET_SUSTAINED_3PLUS: (
        "SUSTAINED_EP",
        "{window} window resolved to Sustained x3+ — 3rd+ Sustained occurrence confirmed within {window} of the anchor",
    ),
}


def _rule1_count_based_queue(daily_output: pd.DataFrame) -> pd.DataFrame:
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
    return pd.DataFrame(rows, columns=QUEUE_COLUMNS)


def _rule2_window_bucket_queue(resolved_windows: pd.DataFrame) -> pd.DataFrame:
    if resolved_windows is None or resolved_windows.empty:
        return pd.DataFrame(columns=QUEUE_COLUMNS)

    qualifying = resolved_windows[
        resolved_windows["WINDOW_KEY"].isin(WINDOW_BUCKET_TRIGGER_WINDOWS)
        & resolved_windows["BUCKET"].isin(WINDOW_BUCKET_TRIGGERS.keys())
    ]
    if qualifying.empty:
        return pd.DataFrame(columns=QUEUE_COLUMNS)

    rows = []
    for _, row in qualifying.iterrows():
        label, reason_template = WINDOW_BUCKET_TRIGGERS[row["BUCKET"]]
        rows.append({
            "SYMBOL": row["SYMBOL"],
            "TRIGGER_REASON_FOR_RESEARCH": reason_template.format(window=row["WINDOW_KEY"]),
            "LABEL": label,
            "ANCHOR_DATE": row["ANCHOR_DATE"],
            "ANCHOR_CLOSE": row["ANCHOR_CLOSE"],
            # Not available for a window-bucket resolution (it doesn't
            # necessarily land on a day the symbol has a price-action
            # event) — left null rather than guessed at. Nothing
            # downstream requires these three to be populated.
            "SESSIONS_SINCE_ANCHOR": None,
            "CLOSE": None,
            "PCT_MOVE_VS_PREV": None,
            "VOLUME_MULTIPLE": None,
        })
    return pd.DataFrame(rows, columns=QUEUE_COLUMNS)


def build_research_queue(
    daily_output: pd.DataFrame,
    as_of: date,
    resolved_windows: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    daily_output: today's EP classification output (Rule 1 source).
    resolved_windows: windows that resolved during today's efficacy run —
        see src/efficacy/pipeline.py's run_efficacy_daily return value
        (Rule 2 source). Optional and backward-compatible: omitting it
        (or passing None/empty) runs Rule 1 only, exactly as before.
    """
    rule1 = _rule1_count_based_queue(daily_output)
    rule2 = _rule2_window_bucket_queue(resolved_windows)

    if rule1.empty and rule2.empty:
        return pd.DataFrame(columns=QUEUE_COLUMNS)

    queue = pd.concat([rule1, rule2], ignore_index=True)

    # A symbol can qualify under both rules on the same day (e.g. its 3rd
    # Sustained fires today's count-based rule AND, independently, a 10D
    # window resolves to Sustained x3+ today too). One research run per
    # symbol per day is enough — keep Rule 1's row when both fire, since
    # it carries richer today's-price-action fields; Rule 2 still gets
    # its own row whenever it fires alone.
    queue = queue.drop_duplicates(subset="SYMBOL", keep="first").reset_index(drop=True)

    logger.info("Research queue built for %s: %d symbol(s) (%d via count-trigger, %d via window-bucket-trigger).",
                as_of, len(queue), len(rule1), len(rule2))
    return queue


def save_research_queue(queue: pd.DataFrame, as_of: date) -> None:
    from src import config
    path = config.RESEARCH_QUEUE_DIR / f"research_queue_{as_of.isoformat()}.csv"
    queue.to_csv(path, index=False)
    logger.info("Wrote research queue: %s", path)
