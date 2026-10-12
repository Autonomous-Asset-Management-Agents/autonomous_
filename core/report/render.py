# core/report/render.py
# RPT-3 (#2002, Epic #1998) — the deterministic FactSet -> report-section renderer.
"""Deterministic, LLM-free renderer: :class:`FactSet` -> Markdown report sections.

This is the *rendering* layer of the auditable report pipeline (Epic #1998). It
turns the frozen, fully-provenanced :class:`~core.report.fact_set.FactSet` built
by RPT-1 into a professional, plain-language equity research note.

Design contract (BORA / pure core):

* **Pure & deterministic.** No I/O, no persistence backend, no network, no LLM,
  no GPU, no ``pandas``, no clock (``datetime.now``), no randomness. Given the
  same FactSet it returns a byte-identical string. Edition-agnostic.
* **Renders facts, invents nothing.** Every number is read from a FactSet
  ``Fact`` (already provenanced) and carries its ``[src: ...]`` tag so the
  auditor can trace it. Missing feeds render as an honest gap, never a fabricated
  value.

FOCUS (RPT-3c, Georg 2026-07-16): the note is about the STOCK, not the bot. The
model's aggregate track record (Sharpe/IC/t-stat/EqualWeight/turnover/leakage
split) is identical for every stock, says nothing about THIS name or the
buy/sell/hold call, and is therefore NOT printed in the per-stock body — at most
a numberless methodology pointer in the footer. The one validated edge is the
cross-sectional RANK (weak-but-real in our own walk-forward); fundamentals, news,
pros/cons and technicals are labelled CONTEXT, never independent alpha, and never
move the call. Conviction is never labelled above "mittel". Plain German, no
oversell, no spin.
"""

from __future__ import annotations

from datetime import date
from typing import Any, List, Mapping, Optional

from core.report.fact_set import FactSet
from core.report.insight_patterns import Insight, detect_insights

# ---------------------------------------------------------------------------
# Editorial constants (display-only; no order or capital path reads them).
# ---------------------------------------------------------------------------
# ADR-RPT3-01: cross-sectional recommendation rule (quintile thresholds).
_TOP_QUINTILE_PCT = 80.0
_BOTTOM_QUINTILE_PCT = 20.0
_CONVICTION = "mittel"

# ADR-RPT4-09: the panel breadth below which the model card's validation does NOT
# transfer, and the report must stop calling its rank "systematisch getestet".
#
# The card measures cs_ic_mean 0.067 / cs_ic_tstat 20.4 across universe_symbols = 486.
# The shipped desktop default is ENVIRONMENT=development -> DEFAULT_SYMBOLS: ten symbols,
# four of them ETFs, and market_scanner truncates to [:10] sorted by VOLATILITY. Rank
# 1-of-10-most-volatile is a DIFFERENT ESTIMATOR ON A BIASED SAMPLE, not a small version
# of rank 3-of-486. Every number stays real — the validation claim about them does not
# transfer, and the mechanical auditor cannot see that: it checks provenance of digits,
# not provenance of meaning.
#
# ⚠ DUPLICATED ON PURPOSE, AND IT MUST NOT STAY THAT WAY: PR #2150 introduces the same
# floor as core.round_table.agents._MIN_PANEL_SYMBOLS — the threshold at which
# LSTMSignalAgent._vote_on_rank already refuses to vote. Importing it here would gate an
# honesty fix behind a large dormant feature PR, which is the wrong trade. When #2150
# lands, unify these two into ONE definition; duplicated thresholds drift, and a drifted
# one would let the report call "tested" what the board refuses to vote on.
_MIN_PANEL_FOR_VALIDATION = 30

# ADR-RPT3-02: Abschnitt 7 scenario weights are fixed EDITORIAL CALIBRATION,
#   explicitly labelled NOT model-derived, and now TILT with the call (bull-tilt
#   in the top quintile, bear-tilt in the bottom) so a sell note is never
#   bull-weighted. See :func:`_section_scenarios`.


# ---------------------------------------------------------------------------
# Small deterministic formatting helpers (rounding facts already in the FactSet).
# ---------------------------------------------------------------------------
def _num(value: Any, nd: int = 2) -> str:
    """Format a numeric fact to ``nd`` decimals, or an honest gap marker."""
    if value is None:
        return "n/a"
    return f"{float(value):.{nd}f}"


def _pct(value: Any, nd: int = 1) -> str:
    """Format a vol/ratio fraction (0.349) as a percent string (34.9%)."""
    if value is None:
        return "n/a"
    return f"{float(value) * 100:.{nd}f}%"


def _iso(value: Any) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _trend(values: Optional[List[float]]) -> str:
    if not values:
        return "n/a"
    return " → ".join(f"{float(v):.2f}" for v in values)


def _rel(close: Any, level: Any) -> str:
    """'über' / 'unter' / 'auf' for the close vs a moving-average level."""
    if close is None or level is None:
        return "n/a"
    c, m = float(close), float(level)
    if c > m:
        return "über"
    if c < m:
        return "unter"
    return "auf"


def _extreme_txt(fact: Any) -> str:
    """Render an :class:`Extreme` (price + date) or an honest gap."""
    ex = getattr(fact, "value", None)
    if ex is None:
        return "n/a"
    return f"{_num(ex.price, 2)} (am {_iso(ex.on)})"


# ADR-RPT-COMPANY-01: the scale boundary for a money magnitude. Above it the figure
# reads in Bio (trillions), below in Mrd (billions).
#
# The old §6 line printed EVERY market cap in "Bio USD" — right for NVDA (4.87) and
# useless for the rest of the universe: LYB's real 18.7 Mrd renders "~0.02 Bio USD",
# and most of the S&P 500 sits between 0.01 and 0.5 Bio, i.e. rounds to two or three
# indistinguishable decimals. Verified against live payloads (2026-07-17): LYB 18.72
# Mrd, MMM 83.73 Mrd, JPM 922.16 Mrd, XOM 598.99 Mrd, NVDA 5.15 Bio — one boundary
# separates them all correctly.
_BIO_THRESHOLD_USD = 1e12


