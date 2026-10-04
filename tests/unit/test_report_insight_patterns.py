# tests/unit/test_report_insight_patterns.py
# RPT-7 (Epic #1998) — the deterministic INSIGHT-PATTERN layer: the code detects
# the deep analytical patterns in a FactSet, DERIVES the logical implication
# itself (never the LLM), and emits a grounded, provenanced Insight. The narrator
# only phrases the implication linguistically.
#
# Test surfaces (TDD, red first):
#   1. GOLDEN-FILE GATE (no LLM): detect_insights(NVDA-FactSet) reproduces the 3
#      NVDA patterns (all condition_met) with the correct grounding numbers
#      (PEG 0.62, score-trend 1.55->4.10, pullback -8.4%, xsec 95.7) and the code
#      derived implications (asymmetrisch / Multiple-nicht-gestreckt / Rauschen-vs-Signal).
#   2. A flat/empty symbol -> 0 insights (honest: never forced).
#   3. Each detector computes its condition from the FactSet and fires ONLY when
#      met (divergence needs fundamentals-intact; valuation needs PEG<1 + low debt;
#      horizon needs long-structural + short-catalyst news + a short model horizon).
#   4. Every grounding field carries a real FactSet Fact.src (auditable provenance).
#   5. The implication is CODE-derived and deterministic (same FactSet -> identical
#      Insight; the module touches no LLM at all).
#   6. render integration: the ## Insights section appears when insights fire (with
#      grounding + implication + caveat), is absent when none fire, the narration
#      seam phrases the implication, and the composed report audits CLEAN.
#   7. BORA: the detector is pure core — no LLM / Redis / SQLite / DEPLOYMENT_MODE.
#
# Fully deterministic: no network / LLM / GPU / pandas.

import json
import os
from datetime import date, timedelta

from core.report import detect_insights as detect_from_pkg
from core.report.auditor import audit
from core.report.fact_set import InMemoryDataContext, NewsItem, build_fact_set
from core.report.insight_patterns import Insight, detect_insights
from core.report.render import render

# ---------------------------------------------------------------------------
# Fixtures — the SAME committed real NVDA slices RPT-1/RPT-3/RPT-5 use.
# ---------------------------------------------------------------------------
_FX = os.path.join(os.path.dirname(__file__), "..", "fixtures", "report")
_CARD = os.path.join(
    os.path.dirname(__file__), "..", "..", "core", "report", "model_card.json"
)
AS_OF = date(2026, 5, 1)

# FY2026 fundamentals — mirrors the RPT-3/RPT-4 golden fixture (P/E 40.5 on
# +65.5% growth -> trailing PEG 0.62; debt/equity 0.31 -> low-debt balance).
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


def _fs_srcs(fs):
    """The set of every Fact.src on a FactSet (the legitimate provenance pool)."""
    from dataclasses import fields

    srcs = set()
    for f in fields(fs):
        val = getattr(fs, f.name)
        src = getattr(val, "src", None)
        if src:
            srcs.add(src)
    return srcs


# A synthetic symbol with NO pattern: flat score, flat price, no news, no
# fundamentals -> the honest zero-insight case.
def _flat_fs():
    days = [date(2026, 4, d) for d in (20, 21, 22, 23, 24)]
    ctx = InMemoryDataContext(
        bars=[(d, 100.0) for d in days],
        lstm_series=[(d, 1.0) for d in days],
        lstm_cross_section={"FLAT": 1.0, "OTHER": 2.0},
        model_card=_load_card(),
        fundamentals=None,
        news=[],
        alt_signals={},
    )
    return build_fact_set("FLAT", AS_OF, ctx)


# A symbol whose news has NOTHING to do with chips: a grocer. Its headlines carry
# a structural competition item AND a results item, so horizon_separation fires —
# but nothing in this FactSet could ever source "Cerebras", "OpenAI", "Burry" or a
# custom-silicon thesis. This is the fixture the NVDA-only golden gate never had:
# with the golden fixture the hard-coded NVDA story happens to be TRUE, which is
# exactly why a canned NVDA narrative survived every test in this file.
_GROCER_NEWS = [
    NewsItem(
        title="Kroger Loses Market Share to Rivals as Discount Grocers Expand",
        published_utc="2026-04-22T12:00:00+00:00",
        publisher="Reuters",
        url="https://example.com/kr-market-share",
    ),
    NewsItem(
        title="Kroger Sets Conference Call for Fourth-Quarter Financial Results",
        published_utc="2026-04-28T12:00:00+00:00",
        publisher="Business Wire",
        url="https://example.com/kr-results",
    ),
]

