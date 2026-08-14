"""
The anchor-generation EP engine — final spec, confirmed across many rounds
with the user. Core model:

  * Each symbol has at most one active "anchor generation" at a time: a
    NEW_EP (generation 1) or a RETRO_NEW_EP (generation 2, 3, 4, ...).
  * Every day, for a symbol with a valid anchor, Persistent / Sustained /
    Fizzle are evaluated FRESH against the CURRENT generation's anchor.
    Nothing about yesterday's label is remembered for the classification
    itself — only LAST_LABEL is kept, purely so the research-trigger layer
    can tell "did this change" without re-deriving it.
  * Each generation tracks, starting the moment its first Persistent fires,
    a "promotion candidate" — that first Persistent's own date/close/prev-
    close. This is captured once per generation and never overwritten.
  * A generation expires the instant it turns more than
    ANCHOR_ELIGIBILITY_WINDOW_SESSIONS (50) trading sessions old. At that
    moment:
      - If it has a promotion candidate: that day gets promoted into a
        RETRO_NEW_EP — a brand new anchor generation, own fresh 50-session
        clock, own fresh Persistent/Sustained counts. This can chain
        indefinitely, generation after generation, as long as each one
        manages to produce a Persistent before its own clock runs out.
      - If it never had a Persistent: the whole episode dies. That same
        day is checked completely fresh for a brand-new NEW_EP.
  * Promotion does NOT rewrite history — the promoted day's original
    PERSISTENT_EP row in the daily output stays exactly as it was.
  * A day where none of New/Persistent/Retro/Sustained/Fizzle fire simply
    doesn't appear in that day's output — the anchor keeps ticking silently.

Priority order when checking an active anchor: Persistent, then Sustained,
then Fizzle — and unlike earlier drafts of this spec, this is no longer
just a tie-break convention, it's now structurally enforced: Persistent
requires close >= 1.04x prev close, Sustained's move band requires
close < 1.04x prev close. Those can never both be true on the same day.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import pandas as pd

from src import config
from src.data import price_store
from src.ep.indicators import avg_volume
from src.ep.detector import scan_for_new_eps

logger = logging.getLogger(__name__)

ANCHOR_COLUMNS = [
    "SYMBOL", "ANCHOR_TYPE", "GENERATION", "ORIGIN_ANCHOR_DATE",
    "ANCHOR_DATE", "ANCHOR_CLOSE", "ANCHOR_PREV_CLOSE",
    "PERSISTENT_COUNT", "SUSTAINED_COUNT",
    "PROMOTION_CANDIDATE_DATE", "PROMOTION_CANDIDATE_CLOSE", "PROMOTION_CANDIDATE_PREV_CLOSE",
    "LAST_LABEL", "LAST_LABEL_DATE",
]

OUTPUT_COLUMNS = [
    "SYMBOL", "AS_OF_DATE", "LABEL", "LABEL_CHANGED", "GENERATION",
    "ANCHOR_DATE", "ANCHOR_CLOSE", "ANCHOR_PREV_CLOSE", "SESSIONS_SINCE_ANCHOR",
    "CLOSE", "PREV_CLOSE", "VOLUME", "AVG_VOLUME_50", "VOLUME_MULTIPLE", "PCT_MOVE_VS_PREV",
    "PERSISTENT_COUNT", "SUSTAINED_COUNT",
]


def _sessions_between(sorted_dates: list[pd.Timestamp], anchor_date, as_of_date) -> int | None:
    """Actual trading-session distance, inclusive of both ends. Not a simple
    day-counter, because RETRO_NEW_EP promotion can jump an anchor's date
    backward — the real calendar has to be consulted every time."""
    anchor_ts = pd.Timestamp(anchor_date)
    as_of_ts = pd.Timestamp(as_of_date)
    try:
        anchor_idx = sorted_dates.index(anchor_ts)
        as_of_idx = sorted_dates.index(as_of_ts)
    except ValueError:
        return None
    return as_of_idx - anchor_idx + 1


@dataclass
class _ActiveCheckResult:
    label: str | None
    volume_multiple: float | None
    pct_move_vs_prev: float | None
    avg_volume_50: float | None
    promotion_data: tuple | None  # (date, close, prev_close) if this fired a NEW first-persistent


def _check_active_anchor(symbol: str, anchor: pd.Series, today_row: pd.Series) -> _ActiveCheckResult:
    close = float(today_row["CLOSE"])
    volume = float(today_row["VOLUME"])
    prev_close = float(today_row.get("PREV_CLOSE", float("nan")))
    today_date = today_row["DATE"]

    if pd.isna(prev_close) or prev_close <= 0:
        history = price_store.load_symbol_history(symbol, as_of=today_date)
        prior_rows = history[history["DATE"] < pd.Timestamp(today_date)]
        if prior_rows.empty:
            return _ActiveCheckResult(None, None, None, None, None)
        prev_close = float(prior_rows.iloc[-1]["CLOSE"])

    history = price_store.load_symbol_history(symbol, as_of=today_date)
    vol_avg = avg_volume(history, config.VOLUME_BASELINE_LOOKBACK_SESSIONS)
    volume_multiple = volume / vol_avg if vol_avg and not pd.isna(vol_avg) and vol_avg > 0 else None
    pct_move = (close - prev_close) / prev_close * 100.0

    anchor_close = float(anchor["ANCHOR_CLOSE"])
    anchor_prev_close = float(anchor["ANCHOR_PREV_CLOSE"])

    # --- Persistent ---
    persistent = (
        volume_multiple is not None
        and volume_multiple >= config.PERSISTENT_EP_VOLUME_MULTIPLE
        and close > anchor_close
        and volume > config.PERSISTENT_EP_MIN_ABS_VOLUME
        and close >= prev_close * (1 + config.PERSISTENT_EP_PRICE_PCT_VS_PREV_CLOSE / 100.0)
    )
    if persistent:
        promo = None
        if pd.isna(anchor.get("PROMOTION_CANDIDATE_DATE")):
            # First Persistent of this generation — becomes the standing promotion candidate.
            promo = (pd.Timestamp(today_date), close, prev_close)
        return _ActiveCheckResult(config.STATUS_PERSISTENT, volume_multiple, round(pct_move, 2), vol_avg, promo)

    # --- Sustained ---
    sustained = (
        close > prev_close * (1 + config.SUSTAINED_EP_MOVE_MIN_PCT_VS_PREV / 100.0)
        and close < prev_close * (1 + config.SUSTAINED_EP_MOVE_MAX_PCT_VS_PREV / 100.0)
        and close >= anchor_close * (config.SUSTAINED_EP_PCT_OF_ANCHOR_CLOSE / 100.0)
    )
    if sustained:
        return _ActiveCheckResult(config.STATUS_SUSTAINED, volume_multiple, round(pct_move, 2), vol_avg, None)

    # --- Fizzle --- (vs. the close on the day before WHICHEVER anchor is currently active)
    fizzled = close < anchor_prev_close * (config.FIZZLE_PCT_OF_ANCHOR_PREV_CLOSE / 100.0)
    if fizzled:
        return _ActiveCheckResult(config.STATUS_FIZZLE, volume_multiple, round(pct_move, 2), vol_avg, None)

    return _ActiveCheckResult(None, volume_multiple, round(pct_move, 2), vol_avg, None)


def _fresh_anchor_row(symbol, trigger, generation=1, origin_date=None) -> dict:
    return {
        "SYMBOL": symbol,
        "ANCHOR_TYPE": config.ANCHOR_TYPE_NEW if generation == 1 else config.ANCHOR_TYPE_RETRO,
        "GENERATION": generation,
        "ORIGIN_ANCHOR_DATE": origin_date if origin_date is not None else pd.Timestamp(trigger.trade_date),
        "ANCHOR_DATE": pd.Timestamp(trigger.trade_date),
        "ANCHOR_CLOSE": trigger.close,
        "ANCHOR_PREV_CLOSE": trigger.prev_close,
        "PERSISTENT_COUNT": 0,
        "SUSTAINED_COUNT": 0,
        "PROMOTION_CANDIDATE_DATE": pd.NaT,
        "PROMOTION_CANDIDATE_CLOSE": None,
        "PROMOTION_CANDIDATE_PREV_CLOSE": None,
        "LAST_LABEL": config.STATUS_NEW if generation == 1 else config.STATUS_RETRO_NEW,
        "LAST_LABEL_DATE": pd.Timestamp(trigger.trade_date),
    }


def run_daily_classification(
    prior_anchors: pd.DataFrame,
    daily_bhavcopy: pd.DataFrame,
    as_of: date,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (updated_anchors_df, daily_output_df)."""
    price_lookup = daily_bhavcopy.set_index("SYMBOL")
    prior_by_symbol = (
        {r["SYMBOL"]: r for _, r in prior_anchors.iterrows()} if not prior_anchors.empty else {}
    )
    sorted_dates = price_store.list_trading_sessions()

    new_anchor_rows: list[dict] = []
    output_rows: list[dict] = []
    valid_anchor_symbols: set[str] = set()

    for symbol, anchor in prior_by_symbol.items():
        if symbol not in price_lookup.index:
            new_anchor_rows.append(anchor.to_dict())
            valid_anchor_symbols.add(symbol)
            continue

        today_row = price_lookup.loc[symbol]
        sessions = _sessions_between(sorted_dates, anchor["ANCHOR_DATE"], today_row["DATE"])

        if sessions is None or sessions > config.ANCHOR_ELIGIBILITY_WINDOW_SESSIONS:
            # This generation has expired.
            if pd.notna(anchor.get("PROMOTION_CANDIDATE_DATE")):
                # Promote: the first Persistent of this generation becomes a brand-new anchor.
                promo_date = anchor["PROMOTION_CANDIDATE_DATE"]
                promo_close = float(anchor["PROMOTION_CANDIDATE_CLOSE"])
                promo_prev_close = float(anchor["PROMOTION_CANDIDATE_PREV_CLOSE"])

                @dataclass
                class _PromoTrigger:
                    trade_date: object
                    close: float
                    prev_close: float

                new_anchor = _fresh_anchor_row(
                    symbol,
                    _PromoTrigger(promo_date, promo_close, promo_prev_close),
                    generation=int(anchor["GENERATION"]) + 1,
                    origin_date=anchor["ORIGIN_ANCHOR_DATE"],
                )
                new_anchor_rows.append(new_anchor)
                valid_anchor_symbols.add(symbol)

                promo_sessions = _sessions_between(sorted_dates, promo_date, today_row["DATE"])
                output_rows.append({
                    "SYMBOL": symbol,
                    "AS_OF_DATE": pd.Timestamp(as_of),
                    "LABEL": config.STATUS_RETRO_NEW,
                    "LABEL_CHANGED": True,
                    "GENERATION": new_anchor["GENERATION"],
                    "ANCHOR_DATE": promo_date,
                    "ANCHOR_CLOSE": promo_close,
                    "ANCHOR_PREV_CLOSE": promo_prev_close,
                    "SESSIONS_SINCE_ANCHOR": promo_sessions,
                    "CLOSE": float(today_row["CLOSE"]),
                    "PREV_CLOSE": float(today_row.get("PREV_CLOSE", float("nan"))),
                    "VOLUME": float(today_row["VOLUME"]),
                    "AVG_VOLUME_50": None,
                    "VOLUME_MULTIPLE": None,
                    "PCT_MOVE_VS_PREV": None,
                    "PERSISTENT_COUNT": 0,
                    "SUSTAINED_COUNT": 0,
                })
                # Promotion day does not also get checked against the new anchor
                # today — that starts from the next trading day, same as how a
                # NEW_EP's own trigger day never also shows a same-day Persistent.
                continue
            else:
                # No Persistent ever happened this generation. Episode dies.
                # Falls through to the fresh NEW_EP check below.
                pass
        else:
            # Still valid — evaluate today against the current anchor.
            result = _check_active_anchor(symbol, anchor, today_row)

            persistent_count = int(anchor["PERSISTENT_COUNT"])
            sustained_count = int(anchor["SUSTAINED_COUNT"])
            promo_date = anchor.get("PROMOTION_CANDIDATE_DATE")
            promo_close = anchor.get("PROMOTION_CANDIDATE_CLOSE")
            promo_prev_close = anchor.get("PROMOTION_CANDIDATE_PREV_CLOSE")

            if result.label == config.STATUS_PERSISTENT:
                persistent_count += 1
                if result.promotion_data is not None:
                    promo_date, promo_close, promo_prev_close = result.promotion_data
            elif result.label == config.STATUS_SUSTAINED:
                sustained_count += 1

            updated_anchor = {
                "SYMBOL": symbol,
                "ANCHOR_TYPE": anchor["ANCHOR_TYPE"],
                "GENERATION": anchor["GENERATION"],
                "ORIGIN_ANCHOR_DATE": anchor["ORIGIN_ANCHOR_DATE"],
                "ANCHOR_DATE": anchor["ANCHOR_DATE"],
                "ANCHOR_CLOSE": anchor["ANCHOR_CLOSE"],
                "ANCHOR_PREV_CLOSE": anchor["ANCHOR_PREV_CLOSE"],
                "PERSISTENT_COUNT": persistent_count,
                "SUSTAINED_COUNT": sustained_count,
                "PROMOTION_CANDIDATE_DATE": promo_date,
                "PROMOTION_CANDIDATE_CLOSE": promo_close,
                "PROMOTION_CANDIDATE_PREV_CLOSE": promo_prev_close,
                "LAST_LABEL": result.label if result.label is not None else anchor.get("LAST_LABEL"),
                "LAST_LABEL_DATE": pd.Timestamp(as_of) if result.label is not None else anchor.get("LAST_LABEL_DATE"),
            }
            new_anchor_rows.append(updated_anchor)
            valid_anchor_symbols.add(symbol)

            if result.label is not None:
                output_rows.append({
                    "SYMBOL": symbol,
                    "AS_OF_DATE": pd.Timestamp(as_of),
                    "LABEL": result.label,
                    "LABEL_CHANGED": result.label != anchor.get("LAST_LABEL"),
                    "GENERATION": anchor["GENERATION"],
                    "ANCHOR_DATE": anchor["ANCHOR_DATE"],
                    "ANCHOR_CLOSE": anchor["ANCHOR_CLOSE"],
                    "ANCHOR_PREV_CLOSE": anchor["ANCHOR_PREV_CLOSE"],
                    "SESSIONS_SINCE_ANCHOR": sessions,
                    "CLOSE": float(today_row["CLOSE"]),
                    "PREV_CLOSE": float(today_row.get("PREV_CLOSE", float("nan"))),
                    "VOLUME": float(today_row["VOLUME"]),
                    "AVG_VOLUME_50": result.avg_volume_50,
                    "VOLUME_MULTIPLE": result.volume_multiple,
                    "PCT_MOVE_VS_PREV": result.pct_move_vs_prev,
                    "PERSISTENT_COUNT": persistent_count,
                    "SUSTAINED_COUNT": sustained_count,
                })
            continue

    # Everyone else — no anchor at all, or one that just died with no promotion — is
    # eligible to be checked fresh for a brand-new NEW_EP.
    all_symbols_today = set(daily_bhavcopy["SYMBOL"])
    eligible_for_new = all_symbols_today - valid_anchor_symbols
    new_triggers = scan_for_new_eps(daily_bhavcopy, eligible_for_new)

    for symbol, trigger in new_triggers.items():
        new_anchor_rows.append(_fresh_anchor_row(symbol, trigger, generation=1))
        output_rows.append({
            "SYMBOL": symbol,
            "AS_OF_DATE": pd.Timestamp(as_of),
            "LABEL": config.STATUS_NEW,
            "LABEL_CHANGED": True,
            "GENERATION": 1,
            "ANCHOR_DATE": pd.Timestamp(trigger.trade_date),
            "ANCHOR_CLOSE": trigger.close,
            "ANCHOR_PREV_CLOSE": trigger.prev_close,
            "SESSIONS_SINCE_ANCHOR": 1,
            "CLOSE": trigger.close,
            "PREV_CLOSE": trigger.prev_close,
            "VOLUME": trigger.volume,
            "AVG_VOLUME_50": trigger.avg_volume_50,
            "VOLUME_MULTIPLE": trigger.volume_multiple,
            "PCT_MOVE_VS_PREV": trigger.pct_move_vs_prev,
            "PERSISTENT_COUNT": 0,
            "SUSTAINED_COUNT": 0,
        })

    updated_anchors = pd.DataFrame(new_anchor_rows, columns=ANCHOR_COLUMNS) if new_anchor_rows \
        else pd.DataFrame(columns=ANCHOR_COLUMNS)
    daily_output = pd.DataFrame(output_rows, columns=OUTPUT_COLUMNS) if output_rows \
        else pd.DataFrame(columns=OUTPUT_COLUMNS)

    logger.info(
        "Classification complete for %s: %d active anchors, %d labeled today (%d new).",
        as_of, len(updated_anchors), len(daily_output), len(new_triggers),
    )
    return updated_anchors, daily_output
