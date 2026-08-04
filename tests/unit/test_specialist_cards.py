# tests/unit/test_specialist_cards.py
# RPAR Epic #1262, Task T2 (#1264) - deterministic, LLM-free card fields.
#
# Two test surfaces:
#   1. The pure builders in core/specialist/cards.py (shape / purity / thresholds).
#   2. The flag-gated wiring in StockSpecialistAgent._build_report:
#        * SPECIALIST_CARDS_ENABLED=False (default) -> V0 defaults, DTO byte-identical
#          (dormancy guarantee - Epic-#1262 Gherkin "all RPAR flags OFF -> byte-identical").
#        * Flag ON vs OFF -> NO decision delta (FINDINGS NEWS-8): sentiment_score /
#          recommendation / confidence / reasons / escalate / escalate_reason identical;
#          only pros/cons/summary/headlines differ.
#
# No network / LLM / GPU - fully deterministic.

import copy
import types
from unittest.mock import patch

from core.engine.api_routes import _serialize_specialist_report
from core.specialist.cards import build_pros_cons, build_summary, select_headlines
from core.stock_specialist import StockSpecialistAgent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _agent():
    return StockSpecialistAgent("AAPL", gemini_api_key="x")


def _gathered(**overrides):
    base = {
        "insider_trades": [],
        "material_events": [],
        "activist_stakes": [],
        "political_trades": [],
        "recent_headlines": [],
        "wiki_spike": False,
        "wiki_views_7d": 0,
        "reddit_mentions_24h": 0,
        "reddit_sentiment": "neutral",
        "short_interest_pct": None,
        "google_trend_score": None,
    }
    base.update(overrides)
    return base


def _cfg(cards_on):
    # _build_report reads BOTH flags off get_config(); keep the blend OFF so the
    # only variable under test is the card flag.
    return types.SimpleNamespace(
        SPECIALIST_CARDS_ENABLED=cards_on,
        ML_SENTIMENT_BLEND_ENABLED=False,
    )


# ---------------------------------------------------------------------------
# 1. build_pros_cons - deterministic mapping from the already-gathered signals
# ---------------------------------------------------------------------------
def test_build_pros_cons_empty_signals_yield_empty():
    pros, cons = build_pros_cons(_gathered())
    assert pros == []
    assert cons == []


def test_build_pros_cons_insider_cluster_is_a_pro():
    pros, cons = build_pros_cons(_gathered(insider_trades=[{}, {}, {}]))
    assert any("insider" in p.lower() for p in pros)
    assert cons == []


def test_build_pros_cons_high_short_interest_is_a_con():
    pros, cons = build_pros_cons(_gathered(short_interest_pct=30.0))
    assert any("short" in c.lower() for c in cons)
    assert pros == []


def test_build_pros_cons_returns_new_list_objects_and_does_not_mutate():
    g = _gathered(insider_trades=[{}, {}, {}], short_interest_pct=30.0)
    before = copy.deepcopy(g)
    pros, cons = build_pros_cons(g)
    # purity: input unchanged
    assert g == before
    # returned lists are fresh objects, not aliases of any gathered list
    assert pros is not g["insider_trades"]
    assert cons is not g["insider_trades"]


# ---------------------------------------------------------------------------
# 2. build_summary - deterministic 1-2 sentence fallback (no LLM)
# ---------------------------------------------------------------------------
def test_build_summary_fires_fallback_when_no_existing_summary():
    out = build_summary(
        news_summary="",
        alt_signals="",
        recommendation="buy",
        sentiment_score=72.0,
        existing_summary="",
    )
    assert isinstance(out, str)
    assert out  # non-empty deterministic fallback


def test_build_summary_preserves_existing_summary_forward_compat():
    existing = "Pre-existing LLM summary."
    out = build_summary(
        news_summary="x",
        alt_signals="y",
        recommendation="hold",
        sentiment_score=50.0,
        existing_summary=existing,
    )
    assert out == existing


