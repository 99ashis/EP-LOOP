# EP Loop System

Daily pipeline that downloads NSE bhavcopy data, maintains a rolling
100-day price history, and classifies stocks into Episodic Pivot (EP)
states using an **anchor-based, daily-recomputed model** — see
`src/ep/classifier.py` for the full lifecycle logic and rationale.

## The five states — exact spec

Each symbol has at most one active **anchor generation**: a NEW_EP
(generation 1) or a RETRO_NEW_EP (generation 2, 3, 4, ...). Every day, for a
symbol with a valid anchor, Persistent/Sustained/Fizzle are evaluated
**fresh** against the *current* generation — nothing about yesterday's label
is remembered. An anchor generation expires the moment it turns more than
`ANCHOR_ELIGIBILITY_WINDOW_SESSIONS` (default 50) trading sessions old:

- **If it produced at least one PERSISTENT_EP during its life**, the
  *earliest* one is promoted into a brand-new anchor — **RETRO_NEW_EP** —
  with its own fresh 50-session clock, own fresh Persistent/Sustained
  counts, counted from its own date (not reset to zero — if that day was
  already several sessions old, the new generation starts partway through
  its own window). This can chain indefinitely: generation 2 can itself
  expire and promote into generation 3, and so on, as long as each
  generation manages to produce a Persistent before its own clock runs out.
- **If it never produced one**, the whole episode dies outright. That same
  day is checked completely fresh for a brand-new, unrelated NEW_EP.

Promotion never rewrites history — the promoted day's original
PERSISTENT_EP row in past output stays exactly as it was originally shown.

| State | Condition |
|---|---|
| **NEW_EP** (no active anchor) | volume ≥ 5× the 50-session avg volume AND close ≥ 1.05× previous session's close AND volume > 100,000 AND close > ₹100 |
| **PERSISTENT_EP** (active anchor) | volume ≥ 2× the 50-session avg volume AND close > the anchor's close AND volume > 100,000 AND close ≥ 1.04× previous session's close |
| **RETRO_NEW_EP** | fires only on the day an anchor expires with a promotion candidate available — see above. Only ever shown on that one day. |
| **SUSTAINED_EP** (active anchor) | close > 1.01× AND < 1.04× previous session's close (a real, modest daily move — not flat/negative) AND close ≥ 97% of the anchor's close. Dropped in favor of Persistent if both fire (structurally impossible now — the 1.04× boundary makes them mutually exclusive by construction) |
| **FIZZLE_OUT_EP** (active anchor) | close < 98% of the close on the session *before whichever anchor is currently active* — the original NEW_EP's pre-day close for generation 1, or the promoted day's own pre-day close for generation 2+ |
| *(none of the above)* | the "gap zone" — not shown in that day's output; the anchor keeps ticking silently |

A day with no label is not an error — the daily output (`data/ep_output/*.csv`)
only contains symbols that actually got a label that day.

Each daily run also drives the **research trigger loop**: whenever a
symbol's label differs from the last label that actually fired for its
anchor (`LABEL_CHANGED` = True), it's queued for fundamental + news
analysis. A repeat of the same label two days running does not re-trigger.

The fundamental and news analysis modules themselves
(`src/research/fundamental_analysis.py`, `src/research/news_analysis.py`)
are currently **stubs with a defined output contract** — see "Wiring in your
fundamental framework" below. Telegram delivery is not yet built.

## Repo layout

