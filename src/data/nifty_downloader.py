"""
Downloads benchmark index closes from NSE's daily market activity report —
used for excess-return calculations in the efficacy study.

NIFTY 50 was deliberately NOT used as the benchmark: EP-triggering stocks
are overwhelmingly not Nifty 50 constituents (efficiently-priced mega-caps
rarely gap 5%+ on 5x volume — that's structurally why they don't show up
here). NIFTY 500 (broad market) and NIFTY MIDSML 400 (the mid+small-cap
segment, where EP events actually cluster) are used instead — see
config.BENCHMARK_INDICES for the exact configured set.

Same report file confirmed live against real data (13-Jan-2026) during
this conversation — one download, multiple index rows extracted from it,
rather than one request per benchmark.
"""
from __future__ import annotations

import logging
import time
from datetime import date

import pandas as pd
import requests

from src import config

logger = logging.getLogger(__name__)


class NiftyDownloadError(RuntimeError):
    pass


def _nse_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(config.NSE_BASE_HEADERS)
    session.get(config.NSE_LANDING_URL, timeout=15)
    return session


def _parse_index_closes(report_text: str, index_names: list[str]) -> dict[str, float]:
    """Returns {exact_index_name: close} for whichever of the requested
    index_names are actually found in the report's indices table. Missing
    ones are simply absent from the result, not an error — lets a schema
    change in one index not block the other."""
    lines = report_text.splitlines()

    header_idx = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(",INDEX,") and "CLOSE" in stripped:
            header_idx = i
            break
    if header_idx is None:
        return {}

    header_fields = [f.strip() for f in lines[header_idx].split(",")]
    try:
        name_col = header_fields.index("INDEX")
        close_col = header_fields.index("CLOSE")
    except ValueError:
        return {}

    wanted = {name.strip().lower(): name for name in index_names}
    found: dict[str, float] = {}

    for line in lines[header_idx + 1:]:
        if not line.strip():
            break  # blank line = end of the indices table section
        fields = line.split(",")
        if len(fields) <= max(name_col, close_col):
            continue
        key = fields[name_col].strip().lower()
        if key in wanted:
            try:
                found[wanted[key]] = float(fields[close_col].strip())
            except ValueError:
                continue

    return found


def download_benchmark_closes(
    trade_date: date,
    max_retries: int = 4,
    backoff_seconds: float = 5.0,
) -> dict[str, float] | None:
    """
    Returns {exact_index_name: close} for every index in
    config.BENCHMARK_INDICES that was found, or None if NSE has no report
    for this date (holiday). Raises NiftyDownloadError only on a genuine,
    retried-out failure — a missing individual index (schema drift on one
    row) does not raise, it's just absent from the dict.
    """
    url = config.NSE_MARKET_REPORT_URL_TEMPLATE.format(ddmmyy=trade_date.strftime("%d%m%y"))
    last_error: Exception | None = None
    wanted_names = list(config.BENCHMARK_INDICES.values())

    for attempt in range(1, max_retries + 1):
        try:
            session = _nse_session()
            resp = session.get(url, timeout=20)

            if resp.status_code == 404:
                logger.info("No market report for %s (404) — likely a holiday.", trade_date)
                return None

            resp.raise_for_status()
            closes = _parse_index_closes(resp.text, wanted_names)
            missing = set(wanted_names) - set(closes.keys())
            if missing:
                logger.warning("Benchmark(s) not found in report for %s: %s", trade_date, missing)
            return closes

        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning("Benchmark download attempt %d/%d for %s failed: %s",
                            attempt, max_retries, trade_date, exc)
            if attempt < max_retries:
                time.sleep(backoff_seconds * attempt)

    raise NiftyDownloadError(
        f"Failed to download benchmark closes for {trade_date} after {max_retries} attempts: {last_error}"
    )


def load_benchmark_history() -> pd.DataFrame:
    cols = ["DATE"] + list(config.BENCHMARK_INDICES.keys())
    if not config.BENCHMARK_HISTORY_PATH.exists():
        return pd.DataFrame(columns=cols)
    df = pd.read_parquet(config.BENCHMARK_HISTORY_PATH)
    for col in cols:
        if col not in df.columns:
            df[col] = None
    return df


def append_benchmark_closes(trade_date: date, closes_by_exact_name: dict[str, float]) -> None:
    """`closes_by_exact_name` uses the EXACT NSE index name as keys (what
    download_benchmark_closes returns) — mapped back to the short config
    keys (e.g. "NIFTY500") before storing, so downstream code never has to
    deal with NSE's exact index-name strings."""
    history = load_benchmark_history()
    row = {"DATE": pd.Timestamp(trade_date)}
    for short_key, exact_name in config.BENCHMARK_INDICES.items():
        row[short_key] = closes_by_exact_name.get(exact_name)

    new_row = pd.DataFrame([row])
    combined = pd.concat([history, new_row], ignore_index=True)
    combined = combined.drop_duplicates(subset=["DATE"], keep="last").sort_values("DATE")
    combined.to_parquet(config.BENCHMARK_HISTORY_PATH, index=False)


def get_benchmark_close_on(trade_date: date, short_key: str) -> float | None:
    """`short_key` is one of config.BENCHMARK_INDICES's keys, e.g. 'NIFTY500'."""
    history = load_benchmark_history()
    if history.empty:
        return None
    row = history[history["DATE"] == pd.Timestamp(trade_date)]
    if row.empty:
        return None
    value = row.iloc[0].get(short_key)
    return float(value) if pd.notna(value) else None