# ---------------------------------------------------------------------------
# 3. select_headlines - normalise str -> {"title": str}, cap 8, preserve order
# ---------------------------------------------------------------------------
def test_select_headlines_normalises_strings_to_title_dicts():
    g = _gathered(recent_headlines=["A", "B", "C"])
    out = select_headlines(g)
    assert out == [{"title": "A"}, {"title": "B"}, {"title": "C"}]
    # each element is a Dict with a "title" key
    assert all(isinstance(h, dict) and set(h) == {"title"} for h in out)


def test_select_headlines_caps_at_8():
    g = _gathered(recent_headlines=[f"h{i}" for i in range(12)])
    out = select_headlines(g)
    assert len(out) == 8
    assert out[0] == {"title": "h0"}
    assert out[-1] == {"title": "h7"}


def test_select_headlines_does_not_mutate_input():
    g = _gathered(recent_headlines=["A", "B"])
    before = copy.deepcopy(g)
    out = select_headlines(g)
    assert g == before
    assert out is not g["recent_headlines"]


def test_select_headlines_empty_yields_empty():
    assert select_headlines(_gathered()) == []


# ---------------------------------------------------------------------------
# 4. Flag OFF (default) -> V0 defaults + DTO byte-identical (Papa core requirement)
# ---------------------------------------------------------------------------
def test_cards_flag_off_leaves_v0_defaults_and_byte_identical_dto():
    agent = _agent()
    g = _gathered(
        insider_trades=[{}, {}, {}],
        short_interest_pct=30.0,
        recent_headlines=["A", "B", "C"],
    )
    with patch("core.stock_specialist.get_config", return_value=_cfg(cards_on=False)):
        report = agent._build_report(g, {"text": ""})

    # V0 card-field defaults untouched even though the signals would populate them
    assert report.pros == []
    assert report.cons == []
    assert report.summary == ""
    assert report.headlines == []

    # The DTO (post-T-SER) carries the 7 Group-B keys at their V0 defaults; the
    # serialized headlines (Group-A) stay [] - byte-identical to a flag-OFF run.
    dto = _serialize_specialist_report("AAPL", report)
    assert dto["headlines"] == []
    assert dto["pros"] == []
    assert dto["cons"] == []
    assert dto["summary"] == ""


# ---------------------------------------------------------------------------
# 5. Flag ON vs OFF -> NO decision delta (FINDINGS NEWS-8)
# ---------------------------------------------------------------------------
def test_build_report_card_fields_no_decision_delta():
    agent = _agent()
    g = _gathered(
        insider_trades=[{}, {}, {}],
        short_interest_pct=30.0,
        recent_headlines=["A", "B", "C"],
    )
    before = copy.deepcopy(g)

    with patch("core.stock_specialist.get_config", return_value=_cfg(cards_on=False)):
        off = agent._build_report(copy.deepcopy(g), {"text": ""})
    with patch("core.stock_specialist.get_config", return_value=_cfg(cards_on=True)):
        on = agent._build_report(copy.deepcopy(g), {"text": ""})

    # Decision-relevant fields are byte-identical between flag-OFF and flag-ON.
    assert on.sentiment_score == off.sentiment_score
    assert on.recommendation == off.recommendation
    assert on.confidence == off.confidence
    assert on.reasons == off.reasons
    assert on.escalate == off.escalate
    assert on.escalate_reason == off.escalate_reason

    # Only the card fields differ.
    assert on.pros and not off.pros
    assert on.cons and not off.cons
    assert on.headlines == [{"title": "A"}, {"title": "B"}, {"title": "C"}]
    assert off.headlines == []

    # Input gathered dict is not mutated by _build_report.
    assert g == before


def test_build_report_flag_on_populates_expected_cards():
    agent = _agent()
    g = _gathered(insider_trades=[{}, {}, {}], short_interest_pct=30.0)
    with patch("core.stock_specialist.get_config", return_value=_cfg(cards_on=True)):
        report = agent._build_report(g, {"text": ""})
    assert any("insider" in p.lower() for p in report.pros)
    assert any("short" in c.lower() for c in report.cons)
    assert isinstance(report.summary, str) and report.summary
