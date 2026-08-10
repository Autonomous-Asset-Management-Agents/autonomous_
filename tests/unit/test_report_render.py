# tests/unit/test_report_render.py
# RPT-3 (#2002, Epic #1998) — deterministic FactSet -> report-section renderer.
#
# Test surfaces:
#   1. GOLDEN-FILE GATE 2 (no LLM): render(NVDA-FactSet) reproduces the
#      DETERMINISTIC sections of scratchpad/nvda_report_final.md number-exact —
#      built from the SAME committed RPT-1 fixtures (tests/fixtures/report/).
#   2. Mandatory caveats are ALWAYS present (net < EqualWeight 0.758 < 0.944;
#      dimensionless rank; sentiment direction ~0; no price target).
#   3. News render as deterministic bullets WITH url (never free LLM text).
#   4. Missing fundamentals -> honest "not wired" gap (never fabrication);
#      fundamentals present -> full FY structure number-exact from the FactSet.
#   5. Recommendation RULE: top-quintile -> BUY, bottom -> SELL, middle -> HOLD.
#   6. Determinism: same FactSet -> byte-identical Markdown.
#   7. Prose slots (thesis/catalysts/risks) are clearly-marked placeholders that
#      RPT-5 fills via the optional ``narration`` seam.
#
# Fully deterministic: no network / LLM / GPU / pandas.

import json
import os
from datetime import date, datetime

from core.report.fact_set import InMemoryDataContext, NewsItem, build_fact_set
from core.report.render import render

# ---------------------------------------------------------------------------
# Fixtures (the SAME minimal committed real NVDA slices RPT-1 uses)
# ---------------------------------------------------------------------------
_FX = os.path.join(os.path.dirname(__file__), "..", "fixtures", "report")
_CARD = os.path.join(
    os.path.dirname(__file__), "..", "..", "core", "report", "model_card.json"
)
AS_OF = date(2026, 5, 1)

# FY2026 fundamentals (the schema RPT-3 consumes; RPT-2 will populate it for
# real). Values mirror nvda_report_final.md §6 so the golden §6-with-data test
# proves the renderer surfaces them number-exact and adds nothing.
FUND_FY2026 = {
    "fiscal_label": "FY2026 10-K",
    "filing_date": "2026-02-25",
    "revenue_bn": 215.94,
    "revenue_yoy_pct": 65.5,
    "gross_margin_pct": 71.1,
    "op_margin_pct": 60.4,
    "net_income_bn": 120.07,
    "net_income_yoy_pct": 64.7,
    "net_margin_pct": 55.6,
    "eps_diluted": 4.90,
    "eps_yoy_pct": 66.7,
    "op_cash_flow_bn": 102.72,
    "equity_bn": 157.29,
    "liabilities_bn": 49.51,
    "debt_to_equity": 0.31,
    "market_cap_bn": 4.87,
    "pe": 40.5,
    "ps": 22.5,
}


def _load_json(name):
    with open(os.path.join(_FX, name), encoding="utf-8") as f:
        return json.load(f)


def _ctx(**overrides):
    kw = {
        "bars": [
            (date.fromisoformat(d), float(c)) for d, c in _load_json("nvda_bars.json")
        ],
        "lstm_series": [
            (date.fromisoformat(d), float(s))
            for d, s in _load_json("nvda_lstm_series.json")
        ],
        "lstm_cross_section": {
            sym: float(sc) for sym, sc in _load_json("nvda_lstm_xsec.json").items()
        },
        "model_card": _load_json_card(),
        "fundamentals": None,
        "news": [
            NewsItem(
                title=n["title"],
                published_utc=n["published_utc"],
                publisher=n["publisher"],
                url=n["url"],
            )
            for n in _load_json("nvda_news.json")
        ],
        "alt_signals": {},
    }
    kw.update(overrides)
    return InMemoryDataContext(**kw)


def _load_json_card():
    with open(_CARD, encoding="utf-8") as f:
        return json.load(f)


def _fs(**overrides):
    return build_fact_set("NVDA", AS_OF, _ctx(**overrides))