# Entities / claims that exist only in NVDA's story. None is sourceable for a
# grocer; "Ergebnis-Termin" is not sourceable for ANY symbol (no earnings field).
_NVDA_GHOSTS = (
    "Cerebras",
    "OpenAI",
    "Burry",
    "Custom-Silicon",
    "Custom-Chip",
    "Chip-Breite",
)


def _grocer_fs():
    days = [date(2026, 3, 1) + timedelta(days=i) for i in range(60)]
    closes = [50.0 + (i % 7) * 0.5 for i in range(60)]
    ctx = InMemoryDataContext(
        bars=list(zip(days, closes)),
        lstm_series=[(d, 1.0) for d in days],
        lstm_cross_section={"KR": 1.0, "OTHER": 2.0},
        model_card=_load_card(),
        fundamentals=None,
        news=_GROCER_NEWS,
        alt_signals={},
    )
    return build_fact_set("KR", AS_OF, ctx)


# ---------------------------------------------------------------------------
# 0. NO FABRICATION — the insight layer may not tell one symbol's story for another.
# ---------------------------------------------------------------------------
def test_grocer_report_never_fabricates_nvidias_story():
    # THE regression: horizon_separation fired on ANY structural+catalyst news mix
    # and then printed a canned NVDA narrative — Cerebras, custom silicon, Burry,
    # OpenAI — with fs.news_items.src pinned next to it as "evidence".
    fs = _grocer_fs()
    md = render(fs)
    assert "## 9. Insights" in md, "the horizon pattern should fire on this news mix"
    section = md.split("## 9. Insights")[1]
    for ghost in _NVDA_GHOSTS:
        assert ghost not in section, f"{ghost!r} fabricated into a grocer's report"


def test_no_detector_claims_an_earnings_date_for_any_symbol():
    # fact_set.FactSet carries NO earnings/event field (verify: no such attribute),
    # so a dated results claim is fabrication for EVERY symbol, every time.
    assert not [f for f in ("earnings", "event", "earnings_date") if hasattr(_fs(), f)]
    for fs in (_fs(), _grocer_fs()):
        for ins in detect_insights(fs):
            blob = " ".join(
                [ins.implication, ins.caveat]
                + [g.value for g in ins.grounding.values()]
            )
            assert "Ergebnis-Termin" not in blob, f"{ins.pattern_name} invents a date"
            assert (
                "Ergebnis-Ausgang" not in blob
            ), f"{ins.pattern_name} invents an event"


def test_news_derived_grounding_quotes_the_real_headlines():
    # The evidence text must COME from the FactSet, not be stood next to it. Any
    # grounding citing news_items.src must quote a PIT headline verbatim.
    for fs in (_fs(), _grocer_fs()):
        titles = [n.title for n in fs.news_items.value]
        for ins in detect_insights(fs):
            for key, g in ins.grounding.items():
                if g.src != fs.news_items.src:
                    continue
                assert any(
                    t in g.value for t in titles
                ), f"{ins.pattern_name}.{key} cites news but quotes no headline: {g.value!r}"


def test_horizon_grounding_tracks_the_actual_news_not_a_constant():
    # Different news -> different evidence text. A canned string cannot do this.
    nvda = {i.pattern_name: i for i in detect_insights(_fs())}
    grocer = {i.pattern_name: i for i in detect_insights(_grocer_fs())}
    n_g = nvda["horizon_separation_pattern"].grounding["structural_long"].value
    g_g = grocer["horizon_separation_pattern"].grounding["structural_long"].value
    assert n_g != g_g
    assert "Kroger" in g_g and "Kroger" not in n_g


