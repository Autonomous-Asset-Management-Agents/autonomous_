# tests/unit/test_report_auditor.py
# RPT-4 (#2003, Epic #1998) — the mechanical auto-audit gate.
#
# Test surfaces (TDD, red first):
#   1. GOLDEN GATE 4a POSITIVE: audit(render(NVDA-FactSet), NVDA-FactSet) ->
#      integrity_ok=True, 0 flags, all deterministic numeric claims VALID/DERIVED
#      (>=22), 0 false-REJECT on correctly-rounded numbers (198.45, 65.5%, P/E 40.5,
#      4.10, 34.9%, ...).
#   2. Rounding tolerance: displayed roundings of raw facts never false-reject.
#   3. Derived registry: P/E (close/eps), P/S (mktcap/revenue) validate even when
#      the pe/ps fields are absent from the FactSet fundamentals.
#   4. GOLDEN GATE 4b ADVERSARIAL: 10 injected hallucinations -> 10/10 STRIKE,
#      Recall 10/10, Precision 10/10 (no real number false-flagged).
#   5. Unit-bearing bypasses the small-int structural pass ("$5 Bio" strikes even
#      though 5 is a small integer / a horizon-day count in the FactSet).
#   6. Structural tolerance: section numbers, years, small integers never strike.
#   7. Entity: a fabricated ticker strikes; real tickers (NVDA/AMD) validate;
#      German ALL-CAPS report vocabulary never strikes.
#   8. Dates: a fabricated date strikes; every PIT date validates.
#   9. Empty / degenerate input never crashes; audit of "" is integrity_ok.
#  10. Determinism: same (report, FactSet) -> identical AuditResult.
#  11. to_table() renders a machine-checkable Markdown audit table.
#  12. RPT-4b semantic hook is a documented, LLM-free stub NOT called by audit().
#
# Fully deterministic: no network / LLM / GPU / pandas / redis.

import json
import os
from datetime import date

from core.report.auditor import (
    DERIVED,
    STRUCTURAL,
    VALID,
    AuditResult,
    Claim,
    audit,
    semantic_attribution_hook,
)
from core.report.fact_set import InMemoryDataContext, NewsItem, build_fact_set
from core.report.render import render

# ---------------------------------------------------------------------------
# Fixtures (the SAME committed real NVDA slices RPT-1/RPT-3 use)
# ---------------------------------------------------------------------------
_FX = os.path.join(os.path.dirname(__file__), "..", "fixtures", "report")
_CARD = os.path.join(
    os.path.dirname(__file__), "..", "..", "core", "report", "model_card.json"
)
AS_OF = date(2026, 5, 1)

# FY2026 fundamentals — mirrors the RPT-3 golden §6-with-data fixture so the
# auditor is exercised against the full report surface (P/E, P/S, YoY, Bio/Mrd).
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


def _load_card():
    with open(_CARD, encoding="utf-8") as f:
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
        "model_card": _load_card(),
        "fundamentals": FUND_FY2026,
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


def _fs(**overrides):
    return build_fact_set("NVDA", AS_OF, _ctx(**overrides))


# The 10 adversarial injections used by the golden GATE-4b test. Each appends a
# sentence carrying a fabricated fact; the auditor must STRIKE every one. The
# expected token is what appears in ``AuditResult.flags``.
INJECTIONS = [
    (
        "merger_amount",
        "\n\nDie angebliche Uebernahme erfolgte fuer 88.8 Mrd USD.",
        "88.8 Mrd",
    ),
    (
        "verified_5bio",
        "\n\nVerifizierte Marktkapitalisierung: 5 Bio USD (bestaetigt).",
        "5 Bio",
    ),
    ("sharpe_3", "\n\nDer realisierte Netto-Sharpe liegt bei 3.0.", "3.0"),
    ("price_target", "\n\nKursziel: 250 USD in zwoelf Monaten.", "250 USD"),
    ("fabricated_pct", "\n\nDie Trefferquote betrug 137%.", "137%"),
    ("growth_pct", "\n\nUmsatzwachstum von 300% erwartet.", "300%"),
    ("forward_pe", "\n\nEin Forward P/E von 12.3 sei attraktiv.", "12.3"),
    ("ticker", "\n\nWir bevorzugen $TSLA gegenueber Wettbewerbern.", "$TSLA"),
    (
        "future_date",
        "\n\nDie Zahlen wurden am 2026-09-15 veroeffentlicht.",
        "2026-09-15",
    ),
    ("insider_volume", "\n\nInsider kauften 7.7 Mio Aktien.", "7.7 Mio"),
]