def _section(md, heading):
    """Genau EIN Abschnitt: von ``heading`` bis zur nächsten Überschrift.

    ⚠ Diese Slices waren auf die feste Nummer des FOLGE-Abschnitts gepinnt
    (``.split("## 7.")``). Nach der B5-Umnummerierung schnitten sie stumm zwei
    Abschnitte zu viel mit — grün, aber am falschen Text geprüft. Auf die nächste
    Überschrift zu schneiden ist gegen jede künftige Umnummerierung immun.
    """
    return md.split(heading)[1].split("\n## ")[0]


# ---------------------------------------------------------------------------
# 1. Structure — all 7 NVDA sections + head + recommendation are present
# ---------------------------------------------------------------------------
def test_renders_all_seven_sections_and_head():
    md = render(_fs())
    assert md.startswith("# NVDA")
    # the redesigned stock-focused note (RPT-3c): the body is about the STOCK,
    # not the bot. Sections were renumbered/added (Kurs- & Trendbild, Pro &
    # Contra) and the model track-record block was dropped from the per-stock body.
    assert "## 6. Investment-These" in md
    assert "## 1. Quant-Signal: der Rang" in md
    assert "## 2. Kurs- & Trendbild" in md
    assert "## 5. Pro & Contra" in md
    assert "## 3. Nachrichtenlage & Marktkontext" in md
    assert "## 4. Fundamentaldaten & Bewertung" in md
    assert "## 7. Szenarien" in md
    assert "## 8. Risiken" in md
    assert "## 10. Konfidenz & Grenzen" in md
    # head carries the as-of close and the ticker
    assert "198.45" in md
    assert "2026-05-01" in md


# ---------------------------------------------------------------------------
# 2. GOLDEN §2 Quant — numbers + the HARD mandatory caveats
# ---------------------------------------------------------------------------
def test_golden_quant_section_numbers_and_caveats():
    md = render(_fs())
    # score / percentiles / universe / trend (all from the FactSet)
    assert "4.10" in md
    assert "93.5" in md
    assert "95.7" in md
    assert "486" in md
    assert "1.55 → 2.52 → 3.29 → 3.58 → 4.10" in md
    # history stats (min / max / mean / std of the score range)
    for tok in ("-5.09", "6.91", "0.39", "2.27"):
        assert tok in md
    # MANDATORY caveat: the score is a relative, dimensionless cross-sectional
    # rank — not a price/return forecast for THIS single stock. RPT-CONS2 (item 2):
    # the "no single-stock price" caveat is stated ONCE (it survives in
    # §Empfehlung/§Konfidenz), NOT repeated as a redundant §1 closing sentence
    # that said the same thing three lines below "Der Score ist ein relativer Rang".
    assert "relativer Rang" in md
    assert "nicht welchen Kurs die Aktie erreichen wird" in md
    quant = _section(md, "## 1. Quant-Signal: der Rang")
    assert "sortiert Aktien gegeneinander" not in quant
    # OVERSELL GUARD: the model's aggregate track record (Sharpe / IC / t-stat /
    # EqualWeight / 80/20 leakage split) is identical for every stock and says
    # nothing about THIS name — it was REMOVED from the per-stock body and must
    # NOT reappear as if it described this recommendation.
    for gone in (
        "0.067",
        "20.4",
        "1.53",
        "0.97",
        "0.758",
        "0.944",
        "1.09",
        "Gleichgewichtung",
        "EqualWeight",
        "80/20",
        "Belastbarkeit des Signals",
    ):
        assert gone not in md
    # vol context (now in §3 Kurs- & Trendbild)
    assert "34.9" in md
    assert "39.1" in md


# ---------------------------------------------------------------------------
# 3. GOLDEN §3 Katalysatoren — deterministic bullets WITH url, no LLM prose
# ---------------------------------------------------------------------------
def test_golden_news_render_as_bullets_with_url():
    fs = _fs()
    md = render(fs)
    # each PIT news item appears as a bullet carrying title + date + publisher + url
    for ni in fs.news_items.value:
        assert ni.title in md
        assert ni.url in md
        assert ni.publisher in md
        assert ni.published_utc[:10] in md
    # the hard, terminated catalyst (earnings CC) is a bullet
    assert "NVIDIA Sets Conference Call for First-Quarter Financial Results" in md
    # bullets are markdown list items
    assert md.count("\n- ") >= 4


