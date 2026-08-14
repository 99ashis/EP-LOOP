"""
Downloads NSE's daily CM-UDiFF Common Bhavcopy Final (zip -> csv) and returns
a cleaned pandas DataFrame with one row per (symbol, series).

NSE's archive endpoint routinely 403s requests that don't carry cookies from
a prior hit on nseindia.com, so we always warm a session first. Built to be
resilient to transient failures (retry + backoff) since this runs
unattended in CI.
"""
from __future__ import annotations

import io
import logging
import time
import zipfile
from datetime import date, datetime
from typing import Optional

import pandas as pd
import requests

from src import config

logger = logging.getLogger(__name__)

# Columns of interest in the UDiFF bhavcopy. NSE has renamed columns before;
# if a download starts failing on KeyError, check the raw CSV header first —
# it's the most common breakage point for this whole pipeline.
COLUMN_MAP = {
    "TckrSymb": "SYMBOL",
    "SctySrs": "SERIES",
    "OpnPric": "OPEN",
    "HghPric": "HIGH",
    "LwPric": "LOW",
    "ClsPric": "CLOSE",
    "PrvsClsgPric": "PREV_CLOSE",
    "TtlTradgVol": "VOLUME",
    "TtlTrfVal": "TURNOVER",
    "TradDt": "DATE",
}


class BhavcopyDownloadError(RuntimeError):
    pass


def _new_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(config.NSE_BASE_HEADERS)
    # Warm-up hit: NSE issues cookies here that the archive host will accept.
    session.get(config.NSE_LANDING_URL, timeout=15)
    return session


def _build_url(trade_date: date) -> str:
    return config.NSE_BHAVCOPY_URL_TEMPLATE.format(yyyymmdd=trade_date.strftime("%Y%m%d"))


def download_bhavcopy(
    trade_date: date,
    max_retries: int = 4,
    backoff_seconds: float = 5.0,
    save_raw: bool = True,
) -> Optional[pd.DataFrame]:
    """
    Fetch and parse the bhavcopy for a single trading date.
    Returns None (does not raise) if NSE has no data for that date — this is
    normal on weekends/holidays, and the caller should treat it as "skip".
    Raises BhavcopyDownloadError only on genuine, retried-out failures.
    """
    url = _build_url(trade_date)
    last_error: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        try:
            session = _new_session()
            resp = session.get(url, timeout=30)

            if resp.status_code == 404:
                logger.info("No bhavcopy published for %s (404) — likely a holiday.", trade_date)
                return None

            resp.raise_for_status()

            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
                if not csv_names:
                    raise BhavcopyDownloadError(f"No CSV found inside zip for {trade_date}")
                raw_bytes = zf.read(csv_names[0])

            if save_raw:
                config.RAW_BHAVCOPY_DIR.mkdir(parents=True, exist_ok=True)
                raw_path = config.RAW_BHAVCOPY_DIR / f"{trade_date.isoformat()}.csv"
                raw_path.write_bytes(raw_bytes)

            df = pd.read_csv(io.BytesIO(raw_bytes))
            return _clean_bhavcopy(df, trade_date)

        except Exception as exc:  # noqa: BLE001 — we want to retry on anything transient
            last_error = exc
            logger.warning(
                "Bhavcopy download attempt %d/%d for %s failed: %s",
                attempt, max_retries, trade_date, exc,
            )
            if attempt < max_retries:
                time.sleep(backoff_seconds * attempt)  # linear backoff

    raise BhavcopyDownloadError(
        f"Failed to download bhavcopy for {trade_date} after {max_retries} attempts: {last_error}"
    )


def _clean_bhavcopy(df: pd.DataFrame, trade_date: date) -> pd.DataFrame:
    # Rename only columns that exist — NSE has added/removed fields before.
    rename = {src: dst for src, dst in COLUMN_MAP.items() if src in df.columns}
    df = df.rename(columns=rename)

    missing = [c for c in ["SYMBOL", "SERIES", "CLOSE", "VOLUME"] if c not in df.columns]
    if missing:
        raise BhavcopyDownloadError(
            f"Bhavcopy for {trade_date} is missing expected columns {missing}. "
            f"NSE likely changed the file schema — inspect data/raw_bhavcopy/{trade_date}.csv"
        )

    if config.SERIES_FILTER:
        df = df[df["SERIES"].isin(config.SERIES_FILTER)]

    keep_cols = [c for c in COLUMN_MAP.values() if c in df.columns]
    df = df[keep_cols].copy()
    df["DATE"] = pd.to_datetime(trade_date)

    for col in ["OPEN", "HIGH", "LOW", "CLOSE", "PREV_CLOSE", "VOLUME", "TURNOVER"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["CLOSE", "VOLUME"])
    df = df[df["CLOSE"] >= config.MIN_PRICE]

    if "TURNOVER" in df.columns:
        # TURNOVER from NSE is already in INR; convert to lakhs for the filter.
        df = df[(df["TURNOVER"] / 1e5) >= config.MIN_TURNOVER_LAKHS]

    return df.reset_index(drop=True)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import sys
    d = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
    result = download_bhavcopy(d)
    if result is None:
        print(f"No data for {d}")
    else:
        print(result.head())
        print(f"\n{len(result)} symbols after filters")