# ---------------------------------------------------------------------------
# 1. GOLDEN GATE 4a — POSITIVE: the rendered NVDA report is fully sourced
# ---------------------------------------------------------------------------
def test_golden_positive_rendered_report_has_integrity():
    fs = _fs()
    res = audit(render(fs), fs)
    assert isinstance(res, AuditResult)
    assert res.integrity_ok is True
    assert list(res.flags) == []
    # the load-bearing deterministic numeric claims all validate (>= ~22)
    numeric_ok = [c for c in res.claims if c.kind == "number" and c.matched]
    assert len(numeric_ok) >= 22


def test_golden_positive_key_numbers_not_false_rejected():
    fs = _fs()
    md = render(fs)
    res = audit(md, fs)
    matched_tokens = {c.token for c in res.claims if c.matched}
    # correctly-rounded, still-present facts must be VALID, never a false-REJECT.
    # (The redesigned note dropped the model track-record numbers 0.758/0.944
    # from the body; the load-bearing per-stock numbers are the close, the rank/
    # percentiles, the universe size and the moving averages.)
    for tok in ("198.45 USD", "4.10", "95.7", "197.22", "93.5", "486"):
        assert tok in matched_tokens, f"{tok!r} should validate"
    # fundamentals + percent + derived valuation
    for needle in ("65.5", "40.5", "22.5", "34.9", "39.1", "-8.4"):
        assert any(
            needle in c.token and c.matched for c in res.claims
        ), f"{needle!r} should validate"
    # the removed aggregate track-record numbers are simply not in the body.
    assert "0.758" not in md
    assert "0.944" not in md


def test_no_fundamentals_report_is_also_clean():
    fs = _fs(fundamentals=None)
    res = audit(render(fs), fs)
    assert res.integrity_ok is True
    assert list(res.flags) == []


# ---------------------------------------------------------------------------
# 2. Rounding tolerance — displayed roundings never false-reject
# ---------------------------------------------------------------------------
def test_rounding_tolerance_no_false_reject():
    fs = _fs()
    # 198.45 is round(198.4499, 2); -8.4 is round(pullback_pct, 1); both VALID
    res = audit("Schlusskurs 198.45 USD, Pullback -8.4%.", fs)
    assert res.integrity_ok is True
    assert all(c.matched for c in res.claims)


def test_percent_scaling_matches_fraction_facts():
    # realized vol is stored as a fraction (0.349); rendered as 34.9%
    fs = _fs()
    res = audit("20d realized vol 34.9%, 63d 39.1%.", fs)
    assert res.integrity_ok is True


# ---------------------------------------------------------------------------
# 3. Derived registry — P/E (close/eps), P/S (mktcap/revenue) accepted
# ---------------------------------------------------------------------------
def test_derived_registry_pe_ps_without_direct_fields():
    fund = dict(FUND_FY2026)
    del fund["pe"]
    del fund["ps"]
    fs = _fs(fundamentals=fund)
    # P/E 40.5 = 198.45 / 4.90 ; P/S 22.6 = 4.87e3 / 215.94
    res = audit("Bewertung: P/E 40.5, P/S 22.6.", fs)
    assert res.integrity_ok is True, list(res.flags)
    verdicts = {c.token: c.verdict for c in res.claims if c.kind == "number"}
    # P/E is re-wired by build_fact_set as a DIRECT, provenanced fact (pe = as-of
    # close / trailing diluted EPS) whenever a close and EPS exist — so even with the
    # incoming pe field deleted it verifies as a direct VALID field, strictly more
    # auditable than a silent re-derivation. (fix/peg-wiring: this is exactly what
    # made valuation_vs_growth_pattern fire again in production.)
    assert verdicts["40.5"] == VALID
    # P/S has NO fact-layer wiring, so it still resolves only via the derived registry.
    assert verdicts["22.6"] == DERIVED