# ---------------------------------------------------------------------------
# 1. GOLDEN-FILE GATE — the 3 NVDA patterns, all met, correct grounding numbers.
# ---------------------------------------------------------------------------
def test_golden_three_nvda_patterns_all_fire():
    # The three per-symbol news/fundamentals detectors still lead, in order. The
    # RPT-7 COVERAGE detectors (trend/vol/rank/own, added so quiet symbols are not
    # empty) also fire on NVDA where their pattern truly holds: NVDA's price is in a
    # clean regime, its score trajectory has a clear direction, and its score sits at
    # an own-history extreme — but its 20d/63d vol sits in the neutral band, so
    # vol_regime honestly does NOT fire here.
    insights = detect_insights(_fs())
    names = [i.pattern_name for i in insights]
    assert names[:3] == [
        "divergence_pattern",
        "valuation_vs_growth_pattern",
        "horizon_separation_pattern",
    ]
    assert names == [
        "divergence_pattern",
        "valuation_vs_growth_pattern",
        "horizon_separation_pattern",
        "trend_regime_pattern",
        "rank_momentum_pattern",
        "own_history_extreme_pattern",
    ]
    assert "vol_regime_pattern" not in names  # neutral vol band -> silent, not forced
    assert all(i.condition_met for i in insights)


def test_golden_grounding_numbers_are_reproduced():
    by_name = {i.pattern_name: i for i in detect_insights(_fs())}

    # divergence: rising score trend, -8.4% pullback, top-quintile 95.7 percentile
    div = " ".join(g.value for g in by_name["divergence_pattern"].grounding.values())
    assert "1.55" in div and "4.10" in div  # score trend endpoints
    assert "-8.4" in div  # pullback pct
    assert "95.7" in div  # cross-sectional percentile

    # valuation: PEG 0.62 on the low-debt balance (0.31)
    val = " ".join(
        g.value for g in by_name["valuation_vs_growth_pattern"].grounding.values()
    )
    assert "0.62" in val  # trailing PEG = P/E 40.5 / +65.5%
    assert "40.5" in val and "65.5" in val
    assert "0.31" in val  # debt/equity

    # horizon: elevated vol 39.1 vs 34.9, ~5d model horizon
    hor = " ".join(
        g.value for g in by_name["horizon_separation_pattern"].grounding.values()
    )
    assert "39.1" in hor and "34.9" in hor
    assert "5" in hor


def test_golden_implications_are_the_code_derived_conclusions():
    by_name = {i.pattern_name: i for i in detect_insights(_fs())}
    # ⚠ This used to assert "asymmetrisch" — from "das macht das Risiko in den
    # Ergebnis-Termin nach dem Stichtag eher asymmetrisch". The asymmetry was hung
    # on an earnings date the FactSet cannot hold, so the clause is gone and the
    # conclusion is now only what the three fired conditions support.
    div = by_name["divergence_pattern"].implication.lower()
    # sentence 1 (always, from the three numbers) is the positioning read-across;
    # NVDA's real news carries sentiment headlines, so sentence 2 (conditional) adds
    # the quoted "stimmungsgetrieben" half.
    assert "positionierungsgetrieben" in div
    assert "stimmungsgetrieben" in div
    assert "gestreckt" in by_name["valuation_vs_growth_pattern"].implication.lower()
    hor = by_name["horizon_separation_pattern"].implication.lower()
    assert "rauschen" in hor and "signal" in hor


# ---------------------------------------------------------------------------
# 2. A flat / empty symbol -> 0 insights (honest, never forced).
# ---------------------------------------------------------------------------
def test_flat_symbol_yields_zero_insights():
    assert detect_insights(_flat_fs()) == []


def test_detect_insights_is_exported_from_the_package():
    assert detect_from_pkg is detect_insights


# ---------------------------------------------------------------------------
# 3. Each detector fires ONLY when its condition is met.
# ---------------------------------------------------------------------------
def test_divergence_needs_intact_fundamentals():
    # No fundamentals -> the code cannot confirm "fundamentals intact" -> the
    # divergence insight honestly does NOT fire (score-up / price-down alone is
    # not the pattern). Horizon still fires from the news.
    names = [i.pattern_name for i in detect_insights(_fs(fundamentals=None))]
    assert "divergence_pattern" not in names
    assert "valuation_vs_growth_pattern" not in names
    assert "horizon_separation_pattern" in names


