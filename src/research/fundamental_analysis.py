"""
Fundamental analysis stage of the research loop.

*** THIS IS A STUB. Wire in your actual framework here. ***

The contract: given a symbol and its EP trigger context, return a
FundamentalVerdict describing whether there's been a MATERIAL change in
quality / leverage / earnings trajectory. The daily loop and the Telegram
output (Phase 3) are written against this contract, so as long as
`analyze()` returns this shape, everything downstream keeps working
regardless of how you implement the internals (Screener.in scrape, your own
ratio-computation scripts, a paid API, an LLM call against filings, etc.).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FundamentalVerdict:
    symbol: str
    material_change_detected: bool
    confidence: str  # "high" | "medium" | "low" | "insufficient_data"
    summary: str
    quality_change: Optional[str] = None       # e.g. "improving", "deteriorating", "no change"
    leverage_change: Optional[str] = None      # e.g. "debt reduced", "debt increased", "no change"
    earnings_trajectory_change: Optional[str] = None  # e.g. "beat + raised guidance"
    supporting_data: dict = field(default_factory=dict)  # raw numbers you pulled, for the audit trail
    data_source: str = "NOT_CONFIGURED"


def analyze(symbol: str, context: dict) -> FundamentalVerdict:
    """
    `context` is one row from the research queue (see src/research/trigger.py
    QUEUE_COLUMNS) as a dict — gives you the EP trigger date/price/reason so
    you can anchor the fundamental check to the right period (e.g. "pull the
    most recent quarter as of the trigger date").

    TODO (you): replace this stub with a call into your existing fundamental
    framework. Once we know whether that's an Excel model, a Python ratio
    pipeline, or a paid data API, this function's body is the only thing
    that needs to change — the return contract (FundamentalVerdict) should
    stay stable so the rest of the loop doesn't need touching.
    """
    return FundamentalVerdict(
        symbol=symbol,
        material_change_detected=False,
        confidence="insufficient_data",
        summary="Fundamental analysis not yet configured — this is a placeholder result.",
        data_source="NOT_CONFIGURED",
    )
