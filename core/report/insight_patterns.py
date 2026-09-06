# core/report/insight_patterns.py
# RPT-7 (Epic #1998) — the deterministic INSIGHT-PATTERN layer: the code detects
# the deep analytical patterns in a FactSet, DERIVES the logical implication
# itself, and grounds every claim. The narrator only phrases it.
"""Deterministic, LLM-free insight-pattern detectors: FactSet -> grounded Insights.

This is the *insight* layer of the auditable report pipeline (Epic #1998). Where
the renderer (RPT-3) states the mechanical facts and the narrator (RPT-5) phrases
the three prose slots, this module produces the report's **deep, grounded
insights** — and it does so *deterministically*: the CODE recognises each
analytical pattern in the frozen :class:`~core.report.fact_set.FactSet`, and the
CODE derives the logical connection (the *implication*). No language model is
involved in the derivation; a narrator may later phrase the finished implication,
but it can neither invent nor alter the logic.

This is the direct answer to the epic's proven residual danger — a weak model
*fusing facts wrongly* in a free synthesis slot ("Burry schaetzte die $5-Bio
Marktkap"). By moving the *reasoning* into code (pattern condition + provenanced
grounding + code-assembled implication), the substance of an insight can no
longer be hallucinated; only its wording is delegated.

Design contract (BORA / pure core):

* **Deterministic-first / code-derived.** Each detector reads only FactSet
  ``Fact`` values, evaluates a boolean pattern condition, and — when it holds —
  assembles the implication by f-string from those same computed values. Given
  the same FactSet it returns identical :class:`Insight` objects.
* **Pure & LLM-free.** No I/O, no persistence backend, no network, **no LLM**,
  no GPU, no ``pandas``, no clock (``datetime.now``), no randomness. It is
  edition-agnostic (no Redis / SQLite / ``DEPLOYMENT_MODE`` / edition
  special-casing). The connection (implication) is CODE, never a model.
* **Honest, never forced.** A detector fires ONLY when its condition is met; an
  absent pattern yields no Insight (``detect_insights`` returns fewer, or zero,
  entries) rather than a manufactured one. Every grounding field carries the
  provenance (``Fact.src``) of the FactSet fact it was computed from, so the
  rendered Insights section audits clean against the RPT-4 gate.
* **Evidence is QUOTED, never captioned.** A grounding's text must come out of
  this symbol's FactSet — a headline verbatim, or a number computed from a Fact.
  ⚠ Every detector here once shipped hand-written prose about ONE symbol's news
  ("Cerebras/Rivalen (Custom-Silicon...)", "(Burry, Chip-Breite)") with
  ``fs.news_items.src`` attached, so an Apple report ran Nvidia's competitive
  thesis under a real citation. Provenance next to an invented sentence is worse
  than no provenance: it reads as the best-sourced line on the page, and the
  mechanical auditor cannot see it (it checks where NUMBERS come from, not
  sentences). A ``src`` proves a FEED WAS ASKED — never that the text beside it
  is that feed's answer.
* **Nothing about an event's timing, ever.** ``FactSet`` has no earnings/event
  field. Any "Ergebnis-Termin (in Tagen / kurz nach dem Stichtag)" is therefore
  fabrication for every symbol on every run — it was removed, not softened. That a
  headline DISCUSSES results may be shown by quoting the headline; when the event
  lands cannot be stated here at all.

Report-only and DORMANT: nothing here is read by the decision path;
``SPECIALIST_ALPHA_WEIGHT`` stays 0.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Mapping, Optional, Sequence

from core.report.fact_set import Fact, FactSet, NewsItem

# ---------------------------------------------------------------------------
# Tunables (all display/heuristic — no order or capital path reads them).
# ---------------------------------------------------------------------------
# ADR-RPT7-01: valuation-vs-growth thresholds. A trailing PEG (= P/E divided by
#   revenue-growth-%) below 1.0 means the multiple is not stretched relative to
#   growth; combined with a low debt/equity balance this reframes an
#   "overvaluation" headline as a crowded TRADE, not an expensive STOCK. These
#   are display/insight heuristics only (report-only, dormant).
_PEG_STRETCHED = 1.0
_LOW_DEBT_MAX = 0.5

# ADR-RPT7-02: the model's ranking horizon (~5 trading days) is "short". When a
#   long-horizon structural threat and a short-horizon catalyst coexist in the
#   news, the structural story is noise FOR THIS HORIZON and the near-term
#   catalyst is the signal. A generous cap (~1 trading month) keeps the
#   separation meaningful without hard-coding the exact 5.
_SHORT_HORIZON_MAX_DAYS = 21

# ADR-RPT7-03: regime / momentum thresholds for the four COVERAGE detectors below
#   (trend-regime, vol-regime, rank-momentum, own-history-extreme). Every one is a
#   display/insight heuristic (report-only, dormant — no order or capital path
#   reads them). They exist so a detector fires ONLY on a CLEAR pattern and stays
#   silent on a flat/ambiguous one — an always-firing detector is a bug (the whole
#   reason RPT-7 was rewritten). The RAW numbers these interpret already appear in
#   the price/trend and quant sections; these detectors add the SYNTHESIS, not the
#   number a second time.
#
# Vol-regime: the short (20d) vs the long (63d) realized-vol window. A ratio at
#   least _VOL_REGIME_RATIO apart from 1.0 in either direction is a REGIME
#   (expanding / calming); the band in between is "no clear change" -> no insight.
#   1.15 ~ a 15 % gap in realized vol, comfortably outside day-to-day noise.
_VOL_REGIME_RATIO = 1.15
# Own-history extreme: the score's percentile against its OWN past. Only the tails
#   (top / bottom decile) count as an "extreme"; the middle is unremarkable.
_OWN_EXTREME_HIGH = 90.0
_OWN_EXTREME_LOW = 10.0
# Rank-momentum needs a real trajectory, not two points: at least this many scores
#   in the trend window, moving STRICTLY one direction (flat / wiggly -> no fire).
_RANK_TREND_MIN_POINTS = 3

# Deterministic news classifiers (lower-cased substring match against the title).
# Structural = multi-quarter competitive / supply-side threats (slow burn).
#
# ⚠ NO COMPANY NAMES. The list carried "cerebras" and "broadcom" — one symbol's
# rivals, hard-coded into a classifier every symbol runs through. They were also
# redundant (NVDA's own headline matches on "rival"), and actively wrong for the
# named firms themselves: a Broadcom headline in Broadcom's report is not evidence
# of competition against Broadcom. Only symbol-neutral TOPIC words belong here.
# ⚠ NO OVER-BROAD TOPICS EITHER. "ipo" was dropped: a "SpaceX IPO" headline is not
# a competitive threat to the reported symbol, yet it matched the structural leg on
# every symbol and let the horizon pattern fire on unrelated news (GOOGL/AAPL ran
# NVDA's story grounded on SpaceX-IPO articles). A structural threat must be a
# COMPETITIVE / market-share topic, not any capital-markets event.
_STRUCTURAL_KEYWORDS: Sequence[str] = (
    "rival",
    "competit",
    "custom silicon",
    "custom chip",
    "market share",
    "moat",
)
# Event = a dated, binary near-term catalyst (earnings / results / guidance).
_EVENT_KEYWORDS: Sequence[str] = (
    "earnings",
    "conference call",
    "financial results",
    "results",
    "guidance",
    "report first-quarter",
)
# Sentiment = short-horizon positioning / risk-appetite moves (not a demand break).
# ⚠ "rally" (bullish — contradicts the selloff framing the sentiment leg supports)
# and "snap" ("stocks snap a losing streak" — a generic market wrap) were dropped:
# both matched upbeat headlines and mislabelled them as selloff drivers. Only
# risk-off / positioning words belong here.
_SENTIMENT_KEYWORDS: Sequence[str] = (
    "swooning",
    "selloff",
    "sell-off",
    "blame",
    "short",
    "puts",
    "plunge",
    "slump",
    "tumble",
    "downgrade",
    "fear",
    "worry",
)


# ---------------------------------------------------------------------------
# Value object.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Insight:
    """One detected, grounded insight.

    ``pattern_name`` names the detector. ``condition_met`` is ``True`` for every
    Insight :func:`detect_insights` returns (a non-fired pattern yields no
    Insight at all). ``grounding`` maps a short key to a :class:`Fact` whose
    ``value`` is the display string and whose ``src`` is the provenance of the
    FactSet fact it was computed from — so every number carries a source.
    ``implication`` is the CODE-derived logical conclusion (deterministic
    f-string assembly, never an LLM). ``caveat`` is the honest limit of the
    claim. ``narration_slot`` is the ``narration`` key a downstream narrator may
    fill to phrase the implication in prose; the deterministic implication is the
    default and the audit anchor.
    """

    pattern_name: str
    condition_met: bool
    grounding: Mapping[str, Fact]
    implication: str
    caveat: str
    narration_slot: str


# ---------------------------------------------------------------------------
# Small deterministic formatting helpers.
# ---------------------------------------------------------------------------
def _num(value: object, nd: int = 2) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.{nd}f}"
    except (TypeError, ValueError):
        return "n/a"


def _pct(value: object, nd: int = 1) -> str:
    """A fraction fact (realized vol 0.349) rendered as a percent ("34.9")."""
    if value is None:
        return "n/a"
    try:
        return f"{float(value) * 100:.{nd}f}"
    except (TypeError, ValueError):
        return "n/a"


def _trend(values: Optional[Sequence[float]]) -> str:
    if not values:
        return "n/a"
    return " -> ".join(f"{float(v):.2f}" for v in values)


def _matches(title: str, keywords: Sequence[str]) -> bool:
    low = title.lower()
    return any(k in low for k in keywords)


def _classify(news: Sequence[NewsItem], keywords: Sequence[str]) -> List[NewsItem]:
    return [n for n in news if _matches(n.title, keywords)]


# The number of matched headlines quoted as evidence (a grounding line stays one
# readable line; the full PIT list is already its own report section).
_QUOTE_LIMIT = 2


def _quote_titles(items: Sequence[NewsItem], limit: int = _QUOTE_LIMIT) -> str:
    """Quote matched headlines VERBATIM as the evidence text.

    ⚠ This is the whole point of the RPT-7 fix. The grounding text used to be a
    hand-written summary of ONE symbol's news ("Cerebras/Rivalen (Custom-Silicon,
    Mehr-Quartals-These)") with ``fs.news_items.src`` pinned beside it. The src was
    real, the sentence was not, and the pairing made the fabrication read as the
    best-evidenced line in the report. Evidence must be a substring of the FactSet,
    not a caption placed next to a citation.
    """
    return " | ".join(f"'{n.title}' ({n.publisher})" for n in items[:limit])


# ---------------------------------------------------------------------------
# DETECTOR 1 — Divergence: model rank rises while price falls, fundamentals intact.
# ---------------------------------------------------------------------------
def divergence_pattern(fs: FactSet) -> Optional[Insight]:
    """Score-up / price-down / fundamentals-intact -> asymmetric event risk.

    Fires only when all three hold: the cross-sectional score TREND is rising,
    the recent price move is negative, AND fundamentals confirm the business is
    intact (revenue YoY > 0). Without a fundamentals feed the code cannot confirm
    "intact", so the pattern honestly does not fire.
    """
    trend = fs.lstm_score_trend.value or []
    score_rising = len(trend) >= 2 and trend[-1] > trend[0]

    pullback = fs.pullback_pct.value
    price_falling = pullback is not None and pullback < 0

    fund = fs.fundamentals.value or {}
    rev_yoy = fund.get("revenue_yoy_pct")
    fundamentals_intact = rev_yoy is not None and rev_yoy > 0

    if not (score_rising and price_falling and fundamentals_intact):
        return None

    pct = fs.lstm_xsec_percentile.value
    n = fs.lstm_xsec_n.value
    net_margin = fund.get("net_margin_pct")

    grounding: Dict[str, Fact] = {
        "score_trend": Fact(_trend(trend), fs.lstm_score_trend.src),
        "cross_section": Fact(
            f"{_num(pct, 1)}. Querschnitts-Perzentil (von {n} bewerteten Titeln)",
            fs.lstm_xsec_percentile.src,
        ),
        "pullback": Fact(
            f"{_num(pullback, 1)}% ueber die letzten Schlusskurse",
            fs.pullback_pct.src,
        ),
        "fundamentals": Fact(
            f"+{_num(rev_yoy, 1)}% Umsatz YoY, {_num(net_margin, 1)}% Nettomarge (intakt)",
            fs.fundamentals.src,
        ),
    }

    # ⚠ The "selloff_type" line asserted that the SELLOFF DRIVERS ARE sentiment
    # headlines — sourced to news_items — from a detector that never once read
    # news_items. It fired on a FactSet with zero news just as confidently. The
    # claim is only made now when sentiment headlines actually exist, and it is made
    # BY QUOTING THEM.
    sentiment = _classify(fs.news_items.value or [], _SENTIMENT_KEYWORDS)
    if sentiment:
        grounding["selloff_type"] = Fact(_quote_titles(sentiment), fs.news_items.src)

    # CODE derives the implication (never the LLM):
    # ⚠ The conclusion used to run "...macht das Risiko in den Ergebnis-Termin nach
    # dem Stichtag eher asymmetrisch". The FactSet has no earnings field: there was
    # no date, no event, and no way to check one — the asymmetry was hung on a
    # fixture that does not exist.
    # ⚠ REST-DEFEKT #1: it then called the pullback "stimmungsgetrieben"
    # UNCONDITIONALLY — a claim about the news, made in a sentence emitted even when
    # the FactSet held ZERO news. Split in two: sentence 1 is the read-across the
    # THREE NUMBERS alone support (rank up + price down + revenue up -> the fall is
    # not tracking the business -> a positioning/flow move, flagged as a Lesart);
    # the SENTIMENT half is added only when real sentiment headlines exist, and it
    # QUOTES the strongest one verbatim rather than asserting one from nothing.
    implication = (
        f"Weil der Modell-Rang stieg ({_num(trend[0], 2)} -> {_num(trend[-1], 2)}, "
        f"{_num(pct, 1)}. Perzentil) waehrend der Kurs {_num(pullback, 1)}% zurueckkam "
        f"und die Fundamentals intakt blieben (+{_num(rev_yoy, 1)}% Umsatz), ist der "
        f"Rueckgang eher positionierungsgetrieben als eine Geschaefts-Verschlechterung "
        f"(kein Beweis, nur eine Lesart)."
    )
    if sentiment:
        implication += (
            f" Die belegten Titel zum Rueckgang sind Stimmungs-Meldungen "
            f"('{sentiment[0].title}'), kein Nachfrage-Bruch — der Rueckgang ist damit "
            f"eher stimmungsgetrieben als fundamental."
        )
    caveat = (
        "Score = relativer Querschnitts-Rang, keine Return-Prognose; steigender "
        "Score ist ein Datenpunkt, kein Beweis — Risiko-Form-Lesart, kein Kursziel."
    )
    return Insight(
        pattern_name="divergence_pattern",
        condition_met=True,
        grounding=grounding,
        implication=implication,
        caveat=caveat,
        narration_slot="insight_divergence_pattern",
    )


# ---------------------------------------------------------------------------
# DETECTOR 2 — Valuation vs growth: PEG < 1 on a low-debt balance sheet.
# ---------------------------------------------------------------------------
def valuation_vs_growth_pattern(fs: FactSet) -> Optional[Insight]:
    """PEG < 1 on a low-debt balance -> the multiple is not stretched.

    PEG is derived as P/E divided by the revenue-growth-%. When it is well below
    1 AND debt/equity is low, the "overvaluation" narrative confuses a crowded
    trade with an expensive stock. Fires only when both hold and the inputs
    exist.
    """
    fund = fs.fundamentals.value or {}
    pe = fund.get("pe")
    rev_yoy = fund.get("revenue_yoy_pct")
    de_ratio = fund.get("debt_to_equity")
    if pe is None or not rev_yoy or de_ratio is None:
        return None

    peg = pe / rev_yoy
    peg_below_1 = peg < _PEG_STRETCHED
    low_debt = de_ratio < _LOW_DEBT_MAX
    if not (peg_below_1 and low_debt):
        return None

    grounding: Dict[str, Fact] = {
        "peg": Fact(
            f"trailing PEG {_num(peg, 2)} (P/E {_num(pe, 1)} / +{_num(rev_yoy, 1)}% Wachstum)",
            fs.fundamentals.src,
        ),
        "balance": Fact(
            f"Verb/EK {_num(de_ratio, 2)} (schuldenarm)", fs.fundamentals.src
        ),
    }
    # ⚠ A "headlines" line stated that the overvaluation warnings ARE headlines
    # "(Burry, Chip-Breite)" — one investor and one sector debate from a single
    # symbol's news cycle, emitted for every symbol, sourced to a news feed this
    # detector never reads. Dropped rather than reworded: the pattern is a
    # fundamentals statement (PEG + balance sheet) and needs no news to stand.
    implication = (
        f"Weil das trailing PEG {_num(peg, 2)} deutlich unter 1 liegt und die Bilanz "
        f"schuldenarm ist (Verb/EK {_num(de_ratio, 2)}), ist das Multiple relativ zum "
        f"Wachstum nicht gestreckt. Das bindende Risiko ist damit eher, ob die "
        f"+{_num(rev_yoy, 1)}%-Wachstumsbahn haelt, als die Bewertung selbst."
    )
    caveat = (
        "trailing PEG (keine Forward-Estimates lokal); bei scharfer "
        "Wachstums-Verlangsamung schwaecht sich das Argument ab. Nicht als "
        "'guenstig' oder 'Kauf' lesen."
    )
    return Insight(
        pattern_name="valuation_vs_growth_pattern",
        condition_met=True,
        grounding=grounding,
        implication=implication,
        caveat=caveat,
        narration_slot="insight_valuation_vs_growth_pattern",
    )


# ---------------------------------------------------------------------------
# DETECTOR 3 — Horizon separation: long-structural vs short-catalyst news.
# ---------------------------------------------------------------------------
def horizon_separation_pattern(fs: FactSet) -> Optional[Insight]:
    """Long-horizon structural threat + short-horizon catalyst vs a short model.

    Fires when the PIT news mixes at least one long-horizon structural item
    (competition / market-share topic) with at least one short-horizon catalyst
    (a results headline or a sentiment move) AND the model's ranking horizon is
    short. For that horizon the structural story is noise and the near catalyst is
    the signal. The matched headlines themselves are the evidence — this detector
    describes the symbol's OWN news or it does not fire.
    """
    news = fs.news_items.value or []
    structural = _classify(news, _STRUCTURAL_KEYWORDS)
    event = _classify(news, _EVENT_KEYWORDS)
    sentiment = _classify(news, _SENTIMENT_KEYWORDS)

    card = fs.model_card.value or {}
    horizon = card.get("horizon_days")
    short_model = horizon is not None and horizon <= _SHORT_HORIZON_MAX_DAYS

    # ⚠ REST-DEFEKT #2: structural[0] and the catalyst could be the SAME headline —
    # "Rival's earnings selloff" matches structural (rival), event (earnings) AND
    # sentiment (selloff) — which would quote ONE headline as BOTH sides of a
    # "separation" that isn't there, collapsing the whole thesis. The catalyst must
    # be a DISTINCT news item from the structural anchor; without a second, distinct
    # headline there is no separation to state and the detector stays silent.
    struct = structural[0] if structural else None
    catalyst_pool = list(event) + [s for s in sentiment if s not in event]
    cat = next(
        (
            c
            for c in catalyst_pool
            if struct is None or (c.title, c.url) != (struct.title, struct.url)
        ),
        None,
    )

    if not (struct is not None and cat is not None and short_model):
        return None

    rvol20 = fs.rvol_20d.value
    rvol63 = fs.rvol_63d.value
    # ⚠ REST-DEFEKT #3: rvol63 > rvol20 means the recent 20d window is CALMER than
    # the 63d window — SHORT-term vol is LOWER, not "elevated". The old label read
    # the comparison backwards. Label from the short vs the long window directly.
    short_vol_elevated = rvol20 is not None and rvol63 is not None and rvol20 > rvol63
    vol_note = (
        "(Kurzfrist-Vol erhoeht)" if short_vol_elevated else "(Kurzfrist-Vol niedriger)"
    )

    # ⚠ THE REGRESSION THIS FIX EXISTS FOR. Both news groundings were canned NVDA
    # prose — "Cerebras/Rivalen (Custom-Silicon, ...)" and "Ergebnis-Termin kurz nach
    # Stichtag (binaer) + Stimmungs-Selloff (OpenAI, Burry-Short)" — emitted for EVERY
    # symbol whose news merely matched a keyword, each with fs.news_items.src pinned
    # beside it. A grocer's report told Nvidia's story and audited at 0 flags: the
    # auditor's entity pass only inspects ALL-CAPS 2-5 char tokens (auditor.py:349),
    # so "Cerebras", "Burry" and "OpenAI" are invisible to it by construction.
    # The detector matched real headlines all along — it just never showed them.
    grounding: Dict[str, Fact] = {
        "structural_long": Fact(_quote_titles([struct]), fs.news_items.src),
        "short_catalyst": Fact(_quote_titles([cat]), fs.news_items.src),
        "model_horizon": Fact(
            f"Modell-Horizont ~{horizon} Handelstage (Querschnitts-Rang)",
            fs.model_card.src,
        ),
        "volatility": Fact(
            f"realisierte Vol 63d {_pct(rvol63)}% vs 20d {_pct(rvol20)}% {vol_note}",
            fs.rvol_63d.src,
        ),
    }
    # ⚠ No date, no "in Tagen", no "kurz nach Stichtag": the FactSet has no earnings
    # field, so the TIMING of any event is unknowable here for every symbol. What is
    # knowable is that a headline in the PIT window discusses one — so the headline
    # is quoted and nothing is asserted about when the event lands. The closing vol
    # sentence ("preist eher das Event als das Struktur-Risiko") is likewise gone:
    # rvol63 > rvol20 is a fact, but what the market is PRICING is not in the FactSet.
    implication = (
        f"Weil der Nachrichtenfluss im Fenster ein strukturelles Langfrist-Thema "
        f"('{struct.title}') mit einem kurzfristigen Katalysator "
        f"('{cat.title}') mischt, und weil der Modell-Horizont nur "
        f"~{horizon} Handelstage betraegt, liegt das strukturelle Thema ausserhalb "
        f"dieses Horizonts: fuer diesen Rang ist es Rauschen, waehrend der kurzfristige "
        f"Katalysator das massgebliche Signal ist."
    )
    caveat = (
        "Ueber laengeren Horizont kann das strukturelle Thema sehr wohl zaehlen — die "
        "Trennung ist horizont-spezifisch und kein Urteil ueber die Sache selbst. "
        "Welche Meldung kurzfristig wirkt, ist eine Einordnung der Schlagzeilen; ein "
        "Termin ist damit nicht belegt."
    )
    return Insight(
        pattern_name="horizon_separation_pattern",
        condition_met=True,
        grounding=grounding,
        implication=implication,
        caveat=caveat,
        narration_slot="insight_horizon_separation_pattern",
    )


# ---------------------------------------------------------------------------
# DETECTOR 4 — Trend regime: the close against the full MA stack (20/50/100/200).
# ---------------------------------------------------------------------------
def trend_regime_pattern(fs: FactSet) -> Optional[Insight]:
    """The close vs all four moving averages -> one clean trend regime, or None.

    Synthesises the four MA relationships (already shown as RAW numbers in the
    price/trend section) into a single regime read: price above ALL four = an
    intact uptrend across every window; below ALL four = an intact downtrend; a
    clean short->long ladder split = a transition, and the code names which end
    is broken. A choppy, non-monotonic split (e.g. above the short and the long
    but below the middle) is no clean regime and yields NO insight. Fires only
    when the close and all four moving averages exist.
    """
    close = fs.close.value
    ordered = [fs.ma20.value, fs.ma50.value, fs.ma100.value, fs.ma200.value]
    if close is None or any(m is None for m in ordered):
        return None

    # +1 above / -1 below / 0 exactly-on, SHORT window first, long window last.
    signs = [1 if close > m else (-1 if close < m else 0) for m in ordered]
    if 0 in signs:  # the close sits exactly on an average -> not a clean regime
        return None

    all_above = all(s == 1 for s in signs)
    all_below = all(s == -1 for s in signs)
    # A clean ladder is monotone from the short to the long window. Recovery =
    # above the short averages, still below the long ones (signs run 1..-1, i.e.
    # sorted high->low); rollover is the mirror (below the short, above the long).
    recovery = (
        signs == sorted(signs, reverse=True) and signs[0] == 1 and signs[-1] == -1
    )
    rollover = signs == sorted(signs) and signs[0] == -1 and signs[-1] == 1

    if all_above:
        implication = (
            "Der Kurs liegt ueber allen vier gleitenden Durchschnitten (kurz-, "
            "mittel- und langfristig) — der Aufwaertstrend ist ueber alle "
            "Zeitfenster intakt und stuetzt den Rang technisch."
        )
    elif all_below:
        implication = (
            "Der Kurs liegt unter allen vier gleitenden Durchschnitten (kurz-, "
            "mittel- und langfristig) — der Abwaertstrend ist ueber alle "
            "Zeitfenster intakt und widerspricht dem Rang technisch."
        )
    elif recovery:
        implication = (
            "Der Kurs hat den kurzfristigen Schnitt bereits zurueckerobert, "
            "liegt aber noch unter dem langfristigen — ein Uebergang von Abwaerts- "
            "zu Aufwaertstrend, technisch noch nicht bestaetigt."
        )
    elif rollover:
        implication = (
            "Der Kurs ist unter den kurzfristigen Schnitt gefallen, haelt sich "
            "aber noch ueber dem langfristigen — ein Uebergang, der nach unten "
            "dreht, technisch noch nicht bestaetigt."
        )
    else:
        return None  # non-monotonic split -> no clean regime, stay silent

    grounding: Dict[str, Fact] = {
        "close_vs_ma": Fact(
            f"Kurs {_num(close, 2)}; Schnitte kurz {_num(fs.ma20.value, 2)}, "
            f"mittel {_num(fs.ma50.value, 2)}/{_num(fs.ma100.value, 2)}, "
            f"lang {_num(fs.ma200.value, 2)}",
            fs.ma20.src,
        ),
    }
    caveat = (
        "Der Trend ist Kontext, kein Kaufgrund — er bestaetigt oder widerspricht "
        "dem Rang, ist aber kein eigenstaendiges Signal."
    )
    return Insight(
        pattern_name="trend_regime_pattern",
        condition_met=True,
        grounding=grounding,
        implication=implication,
        caveat=caveat,
        narration_slot="insight_trend_regime_pattern",
    )


# ---------------------------------------------------------------------------
# DETECTOR 5 — Vol regime: short (20d) vs long (63d) realized vol.
# ---------------------------------------------------------------------------
def vol_regime_pattern(fs: FactSet) -> Optional[Insight]:
    """Short-window vs long-window realized vol -> expanding / calming, or None.

    The price/trend section prints both realized-vol numbers; this synthesises
    them into a regime. When the 20d window runs clearly hotter than the 63d one
    the near-term move is EXPANDING (the market is pricing more uncertainty into
    the immediate window); clearly cooler = CALMING. A ratio inside the neutral
    band around 1.0 is no clear change and yields NO insight. Fires only when
    both vols exist and the long window is non-zero.
    """
    rvol20 = fs.rvol_20d.value
    rvol63 = fs.rvol_63d.value
    if rvol20 is None or rvol63 is None or rvol63 == 0:
        return None

    ratio = rvol20 / rvol63
    if ratio >= _VOL_REGIME_RATIO:
        implication = (
            "Die kurzfristige Schwankungsbreite laeuft deutlich ueber der "
            "mittelfristigen — die Bewegung nimmt gerade zu, der Markt preist "
            "kurzfristig mehr Unsicherheit ein."
        )
    elif ratio <= 1.0 / _VOL_REGIME_RATIO:
        implication = (
            "Die kurzfristige Schwankungsbreite liegt deutlich unter der "
            "mittelfristigen — die Bewegung beruhigt sich gegenueber den "
            "Vorwochen."
        )
    else:
        return None  # inside the neutral band -> no clear regime, stay silent

    grounding: Dict[str, Fact] = {
        "realized_vol": Fact(
            f"realisierte Vol 20d {_pct(rvol20)}% vs 63d {_pct(rvol63)}%",
            fs.rvol_20d.src,
        ),
    }
    caveat = (
        "Volatilitaet misst die Groesse der Bewegung, nicht ihre Richtung — "
        "hoehere Vol ist kein Kauf- oder Verkaufsgrund."
    )
    return Insight(
        pattern_name="vol_regime_pattern",
        condition_met=True,
        grounding=grounding,
        implication=implication,
        caveat=caveat,
        narration_slot="insight_vol_regime_pattern",
    )


# ---------------------------------------------------------------------------
# DETECTOR 6 — Rank momentum: the cross-sectional score trajectory's direction.
# ---------------------------------------------------------------------------
def rank_momentum_pattern(fs: FactSet) -> Optional[Insight]:
    """The score-trend's DIRECTION over the window -> rising / falling, or None.

    The quant section prints the score trend and how the LAST score sits vs its
    mean (a level); this reads the trajectory's DIRECTION across the window and
    pairs it with the current cross-sectional standing. Fires only when the trend
    has at least a few points AND moves strictly one way — a flat or wiggly trend
    has no clear momentum and yields NO insight.
    """
    trend = fs.lstm_score_trend.value or []
    if len(trend) < _RANK_TREND_MIN_POINTS:
        return None

    diffs = [trend[i + 1] - trend[i] for i in range(len(trend) - 1)]
    rising = all(d > 0 for d in diffs)
    falling = all(d < 0 for d in diffs)
    if not (rising or falling):
        return None  # flat / non-monotonic -> no clear momentum, stay silent

    pct = fs.lstm_xsec_percentile.value
    standing = (
        f" und steht im Universums-Vergleich aktuell im {_num(pct, 1)}. Perzentil"
        if pct is not None
        else ""
    )
    direction = "steigt" if rising else "faellt"
    implication = (
        f"Der Modell-Rang {direction} ueber die letzten Bewertungen durchgehend "
        f"({_num(trend[0], 2)} -> {_num(trend[-1], 2)}){standing} — die "
        f"Platzierung im Universum bewegt sich klar in eine Richtung."
    )

    grounding: Dict[str, Fact] = {
        "score_trend": Fact(_trend(trend), fs.lstm_score_trend.src),
    }
    if pct is not None:
        grounding["cross_section"] = Fact(
            f"{_num(pct, 1)}. Querschnitts-Perzentil", fs.lstm_xsec_percentile.src
        )
    caveat = (
        "Der Rang ist relativ zum Universum, kein Kursziel — eine steigende oder "
        "fallende Platzierung sagt nichts ueber die absolute Rendite."
    )
    return Insight(
        pattern_name="rank_momentum_pattern",
        condition_met=True,
        grounding=grounding,
        implication=implication,
        caveat=caveat,
        narration_slot="insight_rank_momentum_pattern",
    )


# ---------------------------------------------------------------------------
# DETECTOR 7 — Own-history extreme: the score at the edge of its own range.
# ---------------------------------------------------------------------------
def own_history_extreme_pattern(fs: FactSet) -> Optional[Insight]:
    """The score's percentile against its OWN past, only at the tails, or None.

    The quant section shows the own-history percentile as a side number; this
    fires only when that percentile sits in a tail (top or bottom decile) AND the
    history actually has dispersion, then reads it as an extreme relative to the
    title's own range. A flat history (no dispersion) collapses the percentile to
    a meaningless 0 and must NOT be called an extreme; the mid-range is
    unremarkable. Fires only when the percentile and a non-zero history std exist.
    """
    own = fs.lstm_own_percentile.value
    std = fs.lstm_hist_std.value
    # ⚠ A flat score history yields own_percentile == 0.0 by construction (no past
    # score is strictly below the last) — an ARTEFACT, not a low extreme. Requiring
    # real dispersion (std present and > 0) is what keeps this off a flat symbol.
    if own is None or not std:
        return None
    if own >= _OWN_EXTREME_HIGH:
        edge, rare = "oberen", "selten hoeher"
    elif own <= _OWN_EXTREME_LOW:
        edge, rare = "unteren", "selten niedriger"
    else:
        return None  # mid-range -> not an extreme, stay silent

    implication = (
        f"Der aktuelle Modell-Score steht am {edge} Rand seiner eigenen Historie "
        f"({_num(own, 1)}. Perzentil) — er war {rare} als heute. Das ist eine "
        f"Positionsangabe zur eigenen Vergangenheit, kein Signal zum Universum."
    )

    grounding: Dict[str, Fact] = {
        "own_percentile": Fact(
            f"{_num(own, 1)}. Perzentil der eigenen Score-Historie",
            fs.lstm_own_percentile.src,
        ),
    }
    lo = fs.lstm_hist_min.value
    hi = fs.lstm_hist_max.value
    if lo is not None and hi is not None:
        grounding["hist_range"] = Fact(
            f"historische Spanne {_num(lo, 2)} bis {_num(hi, 2)}",
            fs.lstm_hist_min.src,
        )
    caveat = (
        "Das Eigen-Perzentil vergleicht den Titel nur mit sich selbst, nicht mit "
        "dem Universum — ein Extrem ist eine Positionsangabe, kein Richtungssignal."
    )
    return Insight(
        pattern_name="own_history_extreme_pattern",
        condition_met=True,
        grounding=grounding,
        implication=implication,
        caveat=caveat,
        narration_slot="insight_own_history_extreme_pattern",
    )


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------
# ⚠ The three per-symbol news/fundamentals detectors run FIRST (they carry the
# substantive, symbol-specific findings); the four deterministic technical /
# rank COVERAGE detectors run last so almost every symbol has >= 1 grounded,
# honest insight even when it has no news, no fundamentals and no divergence.
_DETECTORS: tuple = (
    divergence_pattern,
    valuation_vs_growth_pattern,
    horizon_separation_pattern,
    trend_regime_pattern,
    vol_regime_pattern,
    rank_momentum_pattern,
    own_history_extreme_pattern,
)


def detect_insights(fact_set: FactSet) -> List[Insight]:
    """Return the grounded insights whose patterns the code proves in ``fact_set``.

    Runs every detector in a fixed, deterministic order and keeps only those that
    fired (``condition_met``). A FactSet with no matching pattern yields an empty
    list — the honest zero-insight case, never a manufactured insight.

    Parameters
    ----------
    fact_set:
        The frozen, PIT-filtered fact foundation from RPT-1
        (:func:`core.report.fact_set.build_fact_set`).

    Returns
    -------
    list[Insight]
        Fired insights, each carrying provenanced ``grounding``, a CODE-derived
        ``implication`` and an honest ``caveat``. Feed this to
        :func:`core.report.render.render`, which shows it as the ``## Insights``
        section (or omits the section when the list is empty).
    """
    out: List[Insight] = []
    for detector in _DETECTORS:  # type: Callable[[FactSet], Optional[Insight]]
        insight = detector(fact_set)
        if insight is not None and insight.condition_met:
            out.append(insight)
    return out


__all__ = [
    "Insight",
    "detect_insights",
    "divergence_pattern",
    "valuation_vs_growth_pattern",
    "horizon_separation_pattern",
    "trend_regime_pattern",
    "vol_regime_pattern",
    "rank_momentum_pattern",
    "own_history_extreme_pattern",
]