def test_derived_registry_peg_from_pe_and_revenue_growth():
    # PEG = P/E 40.5 / +65.5% revenue growth = 0.62 — the RPT-7 valuation insight
    # surfaces it; the auditor must accept it as DERIVED, never a fabrication.
    fs = _fs()
    res = audit("Bewertung: trailing PEG 0.62 auf +65.5% Wachstum.", fs)
    assert res.integrity_ok is True, list(res.flags)
    verdicts = {c.token: c.verdict for c in res.claims if c.kind == "number"}
    assert verdicts["0.62"] == DERIVED


# ---------------------------------------------------------------------------
# 4. GOLDEN GATE 4b — ADVERSARIAL: 10 injected hallucinations all STRIKE
# ---------------------------------------------------------------------------
def test_golden_adversarial_all_injections_are_struck():
    fs = _fs()
    clean = render(fs)
    injected = clean + "".join(text for _, text, _ in INJECTIONS)
    res = audit(injected, fs)

    assert res.integrity_ok is False
    flag_tokens = [c.token for c in res.flags]
    # Recall: every injected fabrication is flagged
    for name, _text, expected in INJECTIONS:
        assert expected in flag_tokens, f"missed injection {name!r} ({expected!r})"
    # Precision: the clean report contributes zero flags, so every flag is a TP
    assert len(res.flags) == len(INJECTIONS)


def test_adversarial_precision_recall_is_perfect():
    fs = _fs()
    clean = render(fs)
    injected = clean + "".join(text for _, text, _ in INJECTIONS)
    res = audit(injected, fs)
    injected_tokens = {expected for _, _, expected in INJECTIONS}
    flagged = [c.token for c in res.flags]
    tp = sum(1 for t in flagged if t in injected_tokens)
    fp = sum(1 for t in flagged if t not in injected_tokens)
    fn = len(injected_tokens) - len({t for t in flagged if t in injected_tokens})
    recall = tp / (tp + fn)
    precision = tp / (tp + fp)
    assert recall == 1.0
    assert precision == 1.0


# ---------------------------------------------------------------------------
# 5. Unit-bearing bypasses the small-integer structural pass
# ---------------------------------------------------------------------------
def test_unit_bearing_number_is_not_saved_by_small_int_structural():
    fs = _fs()
    # 5 is a small int AND horizon_days=5 in the FactSet, but "5 Bio" is a
    # market-cap magnitude (own computed = 4.87 Bio) -> must STRIKE.
    res = audit("Verifizierte Marktkapitalisierung 5 Bio USD.", fs)
    assert res.integrity_ok is False
    assert any(c.token == "5 Bio" and not c.matched for c in res.claims)


def test_bare_small_int_still_structural():
    fs = _fs()
    # a bare "~5 Handelstage" is structurally tolerated (no unit) and also the
    # horizon fact; a bare section-style integer never strikes.
    res = audit("Zeithorizont ~5 Handelstage; siehe Abschnitt 2 und Abschnitt 4.", fs)
    assert res.integrity_ok is True


# ---------------------------------------------------------------------------
# 6. Structural tolerance — section numbers, years, small ints
# ---------------------------------------------------------------------------
def test_structural_tokens_never_strike():
    fs = _fs()
    res = audit("## 1. These\n## 7. Konfidenz\nJahr 2026, Punkt 3 und 9.", fs)
    assert res.integrity_ok is True
    assert all(c.matched for c in res.claims)


# ---------------------------------------------------------------------------
# 7. Entities — fabricated ticker strikes, real ones validate, caps vocab safe
# ---------------------------------------------------------------------------
def test_fabricated_ticker_strikes_real_ticker_validates():
    fs = _fs()
    res = audit("Wir moegen NVDA und AMD, aber nicht $TSLA.", fs)
    by_tok = {c.token: c for c in res.claims if c.kind == "entity"}
    assert by_tok["NVDA"].matched is True  # the FactSet symbol
    assert by_tok["AMD"].matched is True  # appears in a PIT news title
    assert by_tok["$TSLA"].matched is False  # never sourced -> STRIKE


def test_german_uppercase_vocabulary_is_not_flagged():
    fs = _fs()
    # BUY/MITTEL/KEIN/USD are report vocabulary, not tickers
    res = audit("Empfehlung BUY, Konviktion MITTEL, KEIN Kursziel in USD.", fs)
    entity_flags = [c for c in res.flags if c.kind == "entity"]
    assert entity_flags == []


