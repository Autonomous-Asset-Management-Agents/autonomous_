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


def _data_sections(
    symbol: str, gathered: Dict[str, Any], *, number_headlines: bool = False
) -> List[str]:
    """Build the raw-data section lines (lock-step with the V1 builder).

    Pure: reads ``gathered`` only. Mirrors ``_build_synthesis_prompt`` so the
    flag-ON prompt differs from flag-OFF only in the YOUR-TASK/format block.

    ``number_headlines`` (grounding mode, default OFF = byte-identical) prefixes
    each headline with a ``[H#]`` index so the model can cite the single headline
    that supports each bull/bear/thesis sentence — the citation the grounding gate
    (``core/specialist/grounding.py``) verifies mechanically.
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
        for i, h in enumerate(headlines[:6], start=1):
            lines.append(f"- [H{i}] {h}" if number_headlines else f"- {h}")
        lines.append("")

    # Direction A: the system's OWN model signals as citable sources — numbered
    # in the SAME [H#] sequence directly after the news headlines, so a thesis
    # sentence can tie to a model line exactly like to a headline and the
    # grounding gate verifies the echoed numbers against these strings. The key
    # is only ever set under SPECIALIST_GROUNDING_ENABLED (stock_specialist),
    # so the flag-OFF prompt stays byte-identical.
    model_lines = gathered.get("model_signal_lines", []) or []
    if model_lines:
        base = len(headlines[:6])
        lines.append("### Own model signals (this system's quantitative view)")
        for i, m in enumerate(model_lines, start=base + 1):
            lines.append(f"- [H{i}] {m}" if number_headlines else f"- {m}")
        lines.append("")

    insider = gathered.get("insider_trades", [])
    if insider:
        lines.append(f"### Insider Trades ({len(insider)} filings)")
        for t in insider[:5]:
            _dir = t.get("direction", "")
            lines.append(
                f"- {t.get('filed', '')} | {t.get('filer', '')} | {t.get('form', '')}"
                + (f" | {_dir}" if _dir else "")
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


def build_prompt_v2(
    symbol: str,
    gathered: Dict[str, Any],
    *,
    require_headline_tags: bool = False,
) -> str:
    """Build the V2 synthesis prompt (data sections + V1 tasks + 4 prose tasks).

    Pure: reads ``gathered`` only; returns a string. Reached only under
    ``SPECIALIST_PROMPT_V2`` ON.

    ``require_headline_tags`` (grounding mode, default OFF = byte-identical to the
    existing V2 prompt) numbers the headlines ``[H#]`` and instructs the model to
    end every bull/bear/thesis sentence with the ``[H#]`` tag of the single
    headline that supports it (or ``NONE``). The grounding gate re-verifies the
    tag mechanically — the instruction is only a hint, never trusted.
    """
    lines = _data_sections(symbol, gathered, number_headlines=require_headline_tags)
    if require_headline_tags:
        prose_tasks = [
            "6. Write a 2-3 sentence company description: what the business does "
            "and how it makes money, what its MAIN market/segment is, and roughly "
            "how large the company is (qualitative scale like 'one of the largest "
            "US banks' — do NOT invent precise figures).",
            "7. Write the strongest bull case in 2-3 complete sentences, like a "
            "senior equity analyst: each sentence names the concrete catalyst from "
            "a headline AND the second-order implication an investor cares about — "
            "what it means for revenue, margins, pricing power, competitive "
            "position, or investor confidence. Do NOT paraphrase the headline; "
            "interpret it (e.g. from a failed product launch, infer what it "
            "signals about the company's ability to innovate). No fragments, no "
            "generic filler. Every sentence MUST end with the [H#] tag of the "
            "single headline that supports it.",
            "8. Write the strongest bear case in 2-3 complete sentences: each names "
            "the concrete risk from a headline AND the second-order implication — "
            "what it means for future earnings, market share, sentiment, or the "
            "company's strategic position. Do NOT paraphrase the headline; "
            "interpret it. No fragments, no generic filler. Every sentence MUST "
            "end with the [H#] tag of the single headline that supports it.",
            "9. Write an actionable investment thesis in 2-3 complete sentences. "
            "THE SYSTEM'S OWN MODEL SIGNALS ARE THE PRIMARY EVIDENCE — when "
            "'Own model signals' lines are present, START from the quantitative "
            "view (ranking, expected price range) and use the news ONLY to "
            "support, contextualize, or challenge it; a news story must never be "
            "the main argument on its own. If no model signals are present, say "
            "plainly that the quantitative view is unavailable this cycle and "
            "treat any news-based lean as tentative. State which side the "
            "combined evidence favors and why. Every sentence MUST end with the "
            "[H#] tag of the single source line that supports it.",
            "If no headline supports a bull/bear/thesis case, write exactly NONE for "
            "that field. Do NOT invent facts, companies, or numbers not in the data. "
            "NEVER repeat or lightly reword a headline as your sentence — every "
            "sentence must ADD interpretation the headline itself does not say. "
            "The volatility range is a RISK WIDTH with no direction: NEVER cite it "
            "as support for a bullish or bearish view ('room for upside' from a "
            "symmetric range is a forbidden argument); use it only to describe "
            "expected movement size or risk.",
        ]
    else:
        prose_tasks = [
            "6. Write a 1-2 sentence company description (what the business does).",
            "7. Write the strongest bull case in 1-2 sentences.",
            "8. Write the strongest bear case in 1-2 sentences.",
            "9. Write a 1-2 sentence actionable investment thesis.",
        ]
    lines += [
        "## YOUR TASK",
        "Based ONLY on the data above:",
        "1. Write a 2-sentence news/event summary.",
        "2. Write a 1-sentence alternative signal summary (insider activity, political trades, social signals).",
        "3. Give an overall outlook: bullish / neutral / bearish.",
        "4. Give a sentiment score 0-100 that MATCHES your outlook and the "
        "strength of the evidence: bullish evidence -> 60-90, bearish -> 10-40; "
        "use ~50 ONLY when the evidence is genuinely mixed. Do NOT default to 50.",
        "5. List up to 3 key reasons (one line each).",
        *prose_tasks,
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
