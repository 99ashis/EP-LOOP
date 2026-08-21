"""
Central configuration for the EP (Episodic Pivot) loop system.
Tune these thresholds as you validate against real data — they are
starting defaults, not fixed truths.
"""
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
PRICE_DIR = DATA_DIR / "prices"
PRICE_ROLLING_WINDOW_PATH = PRICE_DIR / "rolling_window.parquet"  # single consolidated file, all symbols
ROLLING_WINDOW_TRADING_DAYS = 100  # margin over the 51 sessions (50-session baseline + today) the engine needs
RAW_BHAVCOPY_DIR = DATA_DIR / "raw_bhavcopy"     # untouched daily downloads — the permanent archive
EP_STATE_DIR = DATA_DIR / "ep_state"
EP_STATE_LATEST = EP_STATE_DIR / "ep_state_latest.parquet"
EP_STATE_HISTORY_DIR = EP_STATE_DIR / "history"  # one snapshot per trading day
EP_OUTPUT_DIR = DATA_DIR / "ep_output"           # daily EP snapshot files (Phase 2 consumes this)

for d in [PRICE_DIR, RAW_BHAVCOPY_DIR, EP_STATE_DIR, EP_STATE_HISTORY_DIR, EP_OUTPUT_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# NSE Bhavcopy source
# ---------------------------------------------------------------------------
# Current (post July-2024) CM-UDiFF Common Bhavcopy Final format.
NSE_BHAVCOPY_URL_TEMPLATE = (
    "https://nsearchives.nseindia.com/content/cm/"
    "BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip"
)
NSE_BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/all-reports",
}
# NSE requires cookies from a landing-page hit before archive URLs will respond
# to a fresh session in some environments (esp. GitHub Actions runners).
NSE_LANDING_URL = "https://www.nseindia.com/all-reports"

# ---------------------------------------------------------------------------
# Universe filter (optional) — leave empty list to run on the full bhavcopy universe
# ---------------------------------------------------------------------------
SERIES_FILTER = ["EQ"]          # only ordinary equity series, drop BE/BZ/SM etc. as needed
MIN_PRICE = 20.0                # ignore penny stocks below this close price
MIN_TURNOVER_LAKHS = 50.0       # ignore illiquid names (value traded, in INR lakhs)

# ---------------------------------------------------------------------------
# EP detection & classification — anchor-based daily snapshot model
# ---------------------------------------------------------------------------
# Every symbol has at most one active "anchor": the date/price of the day its
# NEW_EP fired. Persistent/Sustained/Fizzle are all evaluated fresh, every
# day, purely against that anchor — nothing about the daily LABEL is carried
# forward from the previous day. Only the anchor itself (and a running count
# of how many trading sessions old it is) persists as tracking state.
VOLUME_BASELINE_LOOKBACK_SESSIONS = 50   # avg volume baseline window, excludes today
ANCHOR_ELIGIBILITY_WINDOW_SESSIONS = 50  # anchor expires once it turns this many sessions old
MIN_HISTORY_SESSIONS_REQUIRED = VOLUME_BASELINE_LOOKBACK_SESSIONS + 1  # baseline + today

NEW_EP_VOLUME_MULTIPLE = 5.0
NEW_EP_PRICE_PCT_VS_PREV_CLOSE = 5.0     # close >= (1 + 5/100) * prev_close
NEW_EP_MIN_ABS_VOLUME = 100_000
NEW_EP_MIN_CLOSE_PRICE = 100.0           # close must be > this on the trigger day

PERSISTENT_EP_VOLUME_MULTIPLE = 2.0      # confirmed 2x, not 4x
PERSISTENT_EP_MIN_ABS_VOLUME = 100_000
PERSISTENT_EP_PRICE_PCT_VS_PREV_CLOSE = 4.0  # close >= (1 + 4/100) * prev_close
# PLUS: close > anchor close (plain greater-than, checked in code, not a %)

SUSTAINED_EP_PCT_OF_ANCHOR_CLOSE = 97.0  # close >= 97% of anchor close
SUSTAINED_EP_MOVE_MIN_PCT_VS_PREV = 1.0  # close > 1.01x prev close
SUSTAINED_EP_MOVE_MAX_PCT_VS_PREV = 4.0  # close < 1.04x prev close (exclusive; 1.04x+ is Persistent's territory)

FIZZLE_PCT_OF_ANCHOR_PREV_CLOSE = 98.0   # close < 98% of the close on the day BEFORE whichever anchor is active

ANCHOR_TYPE_NEW = "NEW_EP"
ANCHOR_TYPE_RETRO = "RETRO_NEW_EP"

# ---------------------------------------------------------------------------
# EP status labels
# ---------------------------------------------------------------------------
STATUS_NEW = "NEW_EP"
STATUS_PERSISTENT = "PERSISTENT_EP"
STATUS_RETRO_NEW = "RETRO_NEW_EP"
STATUS_SUSTAINED = "SUSTAINED_EP"
STATUS_FIZZLE = "FIZZLE_OUT_EP"

