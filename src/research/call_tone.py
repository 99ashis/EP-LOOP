"""
Deterministic concall tone counter — three categories, counted quarter over
quarter from the actual transcript text: congratulatory remarks (analyst
side), uncertainty language, and certainty language (both management side).

Design (confirmed, per user's explicit choices):
  - COUNTING METHOD: a maintained keyword/phrase list, matched literally
    (case-insensitive, substring after whitespace normalization) against
    the transcript text. This is the OFFICIAL, reported count — it's a
    number anyone can re-check by re-reading the transcript themselves,
    same audit-trail philosophy as the rest of this repo. It will miss
    paraphrasing the list doesn't yet recognize; that's a known,
    accepted limitation, not a bug — see NEAR-MISS FLAGGING below for
    how that gap gets narrowed over time.
  - NEAR-MISS FLAGGING (hybrid, confirmed): the LLM, in the SAME call
    that does S-Curve/J-Curve analysis, separately reviews the transcript
    and notes any phrases it saw that clearly mean the same thing as one
    of these categories but weren't a literal match. Those suggestions
    are stored alongside the official count (see fundamental_analysis.py)
    for a human to review and, if they hold up, fold into the phrase
    lists below — the lists are expected to grow over time.
  - LIVING DOCUMENT (confirmed): NOT this phrase list — the growing
    record is the per-symbol history in synthesis_history.py, which
    already stores the full verdict (including the "call_tone" block
    this module produces) every time a symbol is analyzed. Nothing new
    needed there; call_tone just rides along as another verdict field.

SCOPE PER CATEGORY (stated assumption, confirmed via the phrase source):
  - Congrats/kudos: analysts say this opening their own question, so it's
    counted from the Q&A section ONLY (see split_transcript below).
  - Uncertainty / certainty: management says these in both their prepared
    remarks AND their Q&A answers, so these are counted across the WHOLE
    transcript, not just Q&A.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Where Q&A starts — tried in order, first match wins. This is boilerplate
# nearly every Indian concall transcript uses (a handful of conferencing
# services supply almost all of them), but if NONE of these match, treat the
# transcript as unsplit rather than guessing — see split_transcript().
# ---------------------------------------------------------------------------
QA_BOUNDARY_MARKERS = [
    "question-and-answer session",
    "question and answer session",
    "first question is from the line of",
]

# ---------------------------------------------------------------------------
# Phrase lists — v1 seed set, expanded from the user's examples with
# realistic transcript phrasing. Expected to grow: add entries (don't
# rewrite categories) as real near-misses get reviewed and confirmed.
# ---------------------------------------------------------------------------
CONGRATS_PHRASES = [
    "congratulations",
    "congrats",
    "kudos",
    "well done",
    "great set of numbers",
    "great quarter",
    "fantastic quarter",
    "terrific quarter",
]

UNCERTAINTY_PHRASES = [
    # inability to quantify
    "unable to quantify",
    "inability to quantify",
    "difficult to quantify",
    "hard to quantify",
    "not in a position to quantify",
    # inability to provide guidance
    "unable to provide guidance",
    "not providing guidance",
    "not in a position to guide",
    "we don't guide",
    "do not guide",
    # blame macro environment
    "macro environment",
    "macro headwinds",
    "challenging macro",
    "external environment",
    "macro concerns",
    "macroeconomic headwinds",
    # no visibility on recovery
    "no visibility",
    "limited visibility",
    "lack of visibility",
    "visibility on recovery",
    "visibility remains low",
    "yet to see visibility",
    # cautious environment
    "cautious environment",
    "remain cautious",
    "cautious outlook",
    "cautious approach",
    "cautiously watchful",
    # customers taking longer to decide
    "taking longer to decide",
    "elongated decision",
    "delayed decision making",
    "slower decision cycle",
    "customers deferring",
    "deferring their decision",
    # general hedges
    "too early to say",
    "hard to predict",
    "wait and watch",
]

CERTAINTY_PHRASES = [
    # ahead of plan
    "ahead of plan",
    "ahead of schedule",
    # great response to product launch
    "great response",
    "strong response",
    "excellent response",
    "overwhelming response",
    # high demand
    "high demand",
    "strong demand",
    "robust demand",
    "healthy demand",
    # on track
    "on track",
    "well on track",
    # green shoots
    "green shoots",
    "early green shoots",
    "seeing green shoots",
    # general confidence
    "better than expected",
    "exceeded expectations",
    "strong momentum",
]


def split_transcript(text: str) -> tuple[str, str, bool]:
    """Splits a concall transcript into (prepared_remarks, qa_section,
    split_found). If no boundary marker is found, returns (text, text,
    False) — both halves point at the full transcript rather than
    guessing where Q&A starts, so callers can still count congrats
    (just less precisely scoped to the whole call) without special-casing
    a missing split everywhere."""
    lowered = text.lower()
    earliest_idx = None
    for marker in QA_BOUNDARY_MARKERS:
        idx = lowered.find(marker)
        if idx != -1 and (earliest_idx is None or idx < earliest_idx):
            earliest_idx = idx

    if earliest_idx is None:
        return text, text, False

    return text[:earliest_idx], text[earliest_idx:], True


def _normalize(text: str) -> str:
    """Collapses whitespace/newlines so a phrase split across a PDF-extracted
    line break still matches. Known limitation: a phrase split by something
    other than whitespace (e.g. a hyphenated line break) can still be
    missed — that's exactly the kind of gap the LLM near-miss flag exists to
    surface."""
    return re.sub(r"\s+", " ", text).strip().lower()


def count_phrases(text: str, phrase_list: list[str]) -> tuple[int, dict[str, int]]:
    """Case-insensitive, whitespace-normalized substring count. Returns
    (total_count, {phrase: count}) — the breakdown is what makes this
    auditable: not just "12 uncertainty mentions" but exactly which
    phrases, how many times each, re-checkable against the transcript.

    Overlap-safe by construction: several phrases in a category are
    deliberately substrings of a longer one in the same list (e.g. "green
    shoots" inside "seeing green shoots", "no visibility" inside
    "visibility on recovery") — that's intentional, since either the short
    or the long form alone is a real, distinct thing to catch. But the
    SAME occurrence in the text must only ever be counted once. Phrases
    are matched longest-first, and each match "claims" its character span;
    a shorter phrase is only counted where it lands OUTSIDE every span a
    longer phrase already claimed — so "seeing green shoots" counts once,
    not twice as itself plus "green shoots" again inside it."""
    normalized = _normalize(text)
    claimed: list[tuple[int, int]] = []  # (start, end) spans already counted
    breakdown: dict[str, int] = {}
    total = 0

    for phrase in sorted(phrase_list, key=len, reverse=True):
        needle = phrase.lower()
        n = 0
        start = 0
        while True:
            idx = normalized.find(needle, start)
            if idx == -1:
                break
            span = (idx, idx + len(needle))
            start = idx + 1  # allow overlapping occurrences of the SAME phrase to still each count
            if any(span[0] < c_end and span[1] > c_start for c_start, c_end in claimed):
                continue  # fully or partially inside an already-claimed (longer-phrase) span — skip
            claimed.append(span)
            n += 1
        if n:
            breakdown[phrase] = n
            total += n
    return total, breakdown


def _category_result(current_count: int, current_breakdown: dict, prior_count: int | None) -> dict:
    result = {
        "count": current_count,
        "breakdown": current_breakdown,
        "prior_quarter_count": prior_count,
    }
    if prior_count is None:
        result["trend"] = "no_prior_concall"
    elif current_count > prior_count:
        result["trend"] = "rising"
    elif current_count < prior_count:
        result["trend"] = "falling"
    else:
        result["trend"] = "flat"
    result["delta"] = None if prior_count is None else current_count - prior_count
    return result


def analyze_call_tone(current_transcript_text: str | None, prior_transcript_text: str | None) -> dict:
    """Top-level entry point. Both args are already-extracted transcript
    text (see pdf_text_extractor.extract_text_or_none) — pass None for
    either when that quarter's concall doesn't exist or extraction failed;
    this degrades gracefully rather than raising, same as the rest of the
    concall handling in this repo (concalls are optional everywhere else,
    so this needs to be too).

    Returns a dict with one key per category (congrats/uncertainty/
    certainty), each holding current count + breakdown + prior-quarter
    count + trend/delta — this whole dict is what gets stored as
    verdict_json["call_tone"] and rides along into synthesis_history.py
    automatically."""
    if current_transcript_text is None:
        return {
            "available": False,
            "reason": "No current-quarter concall transcript available.",
        }

    cur_prepared, cur_qa, cur_split_found = split_transcript(current_transcript_text)

    cur_congrats_count, cur_congrats_breakdown = count_phrases(cur_qa, CONGRATS_PHRASES)
    cur_uncertainty_count, cur_uncertainty_breakdown = count_phrases(current_transcript_text, UNCERTAINTY_PHRASES)
    cur_certainty_count, cur_certainty_breakdown = count_phrases(current_transcript_text, CERTAINTY_PHRASES)

    prior_congrats_count = prior_uncertainty_count = prior_certainty_count = None
    prior_split_found = None
    if prior_transcript_text is not None:
        _, prior_qa, prior_split_found = split_transcript(prior_transcript_text)
        prior_congrats_count, _ = count_phrases(prior_qa, CONGRATS_PHRASES)
        prior_uncertainty_count, _ = count_phrases(prior_transcript_text, UNCERTAINTY_PHRASES)
        prior_certainty_count, _ = count_phrases(prior_transcript_text, CERTAINTY_PHRASES)

    return {
        "available": True,
        "qa_boundary_found_this_quarter": cur_split_found,
        "qa_boundary_found_prior_quarter": prior_split_found,
        "congrats": _category_result(cur_congrats_count, cur_congrats_breakdown, prior_congrats_count),
        "uncertainty": _category_result(cur_uncertainty_count, cur_uncertainty_breakdown, prior_uncertainty_count),
        "certainty": _category_result(cur_certainty_count, cur_certainty_breakdown, prior_certainty_count),
    }
