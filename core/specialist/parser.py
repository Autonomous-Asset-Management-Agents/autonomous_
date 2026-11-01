# core/specialist/parser.py
# RPAR Epic #1262, Task T1 (#1265) - V2 synthesis parser (flag-gated, dormant).
"""V2 synthesis parser - the 10-field parse of the LLM synthesis text.

This is the ``SPECIALIST_PROMPT_V2``-ON sibling of the legacy 6-tuple parser
``StockSpecialistAgent._parse_synthesis``. It reproduces the V1 score-deriving
tail **byte-for-byte** (NEWS-8 invariant) and *additionally* fills the four
prose fields the V2 prompt asks for (``company_summary``/``bull_case``/
``bear_case``/``investment_thesis``).

Hard contracts (reviewer-checked):

* **NEWS-8 / scoring invariant** - on the *same* LLM text, ``recommendation``,
  ``sentiment_score`` and ``confidence`` are identical to ``_parse_synthesis``.
  The score parsing (SUMMARY/SIGNALS/OUTLOOK/SCORE/REASONS), the recommendation
  alignment, the ``confidence`` formula and the reasons-fallback are copied
  verbatim from ``stock_specialist._parse_synthesis`` (L915-955 @ port time).
* **Purity** - a free function of ``str`` -> ``ParsedSynthesis``; it mutates no
  shared state. Calling it twice on the same input yields equal results.
* **P0-1** - no numeric is ``or``-defaulted (0.0 is a legitimate value); the
  SCORE parse uses an explicit guarded ``float(...)``.

The 280-char reasons cap that the bundle uses is intentionally *NOT* applied
here: the V1 path keeps its ``<3`` / ``[:120]`` reasons cap, and the consumer
(``_build_report``) re-derives the final ``reasons`` list anyway. T1 keeps the
reasons exactly V1 so the NEWS-8 ``reasons`` invariant holds flag-OFF vs ON.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Literal


@dataclass(frozen=True)
class ParsedSynthesis:
    """Named-field result of a synthesis parse (V1 and V2 share this shape).

    The first six fields mirror the legacy 6-tuple exactly (same order, same
    derivation) so the consumer can stay flag-agnostic. The four prose fields
    are empty on the V1 path and filled on the V2 path.
    """

    # --- V1 score-deriving fields (NEWS-8: identical to _parse_synthesis) ---
    news_summary: str = ""
    alternative_signals: str = ""
    recommendation: Literal["buy", "hold", "sell"] = "hold"
    sentiment_score: float = 50.0
    confidence: float = 0.5
    reasons: List[str] = field(default_factory=list)

    # --- V2 prose fields (empty on the V1 path) ---
    company_summary: str = ""
    bull_case: str = ""
    bear_case: str = ""
    investment_thesis: str = ""


# Single-line prose labels the V2 prompt asks for. DOTALL fallback so a model
# that wraps a field across lines (until the next known label or EOF) still
# parses. Order is irrelevant; each is matched independently.
_PROSE_LABELS = {
    "company_summary": "COMPANY",
    "bull_case": "BULL",
    "bear_case": "BEAR",
    "investment_thesis": "THESIS",
}

# Stop a DOTALL prose capture at the next known section label or end-of-text.
_STOP_LABELS = "SUMMARY|SIGNALS|OUTLOOK|SCORE|COMPANY|BULL|BEAR|THESIS|REASONS"


def _extract_prose(text: str, label: str) -> str:
    """Capture the text following ``LABEL:`` up to the next section label/EOF.

    DOTALL so multi-line prose is captured; trailing whitespace stripped.
    Pure - reads ``text`` only.
    """
    pattern = re.compile(
        rf"^\s*{label}:\s*(.*?)(?=^\s*(?:{_STOP_LABELS}):|\Z)",
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        return ""
    return match.group(1).strip()


def parse_synthesis_v2(text: str) -> ParsedSynthesis:
    """Parse the V2 LLM synthesis text into a :class:`ParsedSynthesis`.

    Reproduces ``StockSpecialistAgent._parse_synthesis`` byte-for-byte for the
    six score-deriving fields (NEWS-8) and additionally fills the four prose
    fields via a DOTALL fallback. Pure: no input mutation, no shared state.
    """
    # Empty-text branch - byte-identical to _parse_synthesis's early return.
    if not text:
        return ParsedSynthesis(
            news_summary="",
            alternative_signals="",
            recommendation="hold",
            sentiment_score=50.0,
            confidence=0.3,
            reasons=["Insufficient data for analysis"],
        )

    news_summary = ""
    alt_signals = ""
    recommendation: Literal["buy", "hold", "sell"] = "hold"
    sentiment_score = 50.0
    confidence = 0.4
    reasons: List[str] = []

    # --- V1 line-oriented parse (verbatim port of _parse_synthesis) ---
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("SUMMARY:"):
            news_summary = line[8:].strip()
        elif line.startswith("SIGNALS:"):
            alt_signals = line[8:].strip()
        elif line.startswith("OUTLOOK:"):
            raw = line[8:].strip().lower()
            if "bullish" in raw:
                recommendation = "buy"
            elif "bearish" in raw:
                recommendation = "sell"
            else:
                recommendation = "hold"
        elif line.startswith("SCORE:"):
            try:
                val = float(re.search(r"[\d.]+", line[6:]).group())
                sentiment_score = max(0.0, min(100.0, val))
            except Exception:
                pass
        elif line.startswith("- ") and len(reasons) < 3:
            reasons.append(line[2:].strip()[:120])

    # Align recommendation with score if the model didn't match (verbatim).
    if sentiment_score >= 70 and recommendation == "hold":
        recommendation = "buy"
    elif sentiment_score <= 35 and recommendation == "hold":
        recommendation = "sell"

    confidence = min(0.9, 0.3 + abs(sentiment_score - 50) / 100)
    if not reasons:
        reasons = [f"Gemini score: {sentiment_score:.0f}/100"]

    # --- V2 prose fields (DOTALL fallback; empty when absent) ---
    prose = {attr: _extract_prose(text, label) for attr, label in _PROSE_LABELS.items()}

    return ParsedSynthesis(
        news_summary=news_summary,
        alternative_signals=alt_signals,
        recommendation=recommendation,
        sentiment_score=sentiment_score,
        confidence=confidence,
        reasons=reasons,
        company_summary=prose["company_summary"],
        bull_case=prose["bull_case"],
        bear_case=prose["bear_case"],
        investment_thesis=prose["investment_thesis"],
    )