# ---------------------------------------------------------------------------
# 8. Dates — fabricated date strikes, PIT dates validate
# ---------------------------------------------------------------------------
def test_fabricated_date_strikes_pit_dates_validate():
    fs = _fs()
    res = audit("As-of 2026-05-01, aber am 2026-09-15 passiert nichts.", fs)
    dates = {c.token: c.matched for c in res.claims if c.kind == "date"}
    assert dates["2026-05-01"] is True
    assert dates["2026-09-15"] is False


# ---------------------------------------------------------------------------
# 9. Empty / degenerate input
# ---------------------------------------------------------------------------
def test_empty_report_is_integrity_ok():
    fs = _fs()
    res = audit("", fs)
    assert res.integrity_ok is True
    assert list(res.claims) == []
    assert list(res.flags) == []


def test_whitespace_and_prose_only_report_is_ok():
    fs = _fs()
    res = audit("   \n\nKein Kursziel, keine Return-Zahl.\n", fs)
    assert res.integrity_ok is True


# ---------------------------------------------------------------------------
# 10. Determinism
# ---------------------------------------------------------------------------
def test_deterministic_same_inputs_same_result():
    fs = _fs()
    md = render(fs)
    a = audit(md, fs)
    b = audit(md, fs)
    assert a.integrity_ok == b.integrity_ok
    assert [(_c.token, _c.verdict, _c.matched) for _c in a.claims] == [
        (_c.token, _c.verdict, _c.matched) for _c in b.claims
    ]
    assert a.to_table() == b.to_table()


# ---------------------------------------------------------------------------
# 11. to_table() — a machine-checkable Markdown audit table
# ---------------------------------------------------------------------------
def test_to_table_renders_markdown_with_verdicts():
    fs = _fs()
    injected = render(fs) + "\n\nKursziel 250 USD."
    table = audit(injected, fs).to_table()
    assert table.startswith("|")
    assert "Urteil" in table or "Verdict" in table
    # the struck token appears in the table
    assert "250 USD" in table
    # a header separator row
    assert "|---" in table.replace(" ", "")


# ---------------------------------------------------------------------------
# 12. RPT-4b semantic hook — documented, LLM-free stub, NOT run by audit()
# ---------------------------------------------------------------------------
def test_semantic_hook_is_a_documented_stub_not_wired():
    # the hook exists and is honest about being unimplemented (RPT-4b); it never
    # imports or calls an LLM in the deterministic core.
    assert semantic_attribution_hook.__doc__ is not None
    assert "RPT-4b" in semantic_attribution_hook.__doc__
    # calling it without a model does not raise and returns the "not run" sentinel
    assert semantic_attribution_hook("report", _fs()) is None


def test_claim_is_frozen_value_object():
    fs = _fs()
    res = audit("Kursziel 250 USD.", fs)
    c = res.claims[0]
    assert isinstance(c, Claim)
    try:
        c.matched = True  # frozen dataclass -> must raise
        raised = False
    except Exception:
        raised = True
    assert raised


# ---------------------------------------------------------------------------
# 13. News-headline figures + display roundings (Epic #1998 — RPT-4 fix)
#
# A number that appears VERBATIM in a PIT news headline is *sourced* (the
# headline IS the provenance), and a faithful 1-decimal display rounding of a
# raw fact is not a fabrication. Both were false-REJECTED before this fix. The
# fabrication gate MUST stay sharp: a magnitude that is in NO news and NO fact
# still strikes, and a bare headline integer can never source a money magnitude.
# ---------------------------------------------------------------------------
def _news(title):
    return NewsItem(
        title=title,
        published_utc="2026-04-20T12:00:00+00:00",
        publisher="Reuters",
        url="https://example.com/catalyst",
    )


def test_news_headline_money_magnitude_is_sourced():
    # "$5 Trillion" is a small money integer the structural money-guard blocks
    # from the computed number pool, yet it appears verbatim in a real headline
    # -> it is SOURCED, not a fabrication.
    fs = _fs(news=[_news("Nvidia charts a $5 Trillion path, analysts say")])
    res = audit("Ein Analyst skizziert einen $5 Trillion Pfad.", fs)
    by = {c.token: c for c in res.claims if c.kind == "number"}
    assert by["$5 Trillion"].matched is True
    assert by["$5 Trillion"].verdict == VALID
    assert res.integrity_ok is True, list(res.flags)


