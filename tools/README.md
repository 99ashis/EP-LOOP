# tools/

Standalone utility scripts that are **not** part of the daily EP pipeline
(`src/`, `run_daily.py`). Nothing here is imported by the pipeline, and the
pipeline's daily run doesn't depend on anything in this folder — these are
separate, run-it-yourself tools.

## full_history_bhavcopy_to_supabase.py

Downloads NSE bhavcopy via `nsepython.get_bhavcopy()` and stores it in a
Supabase `bhavcopies` table, unbounded (not trimmed to any rolling window).
This is intentionally separate from `src/data/bhavcopy_downloader.py` +
`src/data/price_store.py`, which download directly from NSE's UDiFF archive
and maintain only the trailing 100-day rolling window on GitHub — that's the
pipeline's actual data source, and this script does not feed it.

Use case: a personal, unbounded historical archive in Supabase, independent
of what the EP loop needs operationally.

### Setup

```bash
pip install nsepython sqlalchemy python-dotenv psycopg2-binary
```

Create a `.env` file in the repo root (already gitignored — never commit
this):
```
DB_CONNECTION_STRING=postgresql://[USER]:[PASSWORD]@[HOST]:[PORT]/[DBNAME]
LOOKBACK_DAYS=3
```

```bash
python tools/full_history_bhavcopy_to_supabase.py
```

### Known issues, not yet fixed (flagging so they don't surprise you)

- The dedup check wraps its `SELECT` in a bare `except Exception: pass`,
  meant to catch "table doesn't exist yet" but silently swallowing any
  other failure (bad credentials, network blip, etc.) too — a real error
  there currently looks identical to "table's empty."
- No `UNIQUE(symbol, date)` constraint at the database level — dedup is
  enforced entirely by the app-level pre-query, which is fine for a single
  scheduled run but not safe against overlapping/retried runs.
- No `SERIES` filter — ingests every instrument type in the bhavcopy
  (equity, SME, debt, etc.), not just `EQ`.
- `PREV_CLOSE` is renamed to `PREV` here (not `PREV_CLOSE`), and there's no
  `TURNOVER` column — this schema does not match `src/data/price_store.py`'s
  columns, by design, since it isn't meant to feed the EP loop.

Say the word if you want these fixed — happy to patch the unique constraint
and the silent-except in place without changing anything else about how the
script works.