def test_valuation_fires_once_pe_is_wired_from_the_as_of_close():
    # PRODUCTION REALITY, end to end: the fundamentals feed emits the report schema
    # with pe=None (the cached reader is built before any per-symbol close exists),
    # yet revenue_yoy_pct and debt_to_equity ARE present. Real committed NVDA filing.
    from core.report.financials_feed import derive_fundamentals, to_report_fundamentals

    results = _load_json("nvda_financials_vx.json")["results"]
    prod_fund = to_report_fundamentals(
        derive_fundamentals(results, AS_OF.isoformat(), close=None)
    )
    # the exact gap this fix closes: pe missing, the two other inputs present
    assert prod_fund["pe"] is None
    assert prod_fund["eps_diluted"] and prod_fund["revenue_yoy_pct"]
    assert prod_fund["debt_to_equity"] is not None

    # build_fact_set completes pe from the as-of close (fixture bars end 198.45) ->
    # the valuation insight, invisible in production, now fires with real numbers.
    names = [i.pattern_name for i in detect_insights(_fs(fundamentals=prod_fund))]
    assert "valuation_vs_growth_pattern" in names


def test_valuation_stays_silent_when_eps_is_missing_no_fabrication():
    # A symbol whose filing carries no EPS -> pe cannot be formed -> the detector
    # must NOT fire (anti-fabrication: no pe=0, no invented multiple).
    from core.report.financials_feed import derive_fundamentals, to_report_fundamentals

    results = _load_json("nvda_financials_vx.json")["results"]
    prod_fund = dict(
        to_report_fundamentals(derive_fundamentals(results, AS_OF.isoformat()))
    )
    prod_fund["eps_diluted"] = None
    fs = _fs(fundamentals=prod_fund)
    assert fs.fundamentals.value.get("pe") is None
    names = [i.pattern_name for i in detect_insights(fs)]
    assert "valuation_vs_growth_pattern" not in names


def test_valuation_does_not_fire_when_multiple_is_stretched():
    fund = dict(FUND_FY2026)
    fund["pe"] = 200.0  # PEG = 200 / 65.5 = 3.05 -> stretched, no fire
    names = [i.pattern_name for i in detect_insights(_fs(fundamentals=fund))]
    assert "valuation_vs_growth_pattern" not in names


def test_valuation_does_not_fire_on_a_levered_balance_sheet():
    fund = dict(FUND_FY2026)
    fund["debt_to_equity"] = 2.5  # highly levered -> no fire even with PEG<1
    names = [i.pattern_name for i in detect_insights(_fs(fundamentals=fund))]
    assert "valuation_vs_growth_pattern" not in names


def test_horizon_does_not_fire_without_structural_and_catalyst_news():
    # Only a single structural headline, no short catalyst -> no separation.
    only_structural = [
        NewsItem(
            title="Cerebras and Other Nvidia Rivals Just Made Key Moves.",
            published_utc="2026-04-21T10:10:00+00:00",
            publisher="The Motley Fool",
            url="https://example.com/cerebras",
        )
    ]
    names = [i.pattern_name for i in detect_insights(_fs(news=only_structural))]
    assert "horizon_separation_pattern" not in names


# ---------------------------------------------------------------------------
# 4. Every grounding field carries a real FactSet Fact.src (auditable).
# ---------------------------------------------------------------------------
def test_every_grounding_field_is_sourced_from_the_factset():
    fs = _fs()
    valid_srcs = _fs_srcs(fs)
    for ins in detect_insights(fs):
        assert ins.grounding, f"{ins.pattern_name} has empty grounding"
        for key, g in ins.grounding.items():
            assert g.value, f"{ins.pattern_name}.{key} has empty value"
            assert (
                g.src in valid_srcs
            ), f"{ins.pattern_name}.{key} src {g.src!r} is not a FactSet provenance"


def test_insight_is_a_value_object_with_the_documented_shape():
    ins = detect_insights(_fs())[0]
    assert isinstance(ins, Insight)
    assert ins.pattern_name and ins.implication and ins.caveat
    assert ins.narration_slot  # the seam a narrator may fill
    # frozen -> attribute assignment must raise
    try:
        ins.pattern_name = "x"
        raised = False
    except Exception:
        raised = True
    assert raised