def test_news_headline_thousands_separator_figures_are_sourced():
    # "13,600%" / "$25,000" carry thousands separators; the old title scan split
    # them (13 + 600, 25 + 000) so the full figure struck. They must validate.
    fs = _fs(
        news=[
            _news("Palantir stock is up 13,600% since its 2013 IPO"),
            _news("You would have $25,000 today had you invested early"),
        ]
    )
    res = audit("Die Aktie stieg 13,600%; aus dem Einsatz waeren $25,000 geworden.", fs)
    matched = {c.token for c in res.claims if c.matched}
    assert "13,600%" in matched
    assert "$25,000" in matched
    assert res.integrity_ok is True, list(res.flags)


def test_display_rounding_of_small_percent_is_sourced():
    # pullback raw 0.72% renders as 0.7%; 2% relative tolerance is too tight for
    # such a small value, but a faithful 1-decimal display rounding is VALID.
    fs = _fs(bars=[(date(2026, 4, 30), 100.0), (date(2026, 5, 1), 100.72)])
    assert round(fs.pullback_pct.value, 2) == 0.72
    res = audit("Pullback 0.7%.", fs)
    assert res.integrity_ok is True, list(res.flags)
    assert all(c.matched for c in res.claims)


def test_fabricated_magnitude_not_in_news_still_strikes():
    # "$9 Trillion" is in NO news headline and NO fact -> STRIKE. The news pool
    # is literal, never a blanket bypass.
    fs = _fs(news=[_news("Nvidia charts a $5 Trillion path, analysts say")])
    res = audit("Ein $9 Trillion Ziel steht im Raum.", fs)
    assert res.integrity_ok is False
    assert any(c.token == "$9 Trillion" and not c.matched for c in res.claims)


def test_bare_news_integer_does_not_source_a_money_magnitude():
    # "5" appears in the headline but NOT as a money magnitude; a report "5 Bio"
    # must still STRIKE (the small-int money-guard intent is preserved).
    fs = _fs(news=[_news("The top 5 chip stocks to watch this quarter")])
    res = audit("Verifizierte Marktkapitalisierung 5 Bio USD.", fs)
    assert res.integrity_ok is False
    assert any(c.token == "5 Bio" and not c.matched for c in res.claims)


def test_integer_display_rounding_does_not_loosen_money_guard():
    # regression guard for the fix itself: faithful display rounding must NOT
    # apply to integer tokens, else "5 Bio" would round-match market-cap 4.87.
    fund = dict(FUND_FY2026)
    fund["market_cap_bn"] = 4.87
    fs = _fs(fundamentals=fund)
    res = audit("Verifizierte Marktkapitalisierung 5 Bio USD.", fs)
    assert res.integrity_ok is False
    assert any(c.token == "5 Bio" and not c.matched for c in res.claims)


def test_news_percent_or_bare_currency_does_not_source_money_scale():
    # A small percent / bare-currency figure in a headline shares only the
    # mantissa, not the money-SCALE class. It must NOT license a "5 Bio" claim
    # (the small-int money-guard would otherwise be defeated by any "5 %"/"$5").
    for title in ("Index rose 5% today", "You could invest $5 now"):
        fs = _fs(news=[_news(title)])
        res = audit("Verifizierte Marktkapitalisierung 5 Bio USD.", fs)
        assert res.integrity_ok is False, f"{title!r}: {list(res.flags)}"
        assert any(c.token == "5 Bio" and not c.matched for c in res.claims)


def test_news_scale_word_inside_a_longer_word_is_not_a_magnitude():
    # "billionaires" / "biotech" merely CONTAIN a scale word; the number next to
    # them is not a magnitude and must not source a fabricated money claim.
    fs1 = _fs(news=[_news("The top 8 billionaires met in Davos")])
    r1 = audit("Verifizierte Marktkapitalisierung 8 Mrd USD.", fs1)
    assert r1.integrity_ok is False, list(r1.flags)
    fs2 = _fs(news=[_news("Only 3 biotech names matter this year")])
    r2 = audit("Verifizierte Marktkapitalisierung 3 Bio USD.", fs2)
    assert r2.integrity_ok is False, list(r2.flags)