def test_missing_news_is_honest_gap_not_fabrication():
    md = render(_fs(news=[]))
    # news moved to §3 "Nachrichtenlage & Marktkontext" (was "Katalysatoren")
    assert "## 3. Nachrichtenlage & Marktkontext" in md
    sec = _section(md, "## 3. Nachrichtenlage & Marktkontext")
    # no invented catalysts; an explicit honest gap instead
    assert "Keine" in sec


# ---------------------------------------------------------------------------
# 4. GOLDEN §4 Szenarien — Bull/Base/Bear with proven, FactSet-sourced drivers
# ---------------------------------------------------------------------------
def test_golden_scenarios_bull_base_bear_with_proven_drivers():
    md = render(_fs())
    # scenarios are now §7 (was §4)
    scen = _section(md, "## 7. Szenarien")
    assert "Bull" in scen and "Base" in scen and "Bear" in scen
    # proven drivers are the FactSet numbers, not invented ones
    assert "95.7" in scen  # top-quintile percentile
    assert "197.22" in scen  # MA20 mean-reversion anchor
    assert "39.1" in scen  # elevated realized vol
    # weights are labelled calibration, NOT model-derived (no false precision)
    assert "Kalibrierung" in scen or "kalibrier" in scen.lower()
    # the fabricated forward "Ereignis-Termin" was removed from the Bear row —
    # the Bear case now cites the percentile + realized vol, not an invented event.
    assert "Ereignis-Termin" not in scen


# ---------------------------------------------------------------------------
# 5. GOLDEN §6 Fundamentals — honest gap when absent, full structure when present
# ---------------------------------------------------------------------------
def test_fundamentals_absent_is_honest_gap():
    md = render(_fs(fundamentals=None))
    sec = _section(md, "## 4. Fundamentaldaten")
    assert "keine geprüften Fundamentaldaten" in sec  # honest gap, no fabrication
    # nothing fabricated: the FY revenue figure must NOT appear
    assert "215.94" not in md


def test_fundamentals_present_renders_full_structure_number_exact():
    md = render(_fs(fundamentals=FUND_FY2026))
    sec = _section(md, "## 4. Fundamentaldaten")
    assert "FY2026 10-K" in sec
    assert "2026-02-25" in sec
    for tok in (
        "215.94",
        "65.5",
        "71.1",
        "60.4",
        "120.07",
        "64.7",
        "55.6",
        "4.90",
        "66.7",
        "102.72",
        "157.29",
        "49.51",
        "0.31",
        "4.87",
        "40.5",
        "22.5",
    ):
        assert tok in sec, f"missing fundamentals token {tok!r}"
    # the honest 'not wired into the model' caveat rides along
    assert "NICHT ins Modell verdrahtet" in sec


# ---------------------------------------------------------------------------
# 6. GOLDEN §7 Konfidenz — one-input-not-oracle + sentiment ~0 + no price target
# ---------------------------------------------------------------------------
def test_golden_confidence_section_caveats():
    md = render(_fs())
    # confidence is now §10 "Konfidenz & Grenzen"
    sec = md.split("## 10. Konfidenz & Grenzen")[1]
    # the single honesty line: ONLY the relative cross-sectional rank is
    # independently proven, via walk-forward, of limited strength; everything
    # else is context; and there is no price target / return promise.
    assert "Belastbarkeit & Grenzen" in sec
    assert "relative Rangfolge" in sec
    # F1 (adversarial fix): the validation is honestly described as a historical
    # back-comparison with known methodological limits — NOT falsely upgraded to a
    # clean temporal "Walk-forward-Test" (the model card's split is random 80/20).
    assert "historischen Rückvergleich" in sec
    assert "Walk-forward-Test" not in sec
    # F1: the material limitation — net of cost the edge has not reliably beaten a
    # simple equal-weight approach — is disclosed in plain language (no Sharpe dump).
    assert "schwach" in sec
    assert "nicht zuverlässig geschlagen" in sec
    assert "Kein Kursziel" in sec
    assert "keine Renditezusage" in sec
    assert "keine Anlageberatung" in sec
    # OVERSELL GUARD: the removed model track-record numbers (incl. the t-stat)
    # must NOT reappear in the confidence section.
    for gone in ("20.4", "0.758", "0.944", "1.53", "0.97"):
        assert gone not in sec