def _usd_scaled(company: Mapping[str, Any]) -> Optional[str]:
    """Render a company's market cap at the scale the PRODUCER chose, or ``None``.

    ⚠ Reads the pre-scaled display fields, never ``market_cap`` itself. The auditor pools
    Fact values verbatim, so a rendered "18.72 Mrd" must exist in the FactSet AS 18.72 —
    the raw 18721516834.0 could never source it (company_feed pre-scales for exactly
    this, as to_report_fundamentals already does for ``revenue_bn``).

    The producer emits ONE scale — the one printed here — so the pool never learns a
    number the report cannot show (see company_feed.map_polygon_company).
    """
    value = company.get("market_cap_display")
    unit = company.get("market_cap_display_unit")
    if value is None or not unit:
        return None
    return f"{_num(value, 2)} {unit} USD"


def _company_stamp(company: Mapping[str, Any]) -> str:
    """' (Stand YYYY-MM-DD)' — or an explicit ' (Stand unbekannt)'.

    The figures are current-state, NOT as-of (see company_feed), so an UNDATED one in an
    as-of-dated report is precisely the lookahead this module argues against. Dating them
    is what keeps that honest — and when the source carried no fetch date, saying
    "unbekannt" is the honest form. Silence would let the figure read as as-of.
    """
    fetched = company.get("fetched_at")
    return (
        f" (Stand {_iso(fetched)})"
        if isinstance(fetched, date)
        else " (Stand unbekannt)"
    )


