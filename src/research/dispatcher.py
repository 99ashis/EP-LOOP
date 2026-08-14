"""
Runs the research stage (fundamental + news) for every symbol in today's
queue, and writes a combined output file.

Fundamental and news results are kept as separate fields in the same record
here, but Phase 3's Telegram sender will split them into two separate
messages per your original spec ("fundamental update sent separately").
Keeping them together in the output file is just easier for auditing.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import date

import pandas as pd

from src import config
from src.research import fundamental_analysis, news_analysis

logger = logging.getLogger(__name__)


def run_research_for_queue(queue: pd.DataFrame, as_of: date) -> list[dict]:
    if queue.empty:
        logger.info("Research queue empty for %s — nothing to research.", as_of)
        return []

    results = []
    for _, row in queue.iterrows():
        context = row.to_dict()
        symbol = row["SYMBOL"]

        try:
            fundamental = fundamental_analysis.analyze(symbol, context)
        except Exception:  # noqa: BLE001
            logger.exception("Fundamental analysis failed for %s", symbol)
            fundamental = None

        try:
            news = news_analysis.analyze(symbol, context)
        except Exception:  # noqa: BLE001
            logger.exception("News analysis failed for %s", symbol)
            news = None

        results.append({
            "symbol": symbol,
            "as_of": as_of.isoformat(),
            "ep_context": {
                "label": row["LABEL"],
                "trigger_reason_for_research": row["TRIGGER_REASON_FOR_RESEARCH"],
                "anchor_date": str(row["ANCHOR_DATE"]),
                "sessions_since_anchor": int(row["SESSIONS_SINCE_ANCHOR"]),
                "pct_move_vs_prev": row["PCT_MOVE_VS_PREV"],
            },
            "fundamental": asdict(fundamental) if fundamental else {"error": "analysis_failed"},
            "news": asdict(news) if news else {"error": "analysis_failed"},
        })

    logger.info("Research stage complete for %s: %d symbols processed.", as_of, len(results))
    return results


def save_research_output(results: list[dict], as_of: date) -> None:
    """Always writes the local JSON copy (cheap audit trail). Additionally
    pushes to Supabase when that backend is active."""
    path = config.RESEARCH_OUTPUT_DIR / f"research_{as_of.isoformat()}.json"
    path.write_text(json.dumps(results, indent=2, default=str))
    logger.info("Wrote research output: %s", path)

    if config.STATE_BACKEND == "supabase" and results:
        from src.research.results_store_supabase import save_research_output_supabase
        save_research_output_supabase(results, as_of)