def test_news_wrong_scale_class_does_not_source_money_claim():
    # "5 million" in a headline is a scale magnitude, but a DIFFERENT scale than
    # a "5 Bio" (trillion) claim — mantissa alone must not cross scale classes.
    fs = _fs(news=[_news("The app crossed 5 million daily users")])
    res = audit("Verifizierte Marktkapitalisierung 5 Bio USD.", fs)
    assert res.integrity_ok is False, list(res.flags)
    assert any(c.token == "5 Bio" and not c.matched for c in res.claims)


# ---------------------------------------------------------------------------
# RPT-4c (#2078 re-review) — German decimal-comma must not re-open the money
# guard. "8,3 Mrd" is ONE figure (8.3, scale B); it must never leave a stray
# fractional "3" that a fabricated "3 Mrd" can source. German (comma-decimal) is
# a first-class input for this BaFin-facing gate.
# ---------------------------------------------------------------------------
def test_german_decimal_comma_does_not_fabricate_magnitude():
    # News says revenue rose to 8,3 Mrd (= 8.3 billion). A report that fabricates
    # a DIFFERENT, unrelated "3 Mrd" must STRIKE — the fractional "3" of "8,3" is
    # not an independent magnitude.
    fs = _fs(news=[_news("Umsatz stieg auf 8,3 Mrd Euro im Quartal")])
    res = audit("Ein erfundener Deal ueber 3 Mrd USD steht im Raum.", fs)
    assert res.integrity_ok is False, list(res.flags)
    assert any(c.token == "3 Mrd" and not c.matched for c in res.claims)


def test_german_decimal_comma_trillion_does_not_fabricate_magnitude():
    fs = _fs(news=[_news("Marktkapitalisierung erreicht 1,5 Bio USD")])
    res = audit("Verifizierte Marktkapitalisierung 5 Bio USD.", fs)
    assert res.integrity_ok is False, list(res.flags)
    assert any(c.token == "5 Bio" and not c.matched for c in res.claims)


def test_german_decimal_comma_figure_itself_is_sourced():
    # Recall side: the genuine comma-decimal figure quoted verbatim in the news
    # (8,3 Mrd) must still be SOURCED when the report cites it faithfully.
    fs = _fs(news=[_news("Umsatz stieg auf 8,3 Mrd Euro im Quartal")])
    res = audit("Der Umsatz stieg laut Bericht auf 8,3 Mrd Euro.", fs)
    by = {c.token: c for c in res.claims if c.kind == "number"}
    assert "8,3 Mrd" in by, list(by)
    assert by["8,3 Mrd"].matched is True
    assert by["8,3 Mrd"].verdict == VALID


# ===========================================================================
# ADR-RPT4-07 — section-heading numbers are STRUCTURAL by position, and the
# Investment-Board vote vocabulary is not a ticker (RPT-3d, "## 11.").
# ===========================================================================
def test_section_heading_number_is_structural_by_position_not_by_coincidence():
    """A heading number must trace to its POSITION, never to a numeric accident.

    Regression: "## 10." used to survive only because the model card happens to
    carry an unrelated cost_bps=10, and "## 11." struck outright — the same
    latent class as a bare "MA200" auditing only when the price sits near 200.
    """
    fs = _fs()
    for n in (7, 10, 11, 12):
        res = audit(f"## {n}. Ein Abschnitt\n\nNur Fliesstext.", fs)
        assert res.integrity_ok, (n, list(res.flags))
        heading = [c for c in res.claims if c.kind == "number" and c.value == float(n)]
        assert heading, f"heading {n} produced no claim"
        assert heading[0].verdict == STRUCTURAL
        assert heading[0].note == "section heading number"


def test_heading_pass_does_not_launder_a_body_number():
    """The heading pass is anchored to the line start + heading marker, so an
    unsourced number in the body can never borrow it."""
    fs = _fs()
    res = audit("## 11. Kursziel\n\nDas Kursziel liegt bei 11111 USD.", fs)
    assert res.integrity_ok is False, list(res.flags)
    assert any(c.value == 11111.0 and not c.matched for c in res.claims)