# ---------------------------------------------------------------------------
# 7. Recommendation RULE (head): top-quintile -> BUY, bottom -> SELL, mid -> HOLD
# ---------------------------------------------------------------------------
def _head(md):
    """The recommendation block with its reasoning.

    B5: this used to be the note's HEAD ("## Empfehlung auf einen Blick", sliced at
    "## 1."). Georg's dictated order moved the full recommendation to the END, so the
    slice follows it there — the assertions below are about the RECOMMENDATION, not
    about a position on the page. Sliced to the footer so the footer's own prose can
    never satisfy (or break) a "not in head" guard.
    """
    return md.split("## Empfehlung")[1].split("\n---")[0]


def _masthead(md):
    """The masthead above §0 — where the close/pullback are printed (_title)."""
    return md.split("## 0.")[0]


def test_recommendation_top_quintile_is_buy_with_mandatory_caveats():
    head = _head(render(_fs()))
    # top-quintile -> KAUFEN, weight the position up
    assert "KAUFEN" in head
    assert "übergewichten" in head.lower()
    # MANDATORY honesty guards of the redesigned head:
    # (1) conviction is capped at "mittel" and is NEVER oversold as high/strong.
    assert "**Wie sicher:** Mittel" in head
    assert "hoch" not in head.lower()
    assert "stark" not in head.lower()
    # (2) the rank is the ONE systematically-tested building block — a hint of
    #     limited strength, not an "independently verified" single-stock oracle
    #     (F9: it is our own test, so "unabhängig" is not claimed).
    assert "systematisch getestete Baustein" in head
    assert "begrenzter Stärke" in head
    assert "unabhängig" not in head
    assert "nicht welchen Kurs die Aktie erreichen wird" in head
    # (3) horizon is stated and (4) there is explicitly no price target.
    assert "Handelstage" in head  # horizon ~5d
    assert "Kursziel" in head and "keines" in head  # no price target
    # OVERSELL GUARD: the model's aggregate track record is NOT sold as this
    # stock's edge — the removed numbers / execution caveat are gone.
    md = render(_fs())
    for gone in ("0.758", "0.944", "Umsetzungshinweis", "Umschlag und Haltedauer"):
        assert gone not in md


def test_recommendation_bottom_quintile_is_sell():
    # push NVDA to the very bottom of the cross section
    xsec = dict.fromkeys(_load_json("nvda_lstm_xsec.json"), 100.0)
    xsec["NVDA"] = -99.0
    head = _head(render(_fs(lstm_cross_section=xsec)))
    assert "VERKAUFEN" in head or "Untergewichten" in head
    # not a buy: check the buy-specific weighting word (avoid the VERKAUFEN⊃KAUFEN
    # substring trap).
    assert "Übergewichten" not in head


def test_recommendation_middle_is_hold():
    # a small symmetric universe with NVDA in the middle (~50th pct)
    xsec = {"A": 10.0, "B": 20.0, "NVDA": 30.0, "C": 40.0, "D": 50.0}
    head = _head(render(_fs(lstm_cross_section=xsec)))
    assert "HALTEN" in head
    # the middle is neither a buy nor a sell verb (VERKAUFEN ⊃ KAUFEN, so both
    # absence checks together also rule out the sell verb)
    assert "KAUFEN" not in head and "VERKAUFEN" not in head


