# core/specialist/news.py
# RPAR Epic #1262, Task T3 (#1267) - Google + Polygon news-source parity.
"""Pure, deterministic headline-merge for the Stock Specialist.

The Stock Specialist gathers Polygon headlines today. Task T3 (behind the
``SPECIALIST_NEWS_V2`` flag, default OFF) adds a Google-News-RSS source and
merges both lists here. Keeping the merge in a pure function (no agent state,
no network) makes the single Bundle-reconcilable rule - order + dedup + cap -
isolated and fully unit-testable.

Contract (coordinated with T2 ``select_headlines`` and the synthesis prompt at
``stock_specialist._build_synthesis_prompt``): the merged list stays
``List[str]`` - the same element shape ``recent_headlines`` has today. The
str->dict normalisation for the card lives solely in T2 (single responsibility).
"""

from typing import List

# Default merge order reconciled against the Bundle snapshot (FINDINGS NEWS-2):
# Google-News headlines first, then Polygon. Dedup is case-insensitive on the
# trimmed title; the first occurrence (Google, by the order above) wins.
_DEFAULT_CAP = 10


def merge_headlines(
    polygon: List[str], google: List[str], *, cap: int = _DEFAULT_CAP
) -> List[str]:
    """Merge Polygon + Google headlines into one capped, deduplicated list.

    Order: Google-first, then Polygon (reconciled Bundle rule). Dedup is
    case-insensitive on the trimmed title - the first occurrence wins. Output is
    truncated to ``cap`` (default 10). Inputs are never mutated; a new list is
    returned. Empty-tolerant: ``merge_headlines([], [])`` returns ``[]``.

    Returns ``List[str]`` (never ``List[dict]``) to preserve the
    ``recent_headlines`` contract with the synthesis prompt and T2.
    """
    merged: List[str] = []
    seen: set = set()
    # Google-first, then Polygon; both iterated in their own order.
    for headline in list(google) + list(polygon):
        if not isinstance(headline, str):
            continue
        key = headline.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(headline)
        if len(merged) >= cap:
            break
    return merged
