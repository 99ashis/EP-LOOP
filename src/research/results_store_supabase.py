"""
Supabase (Postgres) sink for research results — inserts one row per
(symbol, day) into research_results (see db/schema.sql). This is additive:
the local JSON file is always written too (cheap audit copy), regardless
of which state backend is active.
"""
from __future__ import annotations

import json
import logging
from datetime import date

from src.db.supabase_client import get_connection

logger = logging.getLogger(__name__)


def save_research_output_supabase(results: list[dict], as_of: date) -> None:
    if not results:
        return

    from psycopg2.extras import execute_values

    rows = []
    for r in results:
        fundamental = r.get("fundamental", {})
        news = r.get("news", {})
        ep_context = r.get("ep_context", {})
        rows.append((
            as_of,
            r["symbol"],
            ep_context.get("trigger_reason_for_research"),
            ep_context.get("label"),
            fundamental.get("material_change_detected"),
            fundamental.get("confidence"),
            fundamental.get("summary"),
            news.get("catalyst_identified"),
            news.get("headline"),
            news.get("summary"),
            json.dumps(r, default=str),
        ))

    cols = [
        "as_of", "symbol", "trigger_reason_for_research", "ep_label",
        "fundamental_material_change", "fundamental_confidence", "fundamental_summary",
        "news_catalyst_identified", "news_headline", "news_summary", "raw_json",
    ]
    update_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c not in ("as_of", "symbol"))
    sql = (
        f"INSERT INTO research_results ({', '.join(cols)}) VALUES %s "
        f"ON CONFLICT (as_of, symbol) DO UPDATE SET {update_clause}"
    )

    with get_connection() as conn:
        with conn.cursor() as cur:
            execute_values(cur, sql, rows)

    logger.info("Wrote %d research results to Supabase for %s.", len(rows), as_of)