def test_recommendation_without_cross_section_abstains_entirely():
    """No rank -> NO call. Not a neutral one. None.

    ⚠ This test used to be named ..._is_honest_hold and asserted `"HALTEN" in head`.
    It did not miss the defect — it CANONISED it, and called the fabrication honest in
    its own name. That is why a report with no price, no rank, no fundamentals and no
    news shipped "HALTEN — neutral / abwarten" + "Wie sicher: Mittel" past a green suite
    and a 0-flag audit, reading to a user exactly like a real recommendation.

    The intent behind the old test was right — no rank must be handled honestly. Its
    expectation was wrong: suppressing the *bucket* while still emitting a *verb* is not
    an honest gap, it is a fabricated call with the evidence filed off. The precedent we
    hold elsewhere: LSTMSignalAgent._vote_on_rank abstains at weight 0 rather than
    voting a plausible-looking 0.5.
    """
    head = _head(render(_fs(lstm_cross_section={})))

    assert "HALTEN" not in head
    assert "KAUFEN" not in head and "VERKAUFEN" not in head
    # ...and the abstention is SAID, not merely implied by silence
    assert "keine Einschätzung" in head
    assert "keine Empfehlung" in head
    # no confidence may be claimed in a ranking that does not exist
    assert "Wie sicher" not in head


# ---------------------------------------------------------------------------
# 8. Determinism — same FactSet -> byte-identical Markdown
# ---------------------------------------------------------------------------
def test_deterministic_same_factset_same_markdown():
    fs = _fs()
    assert render(fs) == render(fs)
    assert isinstance(render(fs), str)


# ---------------------------------------------------------------------------
# 9. Prose slots — clearly-marked placeholders, filled by the RPT-5 seam
# ---------------------------------------------------------------------------
def test_prose_slots_are_marked_placeholders_by_default():
    md = render(_fs())
    # machine-parseable slot markers for the three narration sections
    assert "SLOT:thesis" in md
    assert "SLOT:catalysts" in md
    assert "SLOT:risks" in md
    # each unfilled slot carries a deterministic, user-facing fallback note (no
    # internal dev-stage names leak into the report the user reads).
    assert "Die belegten Bausteine stehen in Abschnitt" in md
    assert "RPT-5" not in md


def test_narration_fills_slots_without_touching_deterministic_facts():
    md = render(
        _fs(),
        narration={
            "thesis": "PROSE_THESIS_MARKER",
            "catalysts": "PROSE_CATALYSTS_MARKER",
            "risks": "PROSE_RISKS_MARKER",
        },
    )
    assert "PROSE_THESIS_MARKER" in md
    assert "PROSE_CATALYSTS_MARKER" in md
    assert "PROSE_RISKS_MARKER" in md
    # slot placeholders are gone once filled
    assert "SLOT:thesis" not in md
    # deterministic facts still present (narration only *places* prose, it never
    # touches the sourced numbers)
    assert "Platz 21 von 486" in md
    assert "1.55 → 2.52 → 3.29 → 3.58 → 4.10" in md


# ---------------------------------------------------------------------------
# 10. PIT sanity — the renderer never surfaces post-as-of data (it can't:
#     the FactSet already filtered it; this guards against a regression where
#     render bypasses the FactSet).
# ---------------------------------------------------------------------------
def test_render_only_reads_pit_filtered_factset_news():
    future = NewsItem(
        title="SYNTHETIC FUTURE HEADLINE",
        published_utc="2026-06-01T12:00:00+00:00",
        publisher="Test",
        url="https://example.com/future",
    )
    md = render(_fs(news=_load_news_plus(future)))
    assert "SYNTHETIC FUTURE HEADLINE" not in md


def _load_news_plus(extra):
    base = [
        NewsItem(
            title=n["title"],
            published_utc=n["published_utc"],
            publisher=n["publisher"],
            url=n["url"],
        )
        for n in _load_json("nvda_news.json")
    ]
    base.append(extra)
    return base


# ---------------------------------------------------------------------------
# 11. Head close/pullback are FactSet-sourced (no fabricated price)
# ---------------------------------------------------------------------------
def test_head_close_and_pullback_from_factset():
    # B5: close/pullback are printed by _title, i.e. the MASTHEAD. The old head slice
    # (everything before "## 1.") happened to contain it; assert it where it lives.
    head = _masthead(render(_fs()))
    assert "198.45" in head
    assert "-8.4" in head or "8.4" in head


