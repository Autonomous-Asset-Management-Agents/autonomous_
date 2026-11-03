# core/specialist/insight_quality/news.py
# RPAR Epic #1262, Task T6b (#1271) - PR-1: IQ-grounding news enrich (skeleton).
"""IQ-specific Google-News-RSS grounding enrich for the insight-quality path.

Scope note: this is the *insight-quality grounding* enrich only. It does NOT
overlap T3's ``_fetch_google_news`` merge into ``recent_headlines`` - that is a
separate path. Here we only supply extra grounding context for the grader/judge.

PR-1 lands the module shape (importable, side-effect-free, no network call at
import). The actual async RSS fetch is PR-2 (research()-wiring), still behind
``INSIGHT_QUALITY_ENABLED``. Until then nothing here is called from the engine.
"""

from __future__ import annotations

from typing import Any, Dict, List


def select_grounding_headlines(
    gathered: Dict[str, Any], *, limit: int = 8
) -> List[Dict[str, Any]]:
    """Pick already-gathered headlines for IQ grounding. Pure, no network.

    Reads ``gathered['recent_headlines']`` (populated upstream) and returns at
    most ``limit`` of them. No ``or``-default on the slice bound (P0-1): the
    limit is an explicit positive int argument.
    """
    headlines = gathered.get("recent_headlines")
    if headlines is None:
        return []
    if limit < 0:
        limit = 0
    return list(headlines[:limit])
