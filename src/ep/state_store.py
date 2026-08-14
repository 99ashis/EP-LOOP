"""
Dispatches EP state persistence to either the local-parquet backend or the
Supabase backend, based on config.STATE_BACKEND. Everything else in the
codebase (classifier.py, run_daily.py) imports from here and never needs to
know which one is active.

Default is "local" — zero setup, what's been running since Phase 1. Flip to
Supabase by setting the environment variable EP_STATE_BACKEND=supabase (and
SUPABASE_DB_URL) — see src/db/schema.sql for the one-time table setup.
"""
from __future__ import annotations

from src import config

if config.STATE_BACKEND == "supabase":
    from src.ep.state_store_supabase import load_latest_state, save_state, STATE_COLUMNS  # noqa: F401
elif config.STATE_BACKEND == "local":
    from src.ep.state_store_local import load_latest_state, save_state, STATE_COLUMNS  # noqa: F401
else:
    raise ValueError(
        f"Unknown EP_STATE_BACKEND='{config.STATE_BACKEND}'. Expected 'local' or 'supabase'."
    )
