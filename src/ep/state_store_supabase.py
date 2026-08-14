"""
Supabase (Postgres) persistence for the anchor tracking table — same public
interface as state_store_local.py.

Tables (see db/schema.sql):
  ep_anchors_current — one row per symbol with an active anchor, upserted daily.
                        Absence == no active episode.
  ep_output_history   — permanent append-only log of every LABELED day
                         (every row classifier.py put in its daily output),
                         one row per (symbol, as_of). This is the queryable
                         audit trail — "show me every Fizzle Out in June".
"""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from src.db.supabase_client import get_connection
from src.ep.classifier import ANCHOR_COLUMNS

logger = logging.getLogger(__name__)

STATE_COLUMNS = ANCHOR_COLUMNS
_DB_COLUMNS = [c.lower() for c in ANCHOR_COLUMNS]


def _clean(v):
    return None if pd.isna(v) else v


def _row_to_db_tuple(row: pd.Series) -> tuple:
    return tuple(_clean(row[c]) for c in ANCHOR_COLUMNS)


def _records_to_dataframe(records: list[tuple]) -> pd.DataFrame:
    if not records:
        return pd.DataFrame(columns=ANCHOR_COLUMNS)
    df = pd.DataFrame(records, columns=_DB_COLUMNS)
    df.columns = ANCHOR_COLUMNS
    for col in ["ANCHOR_DATE", "ORIGIN_ANCHOR_DATE", "PROMOTION_CANDIDATE_DATE", "LAST_LABEL_DATE"]:
        df[col] = pd.to_datetime(df[col])
    return df


def load_latest_state() -> pd.DataFrame:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {', '.join(_DB_COLUMNS)} FROM ep_anchors_current")
            records = cur.fetchall()
    return _records_to_dataframe(records)


def save_state(anchors_df: pd.DataFrame, as_of: date) -> None:
    anchors_df = anchors_df[ANCHOR_COLUMNS].copy() if not anchors_df.empty else anchors_df

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT symbol FROM ep_anchors_current")
            existing_symbols = {r[0] for r in cur.fetchall()}
            new_symbols = set(anchors_df["SYMBOL"]) if not anchors_df.empty else set()

            removed = existing_symbols - new_symbols
            if removed:
                cur.execute("DELETE FROM ep_anchors_current WHERE symbol = ANY(%s)", (list(removed),))
                logger.info("Removed %d expired/inactive anchors.", len(removed))

            if not anchors_df.empty:
                _upsert_anchors(cur, anchors_df)

    logger.info("Supabase anchor save complete for %s: %d active anchors.", as_of, len(anchors_df))


def _upsert_anchors(cur, df: pd.DataFrame) -> None:
    from psycopg2.extras import execute_values
    cols = _DB_COLUMNS
    update_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "symbol")
    sql = (
        f"INSERT INTO ep_anchors_current ({', '.join(cols)}) VALUES %s "
        f"ON CONFLICT (symbol) DO UPDATE SET {update_clause}"
    )
    values = [_row_to_db_tuple(row) for _, row in df.iterrows()]
    execute_values(cur, sql, values)


def save_daily_output_history(daily_output: pd.DataFrame, as_of: date) -> None:
    """Appends the day's labeled rows (classifier.py's output, not the
    anchor state) into the permanent Supabase audit table."""
    if daily_output.empty:
        return

    from psycopg2.extras import execute_values
    from src.ep.classifier import OUTPUT_COLUMNS

    cols = [c.lower() for c in OUTPUT_COLUMNS]
    update_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c not in ("symbol", "as_of_date"))
    sql = (
        f"INSERT INTO ep_output_history ({', '.join(cols)}) VALUES %s "
        f"ON CONFLICT (as_of_date, symbol) DO UPDATE SET {update_clause}"
    )

    def clean_row(row):
        return tuple(_clean(row[c]) for c in OUTPUT_COLUMNS)

    values = [clean_row(row) for _, row in daily_output.iterrows()]

    with get_connection() as conn:
        with conn.cursor() as cur:
            execute_values(cur, sql, values)

    logger.info("Wrote %d rows to ep_output_history for %s.", len(values), as_of)