# ---------------------------------------------------------------------------
# 5. The implication is CODE-derived and deterministic.
# ---------------------------------------------------------------------------
def test_deterministic_same_factset_same_insights():
    fs = _fs()
    assert detect_insights(fs) == detect_insights(fs)


def test_implication_tracks_the_data_not_a_constant():
    # A different pullback magnitude must change the divergence implication text
    # (proof the conclusion is COMPUTED from the FactSet, not a canned string).
    base = detect_insights(_fs())[0].implication
    # shrink the recent pullback: replace the last 5 closes with a milder dip
    milder = _fs(
        bars=[
            (date.fromisoformat(d), float(c)) for d, c in _load_json("nvda_bars.json")
        ][:-1]
        + [(date(2026, 5, 1), 214.0)]  # tiny -1.2% dip instead of -8.4%
    )
    div = [i for i in detect_insights(milder) if i.pattern_name == "divergence_pattern"]
    assert div, "divergence should still fire on a mild dip"
    assert div[0].implication != base  # the number changed -> the text changed


# ---------------------------------------------------------------------------
# 6. render integration.
# ---------------------------------------------------------------------------
def test_render_shows_insights_section_when_patterns_fire():
    md = render(_fs())
    # insights are now §9 (the body was renumbered in the RPT-3c redesign)
    assert "## 9. Insights" in md
    ins = md.split("## 9. Insights")[1]
    # grounding (Belege) + code-derived implication (inline prose) + caveat
    # (Einordnung) all present
    assert "Belege" in ins
    assert "Einordnung" in ins
    assert "positionierungsgetrieben" in md.lower()  # was "asymmetrisch"
    assert "0.62" in md  # the derived PEG is surfaced with its source
    # OVERSELL GUARD: the divergence caveat no longer injects the model's
    # aggregate net-Sharpe track record — those numbers describe the method over
    # the whole universe, not THIS insight.
    assert "0.758" not in ins
    assert "0.944" not in ins


def test_render_omits_insights_section_when_none_fire():
    md = render(_flat_fs())
    assert "## 9. Insights" not in md


def test_rendered_report_with_insights_audits_clean():
    # THE moat property: the deterministic Insights section invents nothing —
    # every number/entity/date/src traces to the FactSet, so audit finds 0 strikes.
    fs = _fs()
    res = audit(render(fs), fs)
    assert res.integrity_ok, res.summary()
    assert list(res.flags) == []


def test_rendered_report_without_fundamentals_also_audits_clean():
    fs = _fs(fundamentals=None)  # only the horizon insight fires
    res = audit(render(fs), fs)
    assert res.integrity_ok, res.summary()


def test_narration_seam_phrases_the_implication():
    fs = _fs()
    slot = detect_insights(fs)[0].narration_slot
    prose = "Der Rueckgang ist positionsgetrieben, nicht fundamental."
    md = render(fs, narration={slot: prose})
    assert prose in md
    ins = md.split("## 9. Insights")[1]
    # the deterministic implication text was replaced by the narrated prose
    assert "positionierungsgetrieben" not in ins
    # grounding + caveat (the audit anchors) still render deterministically
    assert "Belege" in ins
    # and the composed report is still clean (number/entity-free prose)
    assert audit(md, fs).integrity_ok


# ---------------------------------------------------------------------------
# 7. BORA — pure core: no LLM / Redis / SQLite / DEPLOYMENT_MODE in real code.
# ---------------------------------------------------------------------------
def _executable_code_tokens(path):
    import tokenize

    names = []
    with open(path, encoding="utf-8") as f:
        for tok in tokenize.generate_tokens(f.readline):
            kind = tokenize.tok_name[tok.type]
            if kind in ("COMMENT", "STRING") or kind.startswith("FSTRING"):
                continue
            names.append(tok.string)
    return " ".join(names).lower()


def test_module_is_bora_clean_no_llm_no_infra():
    import core.report.insight_patterns as mod

    code = _executable_code_tokens(mod.__file__)
    for forbidden in (
        "ollama",
        "gemini",
        "openai",
        "anthropic",
        "get_llm",
        "generate_content",
        "redis",
        "sqlite",
        "deployment_mode",
        "k_service",
        "requests",
        "httpx",
    ):
        assert forbidden not in code, f"BORA violation / LLM leak: {forbidden!r}"
