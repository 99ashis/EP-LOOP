"""
Daily entry point.

Pipeline:
  1. Download today's bhavcopy from NSE
  2. Append it into the rolling-window price history
  3. Run the anchor-based EP classification (New / Persistent / Sustained /
     Fizzle — see src/ep/classifier.py for the exact lifecycle rules)
  4. Persist the updated anchor tracking state + write today's labeled-only
     EP snapshot output file
  5. Build the research queue from label changes, and run the (currently
     stub) fundamental/news research stage

Usage:
    python -m src.run_daily                      # run for today
    python -m src.run_daily --date 2026-08-13     # backfill/re-run a specific date
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

from src import config
from src.data.bhavcopy_downloader import download_bhavcopy, BhavcopyDownloadError
from src.data.price_store import append_daily_bhavcopy
from src.ep.classifier import run_daily_classification
from src.ep import state_store
from src.research.trigger import build_research_queue, save_research_queue
from src.research.dispatcher import run_research_for_queue, save_research_output
from src.report.text_report import build_daily_text_report, save_text_report
from src.report.json_report import build_and_save_site_data

logger = logging.getLogger(__name__)


def run_for_date(trade_date: date) -> int:
    """Returns process exit code (0 = success, 1 = no data / holiday, 2 = failure)."""
    logger.info("=== EP daily run for %s ===", trade_date)

    try:
        daily_bhavcopy = download_bhavcopy(trade_date)
    except BhavcopyDownloadError:
        logger.exception("Bhavcopy download failed for %s", trade_date)
        return 2

    if daily_bhavcopy is None:
        logger.info("No trading data for %s (holiday/weekend) — nothing to do.", trade_date)
        return 1

    append_daily_bhavcopy(daily_bhavcopy)

    prior_anchors = state_store.load_state_as_of(trade_date)
    updated_anchors, daily_output = run_daily_classification(prior_anchors, daily_bhavcopy, trade_date)
    state_store.save_state(updated_anchors, trade_date)
    logger.info("Saved anchor state: %d active anchor(s) tracked as of %s", len(updated_anchors), trade_date)

    if config.STATE_BACKEND == "supabase" and not daily_output.empty:
        from src.ep.state_store_supabase import save_daily_output_history
        save_daily_output_history(daily_output, trade_date)

    snapshot_path = config.EP_OUTPUT_DIR / f"ep_snapshot_{trade_date.isoformat()}.csv"
    daily_output.to_csv(snapshot_path, index=False)
    logger.info("Wrote EP snapshot: %s (%d labeled symbols)", snapshot_path, len(daily_output))

    counts = daily_output["LABEL"].value_counts().to_dict() if not daily_output.empty else {}
    logger.info("Label breakdown: %s", counts)

    report_text = build_daily_text_report(daily_output, trade_date)
    report_path = save_text_report(report_text, trade_date)
    logger.info("Wrote daily text report: %s", report_path)

    build_and_save_site_data(daily_output, trade_date)
    logger.info("Wrote site data: docs/data/%s.json + updated index.json", trade_date.isoformat())

    # --- Research trigger loop ---
    queue = build_research_queue(daily_output, trade_date)
    save_research_queue(queue, trade_date)

    if not queue.empty:
        results = run_research_for_queue(queue, trade_date)
        save_research_output(results, trade_date)
        logger.info(
            "NOTE: fundamental_analysis.py / news_analysis.py are still stubs — "
            "research output for %s is placeholder data until your framework is wired in.",
            trade_date,
        )
    else:
        logger.info("No symbols flagged for research today (%s).", trade_date)

    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="YYYY-MM-DD, defaults to today", default=None)
    args = parser.parse_args()

    run_date = date.fromisoformat(args.date) if args.date else date.today()
    exit_code = run_for_date(run_date)
    sys.exit(0 if exit_code in (0, 1) else exit_code)  # holiday is not a failure for CI purposes