```
src/
  config.py                  # all thresholds & paths — start here to tune
  data/
    bhavcopy_downloader.py   # NSE CM-UDiFF bhavcopy fetch (zip -> DataFrame)
    price_store.py           # single consolidated rolling-window parquet (all symbols, ~100 days)
  ep/
    indicators.py            # avg volume, ATR
    detector.py               # today's raw EP trigger candidates
    classifier.py              # New/Persistent/Sustained/Fizzle state machine
    state_store.py             # dispatcher -> local parquet OR Supabase
    state_store_local.py       # local parquet backend (default)
    state_store_supabase.py    # Supabase/Postgres backend (opt-in)
  db/
    supabase_client.py         # Postgres connection helper
    schema.sql                 # run once in Supabase SQL editor
  research/
    trigger.py                 # decides WHO gets researched today, and WHY
    fundamental_analysis.py    # STUB — wire in your framework here
    news_analysis.py           # STUB — wire in a news source here
    dispatcher.py               # runs fundamental+news for the queue, saves output
    results_store_supabase.py   # Supabase sink for research verdicts (opt-in)
  run_daily.py                # orchestrator — the daily entry point
scripts/
  backfill_historical.py      # bootstrap raw archive + rolling window over a date range
data/
  prices/rolling_window.parquet  # ALL symbols, trailing ~100 trading days only — operational cache
  raw_bhavcopy/                   # one immutable file per trading day — the PERMANENT archive
  ep_state/                       # latest.parquet + dated history/ snapshots
  ep_output/                      # daily EP snapshot CSVs
  research_queue/                 # daily "who needs research today" CSVs
  research_output/                # daily fundamental+news results (JSON, currently placeholder data)
tests/
  test_ep_pipeline.py          # synthetic, no-network test of detection + classification
  test_research_trigger.py     # synthetic test of the research trigger rules
```

## Why one consolidated price file instead of one-per-symbol

EP detection is a cross-sectional scan — every day you're asking "which of
~2,500 symbols moved abnormally today?" That's a bulk, all-symbols operation,
and the detector only ever needs a ~45-trading-day trailing baseline (20-day
avg volume, 14-day ATR + margin). So `data/prices/rolling_window.parquet`
holds exactly that: **one file, all symbols, trailing
`ROLLING_WINDOW_TRADING_DAYS` (default 100) days** — trimmed automatically
on every write. It is *not* a historical archive and isn't meant to be one.

Permanent, unbounded history lives in `data/raw_bhavcopy/` — one immutable
CSV per trading day, written once and never rewritten. That's the real
archive, and it's exactly the shape git handles well (pure appends, no
rewrite churn). If you ever need multi-year consolidated data (backtesting,
research), reconstruct it from that archive with a separate script rather
than making the operational rolling-window file carry that job too.

