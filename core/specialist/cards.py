# core/specialist/cards.py
# RPAR Epic #1262, Task T2 (#1264) - deterministic, LLM-free card builders.
"""Pure, deterministic card-field builders for the SpecialistReport.

These functions derive the *display-only* card fields (``pros`` / ``cons`` /
``summary`` / ``headlines``) from the signals already gathered by
``StockSpecialistAgent``. They are intentionally **pure**:

  * no I/O, no LLM, no network, no GPU - fully deterministic and unit-testable;
  * they never mutate their ``gathered`` input - every returned list is a fresh
    object built from scratch;
  * they never touch the score / recommendation / reasons - those are computed
    by ``_build_report`` *before* these helpers run, and these helpers only
    DERIVE from the same already-gathered signals (FINDINGS NEWS-8).

The caller (``_build_report``) invokes them only behind the
``SPECIALIST_CARDS_ENABLED`` flag (default OFF); with the flag OFF the report
keeps the V0 defaults (``[]`` / ``[]`` / ``""`` / ``[]``) and the serialized DTO
is byte-identical.
"""

from typing import Any, Dict, List, Tuple

# Max headlines surfaced on the card (mirrors the Group-A serializer cap, L1487).
_MAX_HEADLINES = 8

# Same thresholds that already drive the scoring bonus block in _build_report
# (stock_specialist.py L824-844). Single-sourced here as named constants so the
# card wording stays in lock-step with the score (imported by the scorer, #1310
# F-03). These are display/heuristic thresholds (no order/capital path reads
# them), so the rationale is inline rather than a regulatory ADR.
# ADR-T2-01: _INSIDER_CLUSTER_MIN = 3 (Form-4 filings)
#   Rationale: a single Form-4 is routine; >= 3 filings in the window signals
#   coordinated/cluster insider activity worth surfacing as a bullet.
_INSIDER_CLUSTER_MIN = 3
# ADR-T2-02: _REDDIT_BUZZ_MIN = 5 (mentions / 24h)
#   Rationale: < 5 daily mentions is noise for a single ticker; >= 5 marks a
#   social-buzz spike worth a watch bullet (not a score driver on its own).
_REDDIT_BUZZ_MIN = 5
# ADR-T2-03: _HIGH_SHORT_PCT = 25.0 (% of float short)
#   Rationale: ~25%+ short interest is the conventional "heavily shorted" line
#   (squeeze / pressure risk); below it short interest is unremarkable.
_HIGH_SHORT_PCT = 25.0


def build_pros_cons(gathered: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """Derive deterministic pros/cons bullets from the gathered signals.

    Bullish signals -> ``pros``; bearish signals -> ``cons``. Thresholds mirror the
    scoring bonus block in ``_build_report`` (single source of truth for the
    signal semantics); this function only reads ``gathered`` and never writes
    back. Both returned lists are new objects.
    """
    pros: List[str] = []
    cons: List[str] = []

    insider = gathered.get("insider_trades") or []
    if len(insider) >= _INSIDER_CLUSTER_MIN:
        pros.append(f"Cluster insider activity: {len(insider)} Form 4 filings")

    activists = gathered.get("activist_stakes") or []
    if activists:
        pros.append(f"Activist/large investor filing ({len(activists)} 13D/G)")

    political = gathered.get("political_trades") or []
    if political:
        pros.append(f"Congressional trading: {len(political)} transaction(s)")

    if gathered.get("wiki_spike"):
        pros.append("Wikipedia research spike - unusual public interest")

    reddit_mentions = gathered.get("reddit_mentions_24h", 0) or 0
    if reddit_mentions >= _REDDIT_BUZZ_MIN:
        sentiment = gathered.get("reddit_sentiment", "neutral")
        pros.append(f"Reddit buzz: {reddit_mentions} mentions in 24h ({sentiment})")

    events = gathered.get("material_events") or []
    if events:
        pros.append(f"Recent material events: {len(events)} 8-K filing(s)")

    short_pct = gathered.get("short_interest_pct")
    if short_pct is not None and short_pct > _HIGH_SHORT_PCT:
        cons.append(f"High short interest: {short_pct:.1f}%")

    return pros, cons


def build_summary(
    *,
    news_summary: str,
    alt_signals: str,
    recommendation: str,
    sentiment_score: float,
    existing_summary: str = "",
) -> str:
    """Deterministic 1-2 sentence summary fallback (no LLM).

    Forward-compat: if a producer already populated ``existing_summary`` (no
    producer does on today's main - ``summary`` is always empty), it is returned
    unchanged. Otherwise a deterministic sentence is composed from the
    already-derived fields. This is NEVER an LLM call (St2 = no-LLM).
    """
    if existing_summary:
        return existing_summary

    if news_summary:
        lead = news_summary.strip()
    elif alt_signals:
        lead = alt_signals.strip()
    else:
        lead = "No new headline or alternative-signal activity this cycle."

    return (
        f"{lead} Overall outlook: {recommendation} "
        f"(sentiment {sentiment_score:.0f}/100)."
    )


def select_headlines(gathered: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalise the gathered headline strings into the V0 ``List[Dict]`` shape.

    ``gathered["recent_headlines"]`` is a ``List[str]`` (``_fetch_polygon_news``
    yields ``item["title"]`` strings), but the V0 schema + serializer expect
    ``List[Dict[str, Any]]``. Each string is normalised to ``{"title": s}``
    (forward-compat to the bundle's headline shape), order preserved, capped at
    8. The returned list is a fresh object; the input is not mutated.
    """
    raw = gathered.get("recent_headlines") or []
    return [{"title": h} for h in raw[:_MAX_HEADLINES]]
