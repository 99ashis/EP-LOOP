"""
Adds results-timing context to New/Persistent/Sustained EP rows — the
"why did this move happen" layer discussed with the user: is it ahead of a
scheduled results announcement, right after one, or unrelated to results at
all. Retro New EP and Fizzle Out are deliberately left untouched (not what
was asked for — easy to extend later, e.g. a dedicated "sell the news"
Fizzle-after-results flag, but that's a separate decision, not silently
bundled in here).

Two NSE feeds, genuinely different in kind:
  - event-calendar: FORWARD-looking — scheduled board meetings, filtered to
    ones whose purpose mentions "Financial Results". This is what makes the
    "before results" check possible at all — without it, you can't know a
    result is coming until it's already happened.
  - corporates-financial-results: BACKWARD-looking — results already filed.
    Used for the "after results" check.

This module never touches the classification engine (src/ep/) — it's a
pure post-classification enrichment step, called from run_daily.py after
the daily_output DataFrame already exists. Keeps the tested, exact-spec EP
logic completely isolated from an unrelated new data source.
"""
from __future__ import annotations

import logging
from datetime import date, datetime

import pandas as pd
import requests

from src import config

logger = logging.getLogger(__name__)


def _nse_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(config.NSE_BASE_HEADERS)
    session.get(config.NSE_LANDING_URL, timeout=15)  # warm cookies, same as bhavcopy_downloader
    return session


def _parse_nse_date(value: str) -> date | None:
    for fmt in ("%d-%b-%Y", "%d-%b-%Y %H:%M:%S"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    return None


def fetch_upcoming_results() -> dict[str, date]:
    """{symbol: nearest upcoming results date}, from board meetings whose
    purpose mentions Financial Results. Never raises — a failed fetch just
    means pre-results tagging is skipped for the day, not a pipeline crash."""
    try:
        session = _nse_session()
        resp = session.get(config.NSE_EVENT_CALENDAR_URL, timeout=20)
        resp.raise_for_status()
        rows = resp.json()
    except Exception:
        logger.exception("Failed to fetch NSE event calendar — pre-results tagging skipped today.")
        return {}

    upcoming: dict[str, date] = {}
    for row in rows:
        if "Financial Results" not in str(row.get("purpose", "")):
            continue
        symbol = row.get("symbol")
        parsed = _parse_nse_date(row.get("date", ""))
        if not symbol or parsed is None:
            continue
        if symbol not in upcoming or parsed < upcoming[symbol]:
            upcoming[symbol] = parsed
    return upcoming


def fetch_filed_results() -> dict[str, date]:
    """{symbol: most recent filed results date}. Never raises — a failed
    fetch just means post-results tagging is skipped for the day."""
    try:
        session = _nse_session()
        resp = session.get(config.NSE_FINANCIAL_RESULTS_URL, timeout=20)
        resp.raise_for_status()
        payload = resp.json()
        rows = payload.get("data", []) if isinstance(payload, dict) else payload
    except Exception:
        logger.exception("Failed to fetch NSE filed results — post-results tagging skipped today.")
        return {}

    filed: dict[str, date] = {}
    for row in rows:
        symbol = row.get("symbol")
        date_str = row.get("filingDate") or row.get("broadcastDate") or row.get("date")
        parsed = _parse_nse_date(date_str) if date_str else None
        if not symbol or parsed is None:
            continue
        if symbol not in filed or parsed > filed[symbol]:
            filed[symbol] = parsed
    return filed


def enrich_with_results_context(
    daily_output: pd.DataFrame,
    as_of: date,
    upcoming: dict[str, date] | None = None,
    filed: dict[str, date] | None = None,
) -> pd.DataFrame:
    """
    Adds RESULT_DATE, DAYS_TO_OR_SINCE_RESULTS, CATALYST_CONTEXT to New/
    Persistent/Sustained rows only. CATALYST_CONTEXT is one of
    "PRE_RESULTS", "POST_RESULTS", or "NO_CATALYST". Other labels get all
    three columns as None, left alone entirely.

    `upcoming`/`filed` can be pre-fetched and passed in (avoids re-hitting
    NSE if this is ever called more than once) — defaults to fetching fresh.
    """
    df = daily_output.copy()
    df["RESULT_DATE"] = None
    df["DAYS_TO_OR_SINCE_RESULTS"] = None
    df["CATALYST_CONTEXT"] = None

    if df.empty:
        return df

    eligible_mask = df["LABEL"].isin(config.RESULTS_ELIGIBLE_LABELS)
    if not eligible_mask.any():
        return df

    upcoming = fetch_upcoming_results() if upcoming is None else upcoming
    filed = fetch_filed_results() if filed is None else filed

    for idx in df[eligible_mask].index:
        symbol = df.at[idx, "SYMBOL"]
        upcoming_date = upcoming.get(symbol)
        filed_date = filed.get(symbol)

        days_to_upcoming = (upcoming_date - as_of).days if upcoming_date else None
        days_since_filed = (as_of - filed_date).days if filed_date else None

        if days_to_upcoming is not None and 0 <= days_to_upcoming <= config.PRE_RESULTS_WINDOW_DAYS:
            df.at[idx, "RESULT_DATE"] = upcoming_date.strftime("%d-%b-%y")
            df.at[idx, "DAYS_TO_OR_SINCE_RESULTS"] = -days_to_upcoming  # negative = days UNTIL
            df.at[idx, "CATALYST_CONTEXT"] = "PRE_RESULTS"
        elif days_since_filed is not None and 0 <= days_since_filed <= config.POST_RESULTS_WINDOW_DAYS:
            df.at[idx, "RESULT_DATE"] = filed_date.strftime("%d-%b-%y")
            df.at[idx, "DAYS_TO_OR_SINCE_RESULTS"] = days_since_filed   # positive = days SINCE
            df.at[idx, "CATALYST_CONTEXT"] = "POST_RESULTS"
        else:
            df.at[idx, "CATALYST_CONTEXT"] = "NO_CATALYST"

    return df