# ---------------------------------------------------------------------------
# 13. RPT-CONS: the "keine Anlageberatung" disclaimer appears EXACTLY ONCE.
#
# Georg, wörtlich: "der Disclaimer steht DOPPELT." Er stand in §10 UND in der
# Fußzeile — Wort für Wort. Ein doppelter Rechtssatz ist keine doppelte
# Absicherung, nur Redundanz, die den Report aufbläht ("zu viel"). Die
# kanonische Fassung bleibt in §10 (dort gepinnt durch
# test_golden_confidence_section_caveats); die Fußzeile behält ihren EIGENEN
# Punkt (Audit-Umfang + Modell-Güte-Zeiger), trägt den Disclaimer aber nicht
# mehr. Er MUSS genau einmal bleiben (LIVE-DEMO_COMPLIANCE_GATE / DISCLAIMER.md
# "No investment advice") — deshalb == 1, nicht == 0.
# ---------------------------------------------------------------------------
def test_disclaimer_appears_exactly_once_and_not_in_footer():
    md = render(_fs())
    assert (
        md.count("keine Anlageberatung") == 1
    ), "der Disclaimer muss genau EINMAL erscheinen — nicht doppelt (§10 + Fußzeile)"
    # kanonische Fassung bleibt in §10
    conf = md.split("## 10. Konfidenz & Grenzen")[1].split("\n## ")[0]
    assert "keine Anlageberatung" in conf
    # die Fußzeile trägt den Disclaimer NICHT mehr, behält aber ihren Eigeninhalt
    footer = md.split("\n---\n")[-1]
    assert (
        "Anlageberatung" not in footer
    ), "Fußzeile darf den Disclaimer nicht duplizieren"
    assert "Herkunft jeder Zahl" in footer


def test_disclaimer_appears_exactly_once_on_thin_panel_path():
    # auch der abstinente / schmale Pfad (thin-panel §10) darf ihn nicht doppeln
    md = render(_fs(lstm_cross_section={}))
    assert md.count("keine Anlageberatung") == 1
    footer = md.split("\n---\n")[-1]
    assert "Anlageberatung" not in footer


# ---------------------------------------------------------------------------
# 12. Every rendered as-of/date is the PIT date (spot-check earnings CC bullet)
# ---------------------------------------------------------------------------
def test_earnings_cc_bullet_carries_correct_date():
    fs = _fs()
    md = render(fs)
    for ni in fs.news_items.value:
        if "Conference Call" in ni.title:
            assert datetime.fromisoformat(ni.published_utc).date() <= AS_OF
            assert "2026-04-29" in md


# ---------------------------------------------------------------------------
# 14. RPT-CONS2 — say each cross-cutting caveat EXACTLY ONCE.
#
# Georg: "zu viel" — a measurement found ~40% of the note talks ABOUT the note.
# PR #2203 removed the doubled disclaimer; this pins the remaining measured
# duplications: the "no single-stock price" caveat (item 2) and the "context, not
# independent alpha" idea (item 4) are each stated once, in ONE canonical place,
# and struck from the per-section duplicates. NOTE: sections are NOT omitted —
# the numbering/cross-reference invariants (test_report_b5_order) forbid it — only
# the redundant SENTENCES are removed.
# ---------------------------------------------------------------------------
def test_context_not_alpha_is_consolidated_into_confidence_once():
    md = render(_fs())
    conf = md.split("## 10. Konfidenz & Grenzen")[1].split("\n## ")[0]
    # ONE canonical "context, not independent alpha" statement — now covering the
    # price/technical picture too, so nothing that used to be said inline is lost.
    assert "liefern Kontext" in conf
    assert "Kursbild" in conf
    # the per-section inline duplicates of that idea are struck:
    price = _section(md, "## 2. Kurs- & Trendbild")
    assert "kein eigenständiger Kaufgrund" not in price
    news = _section(md, "## 3. Nachrichtenlage")
    assert "dokumentierter Kontext" not in news
    # §4's duplicate ("dienen der Einordnung") is only reachable when fundamentals
    # are present, so exercise that path explicitly.
    md_f = render(_fs(fundamentals=FUND_FY2026))
    fund = _section(md_f, "## 4. Fundamentaldaten")
    assert "dienen der Einordnung" not in fund
    # ...but the section-specific MECHANICAL honesty statement stays: the
    # fundamentals genuinely do not feed the rank (a different, stronger claim than
    # the editorial "not an independent advantage").
    assert "NICHT ins Modell verdrahtet" in fund
