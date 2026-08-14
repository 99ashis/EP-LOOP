"""
Thin Postgres connection helper for the Supabase backend.

Only used when config.STATE_BACKEND == "supabase". Never imported (and
psycopg2 never required) when running on the default "local" backend, so
this dependency stays optional.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager

logger = logging.getLogger(__name__)


def _get_dsn() -> str:
    from src import config
    if not config.SUPABASE_DB_URL:
        raise RuntimeError(
            "EP_STATE_BACKEND=supabase but SUPABASE_DB_URL is not set. "
            "Get the connection string from Supabase: Project Settings -> "
            "Database -> Connection string (URI). Store it as a GitHub "
            "Actions secret named SUPABASE_DB_URL — never commit it to the repo."
        )
    return config.SUPABASE_DB_URL


@contextmanager
def get_connection():
    """Yields a psycopg2 connection; commits on success, rolls back on error."""
    try:
        import psycopg2
    except ImportError as exc:
        raise RuntimeError(
            "psycopg2-binary is required for the Supabase backend. "
            "pip install psycopg2-binary (already in requirements.txt)."
        ) from exc

    conn = psycopg2.connect(_get_dsn())
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