# ---------------------------------------------------------------------------
# Recommendation rule (head) — verb + plain retail fragments from the ONE
# validated field (cross-sectional percentile). Nothing else moves the call.
# ---------------------------------------------------------------------------
def _rec_parts(
    xsec_pct: Optional[float],
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Return (verb, handlungssatz, bucket) — ALL None when there is no rank.

    ⚠ No rank means no recommendation. Not a neutral one. None.

    This used to return ``("HALTEN", "neutral / abwarten", None)``: it suppressed the
    bucket — the docstring was even proud of "no bucket is fabricated" — and then handed
    back a verb anyway. The head printed **HALTEN — neutral / abwarten**, byte-identical
    to a genuinely computed mid-field HALTEN, admitted two lines later that no ranking
    existed, and then claimed "Wie sicher: Mittel. Diese Rangfolge ist der einzige
    systematisch getestete Baustein" — confidence in a ranking it had just said was
    absent. The mechanical audit passed it with ZERO flags, because every field was
    technically sourced and "HALTEN" is not a number.

    That mattered far beyond an edge case: the panel's only writer is LSTMDynamic and
    ACTIVE_STRATEGY defaults to RLAgent, so rank-missing is the DEFAULT state of the
    shipped build. A full-universe run would have produced ~500 confident-looking HOLD
    notes built on nothing, all green.

    The precedent is our own: LSTMSignalAgent._vote_on_rank abstains at weight 0 when the
    standing is None rather than voting a plausible 0.5. The report holds the same line.
    """
    if xsec_pct is None:
        return (None, None, None)
    if xsec_pct >= _TOP_QUINTILE_PCT:
        return (
            "KAUFEN",
            "im Depot übergewichten (stärker gewichten als der Marktdurchschnitt)",
            "im **oberen Fünftel** (den besten 20 %)",
        )
    if xsec_pct <= _BOTTOM_QUINTILE_PCT:
        return (
            "VERKAUFEN",
            "im Depot untergewichten (geringer gewichten als der Marktdurchschnitt)",
            "im **unteren Fünftel** (den schwächsten 20 %)",
        )
    return (
        "HALTEN",
        "neutral / abwarten",
        "im **Mittelfeld** — kein klares Signal in die eine oder andere Richtung",
    )


# ADR-RPT-BADGE-01: the note's verdict as a stable buy/hold/sell code for the UI
# badge — or None when the note ABSTAINS (no rank). Derived from the SAME
# ``_rec_parts`` call on the SAME single validated field, guarded by the SAME
# rank/n condition as :func:`_verdict_line` / :func:`_section_recommendation`, so a
# badge fed by this can never disagree with the note's printed Empfehlung. The
# German verb is the report's voice; this code is the machine-readable projection
# of it. ``None`` here is the whole point: a report-only path that never computed a
# recommendation must surface no recommendation, not the dataclass default "hold".
def rec_code(fs: FactSet) -> Optional[str]:
    verb, _handlung, bucket = _rec_parts(fs.lstm_xsec_percentile.value)
    rank = fs.lstm_xsec_rank.value
    n = fs.lstm_xsec_n.value
    if verb is None or bucket is None or rank is None or n is None:
        return None
    return {"KAUFEN": "buy", "HALTEN": "hold", "VERKAUFEN": "sell"}[verb]


# ---------------------------------------------------------------------------
# Section renderers.
# ---------------------------------------------------------------------------
def _title(fs: FactSet) -> str:
    """The masthead: who this note is about, as of when, at what price.

    Split out of ``_head`` so §0 "Das Unternehmen" can sit between the masthead and the
    recommendation — the reader must learn WHAT THE COMPANY DOES before being told how
    it ranks. The recommendation block below is unchanged.
    """
    return (
        f"# {fs.symbol} — Auditierte Research Note\n\n"
        f"**Ticker:** {fs.symbol} · **Stichtag:** {_iso(fs.as_of)}  \n"
        f"**Kurs:** {_num(fs.close.value, 2)} USD [src: {fs.close.src}]"
    )


def _section_company(fs: FactSet) -> str:
    """§0 — what this company actually does, in the source's own words.

    ADR-RPT-COMPANY-01. The note used to print "LYB" and jump to a rank and a table of
    numbers; a reader never learned that LyondellBasell produces petrochemicals.

    THREE STATES, THREE DIFFERENT SENTENCES — never a falsy check:
      None -> no source attached. Nobody looked. The absence of a description is NOT a
              finding, and must not read as one.
      {}   -> the source was asked and carries nothing for this ticker. That IS a
              finding, and a different one.
      dict -> facts. Each field may still be absent on its own (XOM's live payload
              really does carry sic_description: None), so each is gapped separately.

    The description is POLYGON'S PROSE. It is quoted verbatim and attributed — never
    summarised, never translated (a translation would be our voice, or an LLM's, wearing
    the source's authority), never laundered into the report's own voice.
    """
    c = fs.company.value
    if c is None:
        return (
            "## 0. Das Unternehmen\n\n"
            "*Für diesen Titel ist **keine Unternehmensquelle angebunden** — es wurde "
            "also nicht nachgeschlagen, was das Unternehmen tut. Das Fehlen einer "
            "Beschreibung ist **kein Befund**. "
            f"[src: {fs.company.src}]*"
        )
    if len(c) == 0:
        return (
            "## 0. Das Unternehmen\n\n"
            "*Die Unternehmensquelle wurde abgefragt, liefert zu diesem Titel aber "
            "**keine Angaben**. Es wird bewusst nichts ergänzt. "
            f"[src: {fs.company.src}]*"
        )

    name = c.get("name") or fs.symbol
    branche = c.get("sic_description") or "keine Branchenangabe in der Quelle"
    lines = [
        "## 0. Das Unternehmen\n",
        f"**{name}** ({fs.symbol}) — Branche: {branche}.\n",
    ]

    description = c.get("description")
    if description:
        lines.append(
            "**Was das Unternehmen macht** — Originaltext der Quelle, unübersetzt und "
            "unverändert zitiert:\n"
        )
        lines.append(f"> {description}\n")
    elif c.get("description_withheld"):
        # ⚠ NOT "the source has no description" — it has one. Saying so would be a false
        # statement about the source. Say what is actually true: we chose not to quote it.
        lines.append(
            "*Die Quelle enthält eine Beschreibung, sie wird hier aber **nicht "
            "zitiert**: sie nennt eine Geldangabe, die diese Note nicht belegbar "
            "wiedergeben kann. Lieber kein Zitat als ein unbelegtes.*\n"
        )
    else:
        lines.append(
            "*Die Quelle enthält zu diesem Titel **keine Beschreibung** des "
            "Geschäfts — es wird bewusst keine formuliert.*\n"
        )

    # Size: what the reader needs to picture the company. Each part is dropped rather
    # than guessed when the source omits it, and the figures carry their fetch date —
    # they are current-state, not as-of (see company_feed).
    stamp = _company_stamp(c)
    size: List[str] = []
    employees = c.get("employees")
    if employees is not None:
        size.append(f"{employees} Beschäftigte")
    mcap = _usd_scaled(c)
    if mcap is not None:
        size.append(f"Marktkapitalisierung {mcap}")
    if size:
        lines.append(f"**Größe:** {' · '.join(size)}{stamp}.\n")
    else:
        lines.append("*Zur Größe des Unternehmens liegen keine Angaben vor.*\n")

    footer: List[str] = []
    if c.get("list_date") is not None:
        footer.append(f"börsennotiert seit {_iso(c['list_date'])}")
    if c.get("homepage_url"):
        footer.append(str(c["homepage_url"]))
    if footer:
        lines.append(f"{' · '.join(footer)}\n")

    lines.append(f"[src: {fs.company.src}]")
    return "\n".join(lines)


def _verdict_line(fs: FactSet) -> str:
    """ADR-RPT-B5-01: the CALL in ONE line, high up — the reasoning lives at the end.

    Georg dictated the order "Firma → Modell → News → Deutung → Theorien → kaufen
    oder nicht", so the full recommendation moved to the back. A reader must still
    not have to HUNT for the verdict, so the call itself stays here — one line, no
    reasoning. Both blocks derive from the SAME ``_rec_parts`` call on the SAME
    single validated field, so the two can never disagree.

    ⚠ Fails CLOSED exactly like ``_section_recommendation``: no rank -> no verb.
    """
    verb, handlung, bucket = _rec_parts(fs.lstm_xsec_percentile.value)
    rank = fs.lstm_xsec_rank.value
    n = fs.lstm_xsec_n.value

    header = "## Einschätzung in einem Satz\n\n"
    if verb is None or bucket is None or rank is None or n is None:
        return header + (
            "**Für diesen Stichtag liegt keine Einschätzung vor.** "
            "Warum, steht am Ende unter **Empfehlung**."
        )
    return header + (
        f"**{verb} — {handlung}.** Begründung, Sicherheit und Zeithorizont stehen "
        "am Ende unter **Empfehlung**."
    )


def _section_recommendation(fs: FactSet) -> str:
    card = fs.model_card.value or {}
    horizon = card.get("horizon_days", 5)
    verb, handlung, bucket = _rec_parts(fs.lstm_xsec_percentile.value)
    rank = fs.lstm_xsec_rank.value
    n = fs.lstm_xsec_n.value

    # ⚠ UNNUMBERED on purpose: die nummerierte Folge 0..10 beschreibt die AKTIE.
    # Der Call ist der Rahmen darum, nicht ihr elfter Baustein — und eine Nummer
    # hier würde die durchlaufende Nummerierung des Inhalts hinten anhängen.
    # Gleiches gilt für _verdict_line oben.
    header = "## Empfehlung\n\n"

    # ⚠ NO RANK -> NO CALL. The whole head is suppressed: no verb, no confidence, no
    # horizon. Everything below this point would otherwise describe an assessment that
    # was never made. An abstention has to be SAID, not merely implied by silence —
    # a reader who finds no recommendation assumes the page is broken, not that we
    # have nothing to say.
    if verb is None or bucket is None or rank is None or n is None:
        return header + (
            "**Für diesen Stichtag liegt keine Einschätzung vor.**\n\n"
            f"**Warum:** Die Rangfolge im Aktien-Vergleich — die einzige systematisch "
            f"getestete Grundlage unserer Einschätzung — fehlt für {fs.symbol} an "
            f"diesem Stichtag. Ohne sie wird **bewusst keine Empfehlung ausgesprochen** "
            f"und keine Sicherheit angegeben: eine neutrale Empfehlung wäre hier keine "
            f"Zurückhaltung, sondern eine erfundene."
        )

    warum = (
        f"Unser Modell vergleicht an jedem Stichtag rund {n} Aktien miteinander "
        f"und bringt sie in eine Rangfolge. {fs.symbol} steht heute auf **Platz "
        f"{rank} von {n}** — {bucket}. [src: {fs.lstm_xsec_rank.src}]"
    )
    # ADR-RPT4-09: the validation claim only holds on a panel the test covered.
    if n < _MIN_PANEL_FOR_VALIDATION:
        sicher = (
            f"**Wie sicher:** Gering. Diese Rangfolge vergleicht {fs.symbol} mit nur "
            f"{n} Werten. Unsere Prüfung des Verfahrens lief über ein breites "
            f"Aktien-Universum; auf einem so schmalen Vergleich ist sie **nicht "
            f"belastbar** — der Rang unten ist eine Beobachtung, kein geprüftes Signal. "
            f"Er sagt, wie {fs.symbol} zu diesen {n} Werten steht, nicht welchen Kurs "
            f"die Aktie erreichen wird."
        )
    else:
        sicher = (
            f"**Wie sicher:** {_CONVICTION.capitalize()}. Diese Rangfolge ist der "
            f"einzige systematisch getestete Baustein der Einschätzung — ein Hinweis "
            f"von begrenzter Stärke (siehe Abschnitt 10). Sie sagt, wie {fs.symbol} im "
            f"Vergleich zu anderen Aktien dasteht, nicht welchen Kurs die Aktie "
            f"erreichen wird."
        )
    return header + (
        f"**{verb} — {handlung}.**\n\n"
        f"**Warum:** {warum}\n\n"
        f"{sicher}\n\n"
        f"**Zeithorizont:** rund {horizon} Handelstage.\n\n"
        "**Kursziel:** keines. Das Modell liefert eine Rangfolge, keine Kursprognose "
        "und keine Renditezusage."
    )


def _slot(name: str, narration: Optional[Mapping[str, str]], fallback_note: str) -> str:
    """Render a narration slot: the RPT-5 text if provided, else a short lead-in."""
    if narration and narration.get(name):
        return str(narration[name]).strip()
    return f"<!-- SLOT:{name} -->\n{fallback_note}"


def _section_thesis(narration: Optional[Mapping[str, str]]) -> str:
    return "## 6. Investment-These\n\n" + _slot(
        "thesis",
        narration,
        # ⚠ Nummer UND Name müssen zur Überschrift passen — gepinnt durch
        # test_every_cross_reference_points_at_the_section_it_means.
        "Die belegten Bausteine stehen in Abschnitt 1 (der Rang), Abschnitt 2 "
        "(Kurs- & Trendbild) und Abschnitt 5 (Pro & Contra).",
    )


def _section_quant(fs: FactSet) -> str:
    trend = fs.lstm_score_trend.value
    mean = fs.lstm_hist_mean.value
    if trend and mean is not None:
        direction = "über" if float(trend[-1]) >= float(mean) else "unter"
        trend_read = f" — zuletzt {direction} dem historischen Mittel ({_num(mean, 2)})"
    else:
        trend_read = ""
    # ⚠ The rank and n were interpolated RAW here while the percentile on the same line
    # already went through _num(). With no panel that rendered, verbatim:
    #   "**Platz None von None** im Aktien-Vergleich (n/a. Perzentil). [src: ...]"
    # — raw Python None on a user-facing page, wearing a [src:] tag that made the hole
    # look sourced. The auditor could never catch it: it proves that numbers PRESENT are
    # sourced, and "None" is not a number.
    if fs.lstm_xsec_rank.value is None or fs.lstm_xsec_n.value is None:
        operative = (
            f"**Operative Zahl (trägt die Empfehlung):** liegt für {fs.symbol} an "
            f"diesem Stichtag **nicht vor** — es gibt keinen Aktien-Vergleich, in den "
            f"{fs.symbol} eingeordnet werden könnte. Ohne ihn fehlt der Einschätzung "
            f"ihre Grundlage; deshalb wird oben keine Empfehlung ausgesprochen. "
            f"[src: {fs.lstm_xsec_rank.src}]"
        )
    else:
        operative = (
            f"**Operative Zahl (trägt die Empfehlung):** {fs.symbol} steht am "
            f"{_iso(fs.as_of)} auf **Platz {fs.lstm_xsec_rank.value} von "
            f"{fs.lstm_xsec_n.value}** im Aktien-Vergleich "
            f"({_num(fs.lstm_xsec_percentile.value, 1)}. Perzentil). [src: "
            f"{fs.lstm_xsec_rank.src}]"
        )
    return (
        "## 1. Quant-Signal: der Rang\n\n"
        f"{operative}\n\n"
        f"**Zugrundeliegender Score:** {_num(fs.lstm_score.value, 2)} — gegenüber der "
        f"eigenen Vergangenheit das {_num(fs.lstm_own_percentile.value, 1)}. Perzentil "
        f"(eine Nebenzahl: sie sagt, wie {fs.symbol} zu sich selbst steht, nicht zum "
        f"Universum). Der Score ist ein relativer Rang (Bandbreite historisch "
        f"{_num(fs.lstm_hist_min.value, 2)} bis {_num(fs.lstm_hist_max.value, 2)}, "
        f"Mittel {_num(fs.lstm_hist_mean.value, 2)}, Streuung "
        f"{_num(fs.lstm_hist_std.value, 2)}). [src: {fs.lstm_score.src}]\n\n"
        f"**Score-Verlauf:** {_trend(fs.lstm_score_trend.value)}{trend_read}. "
        f"[src: {fs.lstm_score_trend.src}]"
        # ADR-RPT-CONS2 (item 2): the closing sentence "Das Signal ist der relative
        # Rang im Universum — es sortiert Aktien gegeneinander und prognostiziert
        # keinen Einzelkurs." was struck. It restated, verbatim, the "Der Score ist
        # ein relativer Rang" line three rows above AND the §10-Konfidenz caveat;
        # the "not a single-stock price" idea now lives once, in §Empfehlung/§10.
    )


def _section_price_trend(fs: FactSet) -> str:
    # Descriptive labels (no bare window-integers in the body): a "MA200" token
    # emits a bare 200 that only audits when the price happens to sit within
    # tolerance of 200 — a latent strike for stocks not priced near it. The
    # window sizes live in the Fact.src; the body shows the sourced values.
    ma_line = (
        f"kurzfristiger Schnitt {_num(fs.ma20.value, 2)}, mittelfristig "
        f"{_num(fs.ma50.value, 2)} und {_num(fs.ma100.value, 2)}, langfristiger "
        f"Schnitt {_num(fs.ma200.value, 2)}; der Kurs liegt "
        f"{_rel(fs.close.value, fs.ma20.value)} dem kurzfristigen und "
        f"{_rel(fs.close.value, fs.ma200.value)} dem langfristigen Schnitt"
    )
    return (
        "## 2. Kurs- & Trendbild\n\n"
        # ADR-RPT-CONS2 (item 4): "kein eigenständiger Kaufgrund" was one of ~4
        # copies of the "context, not independent alpha" idea. The single canonical
        # statement now lives in §10 (and names the Kursbild explicitly). The lead-in
        # keeps its section-specific meaning: technicals confirm/contradict the rank.
        "*Technische Einordnung — bestätigt oder widerspricht dem Rang.*\n\n"
        f"**Kursbewegung zuletzt:** {_num(fs.pullback_pct.value, 1)}% über die letzten "
        f"Handelstage. [src: {fs.pullback_pct.src}]\n\n"
        f"**Kursband (1 Jahr):** {_extreme_txt(fs.low_52w)} bis {_extreme_txt(fs.high_52w)}; "
        f"aktueller Kurs {_num(fs.close.value, 2)}. [src: {fs.high_52w.src}]\n\n"
        f"**Kursband (1 Monat):** {_extreme_txt(fs.low_1m)} bis {_extreme_txt(fs.high_1m)}. "
        f"[src: {fs.high_1m.src}]\n\n"
        f"**Trend (gleitende Durchschnitte):** {ma_line}. [src: {fs.ma20.src}]\n\n"
        f"**Schwankungsbreite (auf ein Jahr hochgerechnet):** 20d "
        f"{_pct(fs.rvol_20d.value)}, 63d {_pct(fs.rvol_63d.value)}. "
        f"[src: {fs.rvol_20d.src}]"
    )


def _section_pros_cons(fs: FactSet) -> str:
    pros = fs.pros.value or []
    cons = fs.cons.value or []
    if not pros and not cons:
        return (
            "## 5. Pro & Contra\n\n"
            f"*Keine belegten Alt-Signale zum Stichtag. [src: {fs.pros.src}]*"
        )
    pro_txt = (
        "\n".join(f"- {p}" for p in pros) if pros else "- *Keine belegten Pro-Punkte.*"
    )
    con_txt = (
        "\n".join(f"- {c}" for c in cons)
        if cons
        else "- *Keine belegten Contra-Punkte.*"
    )
    return (
        "## 5. Pro & Contra\n\n"
        "*Aus Alt-Signalen abgeleiteter Kontext — kein eigenständig validiertes "
        "Alpha, bewegt die Empfehlung nicht.*\n\n"
        f"**Dafür:**\n{pro_txt}\n\n"
        f"**Dagegen:**\n{con_txt}\n\n"
        f"[src: {fs.pros.src}]"
    )


def _section_news(fs: FactSet, narration: Optional[Mapping[str, str]]) -> str:
    items = fs.news_items.value or []
    if items:
        bullets = "\n".join(
            f"- **{ni.title}** — {ni.published_utc[:10]} · {ni.publisher} [url: {ni.url}]"
            for ni in items
        )
    elif "not wired" in fs.news_items.src:
        # ⚠ "Keine relevanten Meldungen im Fenster" is a claim about the WORLD. With no
        # feed attached we have no evidence for it — and pairing it with a source badge
        # made it read as a checked finding. Say what is actually true: nobody looked.
        bullets = (
            "- *Für diesen Titel ist derzeit **keine Nachrichtenquelle angebunden** — "
            "es wurde also nicht geprüft, ob Meldungen vorliegen. Das Fehlen von "
            "Meldungen unten ist **kein Befund**. "
            f"[src: {fs.news_items.src}]*"
        )
    else:
        bullets = (
            f"- *Keine relevanten Meldungen im Fenster. [src: {fs.news_items.src}]*"
        )
    prose = _slot(
        "catalysts",
        narration,
        # ADR-RPT-CONS2 (item 4): "dokumentierter Kontext" duplicated the §10
        # "Nachrichten liefern Kontext" statement; struck. The section-specific
        # honesty note that news is not a forecast stays.
        "Oben stehen ausschließlich belegte, zeitlich gefilterte Meldungen mit Quelle "
        "— keine Vorhersage künftiger Ereignisse.",
    )
    # ADR-RPT-B5-02: Georg wants "so könnten die beiden dinge zusammenhängen" — a
    # link between the news and the model's view. That link CANNOT be stated as a
    # finding: the model is a pure cross-sectional PRICE-signal ranker, it never
    # reads a headline. "Rang 256, weil Trump 355 Schiffe will" would be fabricated
    # causality in a note whose entire value is that every claim is traceable.
    # So we do not suppress the reading — we NAME WHOSE VOICE IT IS. Below this
    # line the narrator speculates; above it, nothing is claimed. The label is
    # deterministic renderer text and sits BEFORE the slot, so it is present even
    # when the LLM is unreachable and the fallback lead-in renders instead.
    lesart = (
        "**Wie das mit dem Modell zusammenhängt:** Das Modell kennt diese Meldungen "
        "nicht — es sieht ausschließlich Kursdaten. Was folgt, ist eine Lesart, "
        "keine Modell-Aussage."
    )
    return (
        "## 3. Nachrichtenlage & Marktkontext\n\n"
        f"**Belegte Meldungen (Stand {_iso(fs.as_of)}):**\n"
        f"{bullets}\n\n"
        f"{lesart}\n\n"
        f"{prose}"
    )


def _section_fundamentals(fs: FactSet) -> str:
    fund = fs.fundamentals.value
    if not fund:
        return (
            "## 4. Fundamentaldaten & Bewertung\n\n"
            "*Für diesen Titel liegen zum Stichtag keine geprüften Fundamentaldaten vor "
            f"— es wird bewusst kein Wert geschätzt. [src: {fs.fundamentals.src}]*"
        )

    def g(key: str, nd: int) -> str:
        return _num(fund.get(key), nd)

    # ADR-RPT-COMPANY-01: the valuation line rendered "Marktkapitalisierung ~n/a Bio
    # USD" whenever there was no as-of close to derive it from — which is the DEFAULT
    # state, since the derived value is close x diluted shares and needs both.
    #
    # Order of preference is deliberate and must not be flipped: the DERIVED value wins
    # because it is genuinely point-in-time (close@as_of x shares). The reference feed's
    # figure is current-state — fetched today, possibly months after as_of — so letting
    # it displace a PIT value would quietly turn a valuation into a lookahead. It fills
    # a HOLE, and only ever while saying which day it is from.
    #
    # ⚠ market_cap_bn is a MISNOMER carrying TRILLIONS to match this line's "Bio" label
    # (verified: financials_feed.py:531, pinned by
    # test_fundamentals_report_adapter.py:148). Left exactly as it was — renaming it is
    # a separate, deliberate change.
    # ⚠ `or {}` here would be this PR's own None/{} collapse — the very pattern it argues
    # against. The distinction is immaterial to the fallback below (both yield no figure),
    # but writing the collapse anyway is how it comes back.
    company = fs.company.value if isinstance(fs.company.value, Mapping) else {}
    if fund.get("market_cap_bn") is not None:
        mcap_txt = f"~{g('market_cap_bn', 2)} Bio USD"
    else:
        scaled = _usd_scaled(company)
        # ⚠ The fallback comes from a DIFFERENT source than this section's header
        # ("Quelle: FY2025 10-K ... [src: polygon vX financials]"). Printing it bare would
        # attribute a reference-endpoint figure to the 10-K — a real fact wired to the
        # wrong subject, the semantic class the mechanical auditor cannot see. So it
        # carries its OWN [src:] and its own date.
        mcap_txt = (
            f"{scaled}{_company_stamp(company)} [src: {fs.company.src}]"
            if scaled
            else "~n/a Bio USD"
        )

    return (
        "## 4. Fundamentaldaten & Bewertung\n\n"
        f"Quelle: {fund.get('fiscal_label', 'n/a')} (eingereicht {fund.get('filing_date', 'n/a')}) "
        f"[src: {fs.fundamentals.src}].\n\n"
        "| Kennzahl | Wert | YoY |\n"
        "|---|---|---|\n"
        f"| Umsatz | {g('revenue_bn', 2)} Mrd USD | +{g('revenue_yoy_pct', 1)}% |\n"
        f"| Bruttomarge | {g('gross_margin_pct', 1)}% | — |\n"
        f"| Operative Marge | {g('op_margin_pct', 1)}% | — |\n"
        f"| Nettogewinn | {g('net_income_bn', 2)} Mrd USD | +{g('net_income_yoy_pct', 1)}% |\n"
        f"| Nettomarge | {g('net_margin_pct', 1)}% | — |\n"
        f"| EPS (verwässert) | {g('eps_diluted', 2)} USD | +{g('eps_yoy_pct', 1)}% |\n"
        f"| Operativer Cashflow | {g('op_cash_flow_bn', 2)} Mrd USD | — |\n\n"
        f"**Bilanz:** Eigenkapital {g('equity_bn', 2)} Mrd, Verbindlichkeiten "
        f"{g('liabilities_bn', 2)} Mrd → Verschuldungsgrad ~{g('debt_to_equity', 2)}.\n\n"
        f"**Bewertung:** Marktkapitalisierung {mcap_txt}, "
        f"KGV (Kurs-Gewinn-Verhältnis) {g('pe', 1)}, KUV (Kurs-Umsatz-Verhältnis) "
        f"{g('ps', 1)}.\n\n"
        # ADR-RPT-CONS2 (item 4): "dienen der Einordnung" repeated the §10 "context"
        # idea; struck. The mechanical fact — the fundamentals are NOT wired into the
        # model — is a different, stronger claim and stays (pinned by
        # test_fundamentals_present_renders_full_structure_number_exact).
        "*Hinweis: kein Forward-KGV (keine Analystenschätzungen lokal); die Marktkapitalisierung "
        "ist eine Näherung. Diese Fundamentaldaten sind NICHT ins Modell verdrahtet — der Rang "
        "beruht auf dem cross-sectionalen Kurssignal.*"
    )


def _section_scenarios(fs: FactSet) -> str:
    """Coarse editorial scenarios whose TILT and drivers follow the actual call
    (top-quintile -> bull-tilt, bottom -> bear-tilt, middle/none -> balanced), so
    a VERKAUFEN note never carries a bull-weighted table or calls a bottom-ranked
    stock 'Spitzengruppe'. Weights are labelled calibration, not probabilities."""
    pct_v = fs.lstm_xsec_percentile.value
    pct = _num(pct_v, 1)
    vol63 = _pct(fs.rvol_63d.value)
    tape = (
        f"Kurs {_rel(fs.close.value, fs.ma20.value)} dem kurzfristigen "
        f"({_num(fs.ma20.value, 2)}) und {_rel(fs.close.value, fs.ma200.value)} dem "
        f"langfristigen Schnitt ({_num(fs.ma200.value, 2)})"
    )
    if pct_v is None:
        w = ("moderat (~30%)", "mittel (~40%)", "moderat (~30%)")
        bull = "Aufstieg in der Rangfolge, sobald ein Rang vorliegt"
        base = f"seitwärts; {tape}"
        bear = f"erhöhte Schwankungsbreite ({vol63})"
    elif pct_v >= _TOP_QUINTILE_PCT:
        w = ("hoch (~40%)", "mittel (~35%)", "moderat (~25%)")
        bull = f"Titel in der Spitzengruppe ({pct}. Perzentil); {tape}"
        base = "seitwärts; Rang getragen von der Spitzenposition"
        bear = (
            f"Abrutschen aus dem oberen Fünftel + erhöhte Schwankungsbreite ({vol63})"
        )
    elif pct_v <= _BOTTOM_QUINTILE_PCT:
        w = ("moderat (~25%)", "mittel (~35%)", "hoch (~40%)")
        bull = f"Erholung aus der Schlussgruppe ({pct}. Perzentil) Richtung Mittelfeld"
        base = f"seitwärts in der unteren Rang-Region; {tape}"
        bear = (
            f"Verharren in der Schlussgruppe ({pct}. Perzentil) + erhöhte "
            f"Schwankungsbreite ({vol63})"
        )
    else:
        w = ("moderat (~30%)", "mittel (~40%)", "moderat (~30%)")
        bull = f"Aufstieg ins obere Fünftel aus dem Mittelfeld ({pct}. Perzentil)"
        base = f"seitwärts im Mittelfeld; {tape}"
        bear = f"Abrutschen ins untere Fünftel + erhöhte Schwankungsbreite ({vol63})"
    return (
        "## 7. Szenarien\n\n"
        "| Szenario | Gewichtung | Treiber (belegt aus dem FactSet) |\n"
        "|---|---|---|\n"
        f"| **Bull** | {w[0]} | {bull} |\n"
        f"| **Base** | {w[1]} | {base} |\n"
        f"| **Bear** | {w[2]} | {bear} |\n\n"
        "*Die Gewichtungen sind grobe Kalibrierung zur Größenordnung, keine "
        "modell-abgeleiteten Wahrscheinlichkeiten.*"
    )


def _section_risks(fs: FactSet, narration: Optional[Mapping[str, str]]) -> str:
    return "## 8. Risiken\n\n" + _slot(
        "risks",
        narration,
        "Wesentliche Risiken: erhöhte Schwankungsbreite (Abschnitt 2), die in Abschnitt 5 "
        "belegten Contra-Punkte und die Grenze des Signals selbst — es ist ein relativer "
        "Rang, keine Einzelkurs-Trefferquote.",
    )


def _insight_implication(ins: Insight, narration: Optional[Mapping[str, str]]) -> str:
    """The insight's implication: the RPT-5 prose if provided, else the code text."""
    if narration and narration.get(ins.narration_slot):
        return str(narration[ins.narration_slot]).strip()
    return ins.implication


def _render_insight(ins: Insight, narration: Optional[Mapping[str, str]]) -> str:
    ground = "\n".join(
        f"- **{key}:** {g.value} [src: {g.src}]" for key, g in ins.grounding.items()
    )
    return (
        f"### {ins.pattern_name}\n\n"
        f"{_insight_implication(ins, narration)}\n\n"
        f"*Einordnung: {ins.caveat}*\n\n"
        f"<details><summary>Belege</summary>\n\n{ground}\n\n</details>"
    )


# The insights whose bullish reading is a statement ABOUT THE RANK — and therefore may
# NOT headline a symbol the model does not rank highly (anti-overselling). Today that is
# exactly ``divergence_pattern``: it fires on a RISING cross-sectional model score
# (``lstm_score_trend``) while price falls and argues "the selloff is positioning-driven,
# the model still ranks it up" — bull prose coherent only with a KAUFEN. It stays
# rank-gated (suppressed on HALTEN, VERKAUFEN and rank-missing). The gate is load-bearing,
# NOT redundant: the per-symbol score series is INDEPENDENT of the cross-section panel
# (``cross_section_standing`` -> None on an empty panel, lstm_panel_store.py:192), so
# divergence CAN fire while ``pct`` is None.
#
# ⚠ ``valuation_vs_growth_pattern`` was in this set and that was the bug this fix closes.
# A trailing PEG < 1 on a low-debt balance sheet is a FACT about the company — it reads
# ONLY fundamentals (insight_patterns.py:332), independent of whether or how the model
# ranks the name. In the shipped default the cross-section is empty (``pct`` is None for
# every symbol), so gating PEG made the ENTIRE "## 9. Insights" section disappear for a
# name like NVDA whose only firing insight WAS the PEG fact. Factual insights (valuation,
# trend_regime, vol_regime, own_history_extreme, and rank_momentum's directional read)
# are never rank-gated; only genuinely rank-derived bull framing is. PEG carries its own
# "nicht als 'guenstig' oder 'Kauf' lesen" caveat, so it is honest context, not a call.
_RANK_DERIVED_BULLISH_INSIGHTS = {"divergence_pattern"}


def _section_insights(
    fs: FactSet, narration: Optional[Mapping[str, str]]
) -> Optional[str]:
    """The deterministic ``## Insights`` section, or ``None`` when none fire."""
    insights = detect_insights(fs)
    pct = fs.lstm_xsec_percentile.value
    if pct is None or pct < _TOP_QUINTILE_PCT:
        # Drop ONLY rank-derived bullish framing when the model does not rank this name
        # highly. Factual insights (PEG, trend/vol regime, own-history) pass through.
        insights = [
            i for i in insights if i.pattern_name not in _RANK_DERIVED_BULLISH_INSIGHTS
        ]
    if not insights:
        return None
    body = "\n\n".join(_render_insight(ins, narration) for ins in insights)
    return (
        "## 9. Insights\n\n"
        "*Jede Erkenntnis wird nur ausgewiesen, wenn ihr Muster im FactSet belegt ist; "
        "die Belege stehen aufklappbar darunter.*\n\n"
        f"{body}"
    )


def _section_confidence(fs: FactSet) -> str:
    # ADR-RPT4-09: the same claim, a second time — and it must fall the same way. The
    # card's validation covers ~486 names; on a thin panel it is not this report's
    # number. Withdrawing the claim in the head and repeating it here would be worse
    # than never guarding at all: the reader would find the honest line and the
    # flattering one side by side and believe the flattering one.
    n = fs.lstm_xsec_n.value
    if n is not None and n < _MIN_PANEL_FOR_VALIDATION:
        return (
            "## 10. Konfidenz & Grenzen\n\n"
            f"**Belastbarkeit & Grenzen:** An dieser Note ist derzeit **nichts "
            f"systematisch geprüft**. Der Rang vergleicht {fs.symbol} mit nur {n} "
            f"Werten — unsere Prüfung des Verfahrens lief über ein breites "
            f"Aktien-Universum und ist auf einen so schmalen Vergleich **nicht "
            f"übertragbar**; die geprüften Kennzahlen sind mit diesem Rang **nicht "
            f"vergleichbar**. Alle genannten Zahlen sind echt und belegt, aber sie "
            f"tragen hier keine geprüfte Aussage. Fundamentaldaten und Nachrichten "
            f"liefern Kontext, sind aber kein geprüftes Signal. Diese Note ist keine "
            "Anlageberatung."
        )
    return (
        "## 10. Konfidenz & Grenzen\n\n"
        "**Belastbarkeit & Grenzen:** Systematisch getestet ist an dieser Note nur die "
        "relative Rangfolge im Vergleich aller bewerteten Aktien — und zwar in einem "
        "historischen Rückvergleich mit bekannten methodischen Grenzen (einfacher "
        "Datensplit statt echter Zeitreihen-Trennung, der Rang kann daher leicht zu "
        "optimistisch ausfallen). Der Effekt ist real, aber schwach: nach Handelskosten "
        "hat er einen simplen Gleichgewichts-Ansatz bisher nicht zuverlässig geschlagen. "
        "Behandeln Sie den Rang als einen von mehreren, bewusst konservativ gewichteten "
        # ADR-RPT-CONS2 (item 4): the ONE canonical "context, not independent alpha"
        # statement. It now names the Kursbild too, so the inline copies struck from
        # §2/§3/§4 lose nothing — each idea is said here exactly once.
        "Bausteinen. Kursbild, Fundamentaldaten und Nachrichten liefern Kontext, sind "
        "aber kein eigenständig belegter Vorteil. Kein Kursziel, keine Renditezusage, "
        "keine Anlageberatung."
    )


def _footer(fs: FactSet) -> str:
    # ADR-RPT-CONS-01: der "keine Anlageberatung"-Disclaimer stand hier UND in §10 —
    # Wort für Wort (Georg: "der Disclaimer steht DOPPELT"). Ein doppelter Rechtssatz
    # ist keine doppelte Absicherung, nur Redundanz. Die kanonische Fassung bleibt in
    # §10 (_section_confidence, gepinnt durch test_golden_confidence_section_caveats);
    # rechtlich verlangt LIVE_DEMO_COMPLIANCE_GATE / DISCLAIMER.md ihn EINMAL prominent,
    # nicht zweimal. Die Fußzeile behält ihren EIGENEN Punkt (Audit-Umfang + der Zeiger
    # auf die aggregierte Modell-Güte), trägt den Disclaimer aber nicht mehr.
    return (
        "---\n"
        "*Maschinell geprüft ist die **Herkunft jeder Zahl** (jede Zahl ist auf ihre "
        "Quelle im Datenbestand zurückführbar) — nicht die Kauf-/Verkaufs-Empfehlung "
        "selbst; die ist eine Modell-Einschätzung, kein geprüftes Urteil. Die aggregierte "
        "Modell-Güte über das Gesamtuniversum beschreibt die Methode, nicht diesen Titel, "
        "und steht in der Modell-Dokumentation.*"
    )


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------
def render(fact_set: FactSet, *, narration: Optional[Mapping[str, str]] = None) -> str:
    """Render a :class:`FactSet` into the professional research-note Markdown.

    ``narration`` is the optional RPT-5 seam for the three prose slots
    (``"thesis"`` / ``"catalysts"`` / ``"risks"``) and the insight-narration
    slots; when absent, a short deterministic lead-in / the code-derived insight
    implication renders instead. The renderer never calls a model — it only
    *places* text handed to it, and every number carries its ``[src:]`` tag.
    Same ``fact_set`` (and ``narration``) → byte-identical string.
    """
    # ADR-RPT-B5-01: the ORDER is Georg's, dictated verbatim:
    #   "das ist die firma / das macht die / das sagen unsere ml modelle dazu /
    #    das sind news artiekel von jz grad / so könnten die beiden dinge
    #    zusammenhängen / das hier sind unsere theorien zu was mit der aktie
    #    passieren wird warum sie so steht und ob man sie kaufen sollte oder nicht"
    #
    # Die Nummern laufen in LESEREIHENFOLGE lückenlos durch (0..10). Springende
    # Nummern sind für einen Kunden nicht von einem Bug zu unterscheiden — und
    # Georgs ganzer Punkt war "die reports sind zu kompliziert".
    #
    # ⚠ Wer hier die Reihenfolge ändert, ändert damit die NUMMERN — und der
    # Fließtext VERWEIST auf die Nummern ("Abschnitt 1 (der Rang)" in
    # _section_thesis, "(Abschnitt 2)" in _section_risks, "siehe Abschnitt 10" in
    # _section_recommendation). Ein Verweis, der auf den falschen Abschnitt zeigt,
    # ist ein ERFUNDENER Querverweis in einer Auditierbarkeits-Note — genau die
    # Fehlerklasse, gegen die dieser Report existiert — und er geht STILL durch:
    # der Auditor prüft Zahlen-Herkunft, nicht Verweis-Korrektheit.
    # Nummer UND Name jedes Verweises sind deshalb durch
    # test_every_cross_reference_points_at_the_section_it_means gepinnt.
    #
    # ⚠ auditor._SCENARIOS_BLOCK_RE schneidet "## <n>. Szenarien" aus dem
    # Band-Pass; die Regex nutzt \d+ und überlebt ein Renumber, ihr Pinning-Test
    # (test_report_band_claims) hängt aber am Literal. §7 widerspricht per Design —
    # bricht der Carve-out, schlägt JEDER Report an.
    parts = [
        # ADR-RPT-COMPANY-01: §0 sits between the masthead and the call. The reader
        # learns WHAT THE COMPANY DOES before being told how it ranks — the note used
        # to open on a ticker and a rank and never say what the business is.
        _title(fact_set),
        _section_company(fact_set),  # "das ist die firma / das macht die"
        _verdict_line(fact_set),  # der Call in EINEM Satz — Begründung am Ende
        _section_quant(fact_set),  # "das sagen unsere ml modelle dazu"
        _section_price_trend(fact_set),
        _section_news(fact_set, narration),  # "news von jz grad" + die Lesart
        _section_fundamentals(fact_set),
        _section_pros_cons(fact_set),
        _section_thesis(narration),  # "unsere theorien ... warum sie so steht"
        _section_scenarios(fact_set),
        _section_risks(fact_set, narration),
    ]
    insights_section = _section_insights(fact_set, narration)
    if insights_section is not None:
        parts.append(insights_section)
    parts.append(_section_confidence(fact_set))
    parts.append(_section_recommendation(fact_set))  # "ob man sie kaufen sollte"
    parts.append(_footer(fact_set))
    return "\n\n".join(parts) + "\n"
