# core/specialist/insight_quality/prompt.py
# RPAR Epic #1262, Task T6b (#1271) - PR-1: variant-prompt routing (skeleton).
"""Variant-prompt routing for the insight-quality judge/rewrite path.

Owns the (pure) shaping of the prompt context the judge sees - including the
earnings-transcript inject which the bundle caps at <=1400 characters. PR-1 lands
only the pure helpers (cap + variant selection); the actual transcript FETCH
(``_fetch_earnings_transcript``) and the live prompt assembly are PR-2, still
behind ``INSIGHT_QUALITY_ENABLED``. No network / LLM here.
"""

from __future__ import annotations

# Bundle parity: the earnings-transcript snippet injected into the IQ prompt is
# capped at this many characters before it is added to the model context.
TRANSCRIPT_INJECT_MAX_CHARS = 1400


def cap_transcript(text: str, *, max_chars: int = TRANSCRIPT_INJECT_MAX_CHARS) -> str:
    """Cap a transcript snippet for prompt injection. Pure; no ``or``-default.

    ``None`` -> empty string; otherwise truncated to ``max_chars``.
    """
    if text is None:
        return ""
    if max_chars < 0:
        max_chars = 0
    return text[:max_chars]