**Effect on the storage-location question from before:** because the
rolling-window file is now bounded in size (doesn't grow with history) and
touches one file per day instead of thousands, keeping it in the GitHub repo
is a much more reasonable default than it was with the per-symbol design —
the year-over-year git bloat concern is largely gone. Cloud storage/a real
database is still worth it if you want ad-hoc SQL querying or a dashboard
later, but Phase 1 no longer forces you into it.

## Optional: Supabase backend for EP state + research results

By default everything runs on the **local backend** — EP state in
`data/ep_state/*.parquet`, research output in `data/research_output/*.json`.
Zero setup, what's been running throughout Phase 1/2.

To switch EP state + research results over to Supabase (price data is
**never** affected by this — it always stays in the git-tracked rolling
window file):

1. Create a free Supabase project at supabase.com (500 MB DB, 5 GB egress —
   comfortably enough for this; free projects pause after 7 days with *no
   database activity*, which a daily cron job naturally prevents as long as
   it keeps running).
2. In the Supabase SQL editor, run `src/db/schema.sql` once — creates
   `ep_state_current`, `ep_state_history`, `research_results`.
3. Get the Postgres connection string: Project Settings -> Database ->
   Connection string (URI).
4. Set two things wherever the job runs (GitHub Actions: repo Secrets):
   - `SUPABASE_DB_URL` = the connection string (secret — never commit this)
   - `EP_STATE_BACKEND` = `supabase`
5. `pip install -r requirements.txt` (pulls in `psycopg2-binary`).

That's it — `state_store.py` and `research/dispatcher.py` route to the
Supabase-backed implementations automatically once that env var is set; no
other code changes needed.

**One gotcha worth knowing:** if you switch backends while you have EPs
actively tracked in the local parquet state, they don't automatically carry
over — Supabase starts from an empty `ep_state_current`. Cleanest is to
switch on a day with no active episodes, or say the word and I'll write a
one-time migration script to copy local state into Supabase before you flip
the flag.

Why Supabase for this piece and not price data: EP state and research
verdicts are exactly what you'll want to query later ("show me every EP
that fizzled," "average days to Sustained") — that's SQL's home turf, and
at a few thousand rows a year it'll never come close to the 500 MB cap.
Price bars are just an operational cache the pipeline reads once a day;
they don't benefit from a database the same way.

## Wiring in your fundamental framework

`src/research/fundamental_analysis.py::analyze(symbol, context)` is the only
function you need to replace. It receives the EP trigger context (trigger
date, price, % move, days since trigger) as a plain dict, and must return a
`FundamentalVerdict` — that return shape is what the rest of the loop (and
eventually the Telegram sender) is written against, so the internals are
entirely up to you: Screener.in scrape, your own ratio scripts, an Excel
model read via `openpyxl`, a paid API, whatever you're already using.

Same pattern for `src/research/news_analysis.py` — worth considering NSE's
own corporate-announcements feed as the primary source here rather than
general news search, since exchange filings are precisely the "material
change" disclosures this loop exists to catch, and they're already
timestamped against the same trigger date you're anchoring to.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## First-time historical backfill

NSE's current (UDiFF) bhavcopy format is available from **2024-07-08
onward**. Older history needs the discontinued `sec_bhavdata_full` format —
not included here since it's dead; flag it if you need pre-2024-07 history
and we'll add a legacy-format branch.

```bash
python -m scripts.backfill_historical --start 2024-07-08 --end 2026-08-13
```

This is rate-limited to be polite to NSE and will take a while for a
multi-year backfill — expect ~1.5s per trading day.

## Daily run

```bash
python -m src.run_daily                    # today
python -m src.run_daily --date 2026-08-13   # re-run a specific date (idempotent)
```

Exit codes: `0` = success, `1` = no data (holiday — not treated as CI
failure), `2` = genuine failure (download error after retries).

## Running tests

```bash
pytest tests/ -v
```

Tests are fully synthetic (fake price series with known patterns for a
pivot, a fizzle, and a sustain) — no network access required, safe to run
in CI on every commit.

## Known fragile points (worth knowing before you automate this)

1. **NSE changes its bhavcopy URL/schema periodically** — it did in July
   2024 (this repo targets the current UDiFF format). If the daily download
   starts failing, check `data/raw_bhavcopy/<date>.csv` first — the header
   row will tell you what changed.
2. **NSE requires session cookies** from a landing-page hit before the
   archive host will serve data to some clients (this is handled in
   `bhavcopy_downloader._new_session()`), but NSE occasionally tightens
   bot detection further — if GitHub Actions runners start getting 403s
   that a local run doesn't, that's usually why.
3. **Publish timing varies** — NSE typically publishes by ~7–8 PM IST but
   this isn't guaranteed. The scheduler (Phase 2) should retry rather than
   assume a fixed time.
4. **Corporate actions** (splits/bonuses) will show up as huge fake
   "moves" in raw close price. This version does not adjust for corporate
   actions — worth flagging if you see spurious EPs around ex-dates; we can
   add a corporate-action filter in a later pass if it's needed.

## tools/

Standalone utilities that are NOT part of the daily pipeline — currently a
separate, unbounded full-history bhavcopy-to-Supabase script for personal
use. See `tools/README.md`. Nothing in `src/` imports from here, and the
daily job doesn't touch it.

## Roadmap — Phase 3 (not built yet)

- Plug your real fundamental framework into `fundamental_analysis.py`
- Plug a real news source into `news_analysis.py` (NSE announcements feed recommended)
- Telegram bot: daily EP snapshot message + a **separate** fundamental/news
  update message
- Decide: commit outputs to repo vs. push to cloud storage
