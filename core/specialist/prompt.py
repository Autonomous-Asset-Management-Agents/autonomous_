# core/specialist/prompt.py
# RPAR Epic #1262, Task T1 (#1265) - V2 synthesis prompt builder (flag-gated, dormant).
"""V2 synthesis prompt - the ``SPECIALIST_PROMPT_V2``-ON prompt builder.

This is the richer sibling of ``StockSpecialistAgent._build_synthesis_prompt``.
It builds the **same raw-data sections** (headlines / insider / events /
activists / political / social / alt-data) and then asks the model for four
additional prose deliverables - COMPANY / BULL / BEAR / THESIS - on top of the
five V1 tasks (SUMMARY / SIGNALS / OUTLOOK / SCORE / REASONS).

Hard contracts (reviewer-checked):

* **Dormancy** - this builder is only reached when ``SPECIALIST_PROMPT_V2`` is
  ON. With the flag OFF, ``_build_synthesis_prompt`` is used unchanged, so the
  emitted prompt is byte-identical to today.
* **Purity** - a free function of ``(symbol, gathered)`` -> ``str``; it reads
  ``gathered`` only and never mutates it.
* **P0-1** - numeric optionals (``short_interest_pct`` / ``google_trend_score``)
  are gated with ``is not None`` (0.0 is a legitimate value), exactly as V1.

The data-section block is kept deliberately in lock-step with the V1 builder so
the only ON-vs-OFF prompt delta is the additional prose tasks + format lines.
"""

from __future__ import annotations

from typing import Any, Dict, List


def _data_sections(symbol: str, gathered: Dict[str, Any]) -> List[str]:
    """Build the raw-data section lines (lock-step with the V1 builder).

    Pure: reads ``gathered`` only. Mirrors ``_build_synthesis_prompt`` so the
    flag-ON prompt differs from flag-OFF only in the YOUR-TASK/format block.
    """
    lines = [
        f"You are a stock research analyst. Analyse {symbol} using ONLY the data below.",
        "Do NOT use external knowledge or search. Synthesise only what is provided.",
        "",
        f"## RAW DATA FOR {symbol}",
        "",
    ]

    headlines = gathered.get("recent_headlines", [])
    if headlines:
        lines.append("### Recent Headlines")
        for h in headlines[:6]:
            lines.append(f"- {h}")
        lines.append("")

    insider = gathered.get("insider_trades", [])
    if insider:
        lines.append(f"### Insider Trades ({len(insider)} filings)")
        for t in insider[:5]:
            lines.append(
                f"- {t.get('filed', '')} | {t.get('filer', '')} | {t.get('form', '')}"
            )
        lines.append("")

    events = gathered.get("material_events", [])
    if events:
        lines.append(f"### Material Events / 8-K Filings ({len(events)})")
        for e in events[:3]:
            lines.append(f"- {e.get('filed', '')} | {e.get('entity', '')}")
        lines.append("")

    activists = gathered.get("activist_stakes", [])
    if activists:
        lines.append(
            f"### Activist/Large Investor Disclosures ({len(activists)} 13D/G filings)"
        )
        for a in activists[:3]:
            lines.append(
                f"- {a.get('filed', '')} | {a.get('filer', '')} | {a.get('form', '')}"
            )
        lines.append("")

    political = gathered.get("political_trades", [])
    if political:
        lines.append(f"### Congressional Trading ({len(political)} transactions)")
        for p in political[:3]:
            lines.append(
                f"- {p.get('date', '')} | {p.get('politician', '')} | {p.get('transaction', '')} "
                f"| {p.get('amount', '')}"
            )
        lines.append("")

    reddit_mentions = gathered.get("reddit_mentions_24h", 0)
    reddit_sent = gathered.get("reddit_sentiment", "neutral")
    if reddit_mentions > 0:
        lines.append("### Social Signal")
        lines.append(
            f"- Reddit mentions (24h): {reddit_mentions} | Sentiment: {reddit_sent}"
        )
        lines.append("")

    wiki_spike = gathered.get("wiki_spike", False)
    wiki_views = gathered.get("wiki_views_7d", 0)
    if wiki_spike or wiki_views > 1000:
        lines.append("### Alternative Data")
        if wiki_spike:
            lines.append(
                f"- Wikipedia: SPIKE detected (views 7d: {wiki_views:,}) - unusual research interest"
            )
        short_pct = gathered.get("short_interest_pct")
        if short_pct is not None:
            lines.append(f"- Short interest: {short_pct:.1f}% of volume")
        google_score = gathered.get("google_trend_score")
        if google_score is not None:
            lines.append(f"- Google Trends score (7d): {google_score:.0f}/100")
        lines.append("")

    return lines


def build_prompt_v2(symbol: str, gathered: Dict[str, Any]) -> str:
    """Build the V2 synthesis prompt (data sections + V1 tasks + 4 prose tasks).

    Pure: reads ``gathered`` only; returns a string. Reached only under
    ``SPECIALIST_PROMPT_V2`` ON.
    """
    lines = _data_sections(symbol, gathered)
    lines += [
        "## YOUR TASK",
        "Based ONLY on the data above:",
        "1. Write a 2-sentence news/event summary.",
        "2. Write a 1-sentence alternative signal summary (insider activity, political trades, social signals).",
        "3. Give an overall outlook: bullish / neutral / bearish.",
        "4. Give a sentiment score 0-100 (50=neutral, 75+=bullish, 25-=bearish).",
        "5. List up to 3 key reasons (one line each).",
        "6. Write a 1-2 sentence company description (what the business does).",
        "7. Write the strongest bull case in 1-2 sentences.",
        "8. Write the strongest bear case in 1-2 sentences.",
        "9. Write a 1-2 sentence actionable investment thesis.",
        "",
        "Format your response EXACTLY as:",
        "SUMMARY: <2 sentences>",
        "SIGNALS: <1 sentence>",
        "OUTLOOK: <bullish|neutral|bearish>",
        "SCORE: <0-100>",
        "COMPANY: <1-2 sentence company description>",
        "BULL: <strongest bull case>",
        "BEAR: <strongest bear case>",
        "THESIS: <actionable investment thesis>",
        "REASONS:",
        "- <reason 1>",
        "- <reason 2>",
        "- <reason 3>",
    ]
    return "\n".join(lines)
