"""
Sends the daily EP text report to Telegram, wrapped in a monospace block
(HTML <pre>) so the aligned columns don't collapse on a proportional font.

Telegram caps messages at 4096 characters. If the report is longer than
that (a genuinely busy day across many stocks), it's split into multiple
messages — always on section boundaries, so a table is never cut mid-row.
"""
from __future__ import annotations

import html
import logging
import os
import time

import requests

logger = logging.getLogger(__name__)

TELEGRAM_MAX_MESSAGE_LENGTH = 4096
_SAFETY_MARGIN = 200  # room for <pre></pre> tags and an optional "Part x/y" header


def _get_credentials() -> tuple[str, str]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must both be set as environment "
            "variables (GitHub Actions secrets) to send Telegram alerts."
        )
    return token, chat_id


def _split_into_chunks(report_text: str) -> list[str]:
    """Splits on the blank lines between sections, packing as many whole
    sections into each chunk as fit. If a SINGLE section is already bigger
    than one message on its own (a very busy day, many tickers in one
    category), that section gets split line-by-line as a fallback — a
    table row is still never cut in half, but a huge section spans
    multiple messages rather than being sent oversized and rejected."""
    limit = TELEGRAM_MAX_MESSAGE_LENGTH - _SAFETY_MARGIN
    blocks = report_text.split("\n\n")
    chunks: list[str] = []
    current = ""

    def flush():
        nonlocal current
        if current:
            chunks.append(current)
            current = ""

    for block in blocks:
        if len(block) > limit:
            flush()
            lines = block.split("\n")
            sub = ""
            for line in lines:
                candidate = (sub + "\n" + line) if sub else line
                if len(candidate) > limit and sub:
                    chunks.append(sub)
                    sub = line
                else:
                    sub = candidate
            if sub:
                chunks.append(sub)
            continue

        candidate = (current + "\n\n" + block) if current else block
        if len(candidate) > limit and current:
            chunks.append(current)
            current = block
        else:
            current = candidate

    flush()
    return chunks


def send_telegram_report(report_text: str) -> int:
    """Returns the number of messages sent."""
    token, chat_id = _get_credentials()
    chunks = _split_into_chunks(report_text)
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    for i, chunk in enumerate(chunks, start=1):
        body = f"<pre>{html.escape(chunk)}</pre>"
        if len(chunks) > 1:
            body = f"<b>Part {i}/{len(chunks)}</b>\n{body}"

        resp = requests.post(url, data={
            "chat_id": chat_id,
            "text": body,
            "parse_mode": "HTML",
        }, timeout=15)

        if resp.status_code != 200:
            raise RuntimeError(f"Telegram send failed ({resp.status_code}): {resp.text}")

        logger.info("Sent Telegram message %d/%d (%d chars).", i, len(chunks), len(chunk))
        if i < len(chunks):
            time.sleep(1)  # be polite to Telegram's rate limit between chunks

    return len(chunks)


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) < 2:
        print("Usage: python -m src.notify.telegram_sender <path-to-report.txt>")
        sys.exit(1)
    with open(sys.argv[1], "r") as f:
        text = f.read()
    sent = send_telegram_report(text)
    print(f"Sent {sent} message(s).")
