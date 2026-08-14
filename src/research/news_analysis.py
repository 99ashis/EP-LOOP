"""
News analysis stage of the research loop.

*** THIS IS A STUB. ***

Same idea as fundamental_analysis.py: a stable contract (NewsVerdict) that
the loop and Telegram output are written against, with the actual
news-fetching logic left for you to wire in — depends on whether you want
free web search, a news API (e.g. NewsAPI, Google News RSS), or NSE's own
corporate-announcements feed (which is arguably the highest-signal, lowest-
noise source for this specific use case — exchange filings are exactly
"material" events by definition).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class NewsVerdict:
    symbol: str
    catalyst_identified: bool
    headline: Optional[str] = None
    source: Optional[str] = None
    published_date: Optional[str] = None
    category: Optional[str] = None  # e.g. "earnings", "M&A", "regulatory", "management change", "order win"
    summary: str = "News analysis not yet configured — this is a placeholder result."
    supporting_links: list = field(default_factory=list)


def analyze(symbol: str, context: dict) -> NewsVerdict:
    """
    TODO (you): wire in a real news source. Strong candidate for this
    specific use case: NSE's corporate-announcements API
    (https://www.nseindia.com/companies-listing/corporate-filings-announcements)
    since it's the same trigger date you're already anchoring to, and
    exchange filings are precisely the kind of "material change" disclosure
    this loop is trying to catch — worth prioritizing over general news
    search if you want low-noise, high-precision results.
    """
    return NewsVerdict(symbol=symbol, catalyst_identified=False)