# ---------------------------------------------------------------------------
# Research trigger loop
# ---------------------------------------------------------------------------
# Research fires whenever a symbol's LABEL_CHANGED is True (see
# src/research/trigger.py) — i.e. today's label differs from the last one
# that actually fired for that anchor. No further config needed here; the
# rule lives directly in trigger.py since it's simple enough not to need
# tuning knobs.

RESEARCH_QUEUE_DIR = DATA_DIR / "research_queue"
RESEARCH_OUTPUT_DIR = DATA_DIR / "research_output"
for d in [RESEARCH_QUEUE_DIR, RESEARCH_OUTPUT_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# GitHub Pages site data (the calendar UI)
# ---------------------------------------------------------------------------
SITE_DIR = ROOT_DIR / "docs"
SITE_DATA_DIR = SITE_DIR / "data"
SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Results-timing enrichment (New/Persistent/Sustained only)
# ---------------------------------------------------------------------------
# Two NSE feeds: event-calendar is forward-looking (scheduled board meetings,
# used for the "before results" check); corporates-financial-results is
# backward-looking (already-filed results, used for "after results").
NSE_EVENT_CALENDAR_URL = "https://www.nseindia.com/api/event-calendar"
NSE_FINANCIAL_RESULTS_URL = (
    "https://www.nseindia.com/api/corporates-financial-results"
    "?index=equities&period=Quarterly"
)
PRE_RESULTS_WINDOW_DAYS = 10   # tag as PRE_RESULTS if results are due within this many days
POST_RESULTS_WINDOW_DAYS = 5   # tag as POST_RESULTS if results were filed within this many days
RESULTS_ELIGIBLE_LABELS = {STATUS_NEW, STATUS_PERSISTENT, STATUS_SUSTAINED}

# ---------------------------------------------------------------------------
# Benchmark indices (for excess-return calculations in the efficacy study)
# ---------------------------------------------------------------------------
# NIFTY 50 was deliberately rejected as the benchmark: EP-triggering stocks
# are overwhelmingly NOT Nifty 50 constituents — that's structurally why
# they're EP candidates at all (efficiently-priced mega-caps rarely gap 5%+
# on 5x volume). NIFTY 500 (broad market) and NIFTY MIDSML 400 (the
# mid+small-cap segment, where EP events actually cluster) are used
# instead. Both are just rows in the same daily market report already
# needed for Nifty 50 — no extra download, confirmed against real fetched
# data (13-Jan-2026) during this conversation.
BENCHMARK_INDICES = {
    "NIFTY500": "Nifty 500",              # exact string as it appears in NSE's INDEX column
    "MIDSMALL400": "NIFTY MIDSML 400",    # exact string as it appears in NSE's INDEX column
}

BENCHMARK_DIR = DATA_DIR / "benchmarks"
BENCHMARK_HISTORY_PATH = BENCHMARK_DIR / "benchmark_history.parquet"
BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)

NSE_MARKET_REPORT_URL_TEMPLATE = (
    "https://nsearchives.nseindia.com/archives/equities/mkt/MA{ddmmyy}.csv"
)

# ---------------------------------------------------------------------------
# Episodic Pivot efficacy study (forward-test, not backtest — see conversation)
# ---------------------------------------------------------------------------
EFFICACY_DIR = DATA_DIR / "efficacy"
EFFICACY_EVENTS_LOG_PATH = EFFICACY_DIR / "events_log.parquet"      # (symbol, date, label) — every labeled day, ever
EFFICACY_TRACKER_PATH = EFFICACY_DIR / "new_ep_tracker.parquet"     # one row per original NEW_EP, tracked through its lifecycle
EFFICACY_DIR.mkdir(parents=True, exist_ok=True)

EFFICACY_CLASSIFICATION_WINDOW_SESSIONS = 10  # trading sessions after NEW_EP to observe before classifying
EFFICACY_RETURN_HORIZONS = [10, 20, 30]        # trading sessions from the anchor close, for each bucket

# The 9 buckets — see conversation for full reasoning behind each.
BUCKET_PURE_NEW = 1
BUCKET_PERSISTENT_1 = 2
BUCKET_PERSISTENT_2 = 3
BUCKET_PERSISTENT_3PLUS = 4
BUCKET_SUSTAINED_1 = 5
BUCKET_SUSTAINED_2 = 6
BUCKET_SUSTAINED_3PLUS = 7
BUCKET_FIZZLE = 8
BUCKET_MIXED = 9

# ---------------------------------------------------------------------------
# Storage backend for EP state + research results
# ---------------------------------------------------------------------------
# "local"    -> parquet/JSON files under data/ (default, zero setup, what's
#               been running so far)
# "supabase" -> Postgres tables (ep_state_current, ep_state_history,
#               research_results) via SUPABASE_DB_URL. Price data is NEVER
#               affected by this flag — it always stays in the git-tracked
#               rolling window file regardless.
# Set via environment variable so GitHub Actions can flip it with a repo
# variable/secret without touching code.
import os  # noqa: E402
STATE_BACKEND = os.environ.get("EP_STATE_BACKEND", "local").strip().lower()
SUPABASE_DB_URL = os.environ.get("SUPABASE_DB_URL")  # Postgres connection string, never commit this
