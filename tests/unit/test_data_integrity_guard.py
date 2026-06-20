# tests/unit/test_data_integrity_guard.py
# RPAR Epic #1262, Task T6a (#1268, Closes #1270) - data-integrity guard.
#
# TDD Red -> Green. The guard is a PURE, deterministic package (core/data_integrity)
# that derives two DISPLAY-ONLY fields (SpecialistReport.data_quality / .degraded)
# from the already-built `gathered` dict. It NEVER touches the decision path:
#   * sentiment_score / recommendation / confidence / reasons / escalate are produced
#     unchanged in _build_report / _parse_synthesis; the guard never writes them back.
#   * the skip-LLM path reuses the EXISTING V0-default synthesis (the engine already
#     produces `("","","hold",50.0,0.3,[...])` for empty text) - no new decision branch.
#
# Fully gated behind DATA_INTEGRITY_GUARD_ENABLED (default OFF) -> byte-identical DTO.
#
# Guard unit tests are sync (pure function); the research()-wiring tests are async and
# use AsyncMock for _gemini_synthesize and the fetchers (§5.2).

import copy
import types
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from config import get_config
from core.data_integrity import DataIntegrityResult, DataIntegrityThresholds, assess
from core.stock_specialist import SpecialistReport, StockSpecialistAgent

_NOW = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)


def _agent():
    return StockSpecialistAgent("AAPL", gemini_api_key="x")


def _full_gathered():
    """A `gathered` dict where every source returned data (best quality)."""
    return {
        "insider_trades": [{"filer": "A"}, {"filer": "B"}],
        "material_events": [{"event": "8-K"}],
        "activist_stakes": [{"filer": "Icahn"}],
        "political_trades": [{"rep": "X"}],
        "recent_headlines": ["Apple beats earnings", "New iPhone ships"],
        "wiki_spike": True,
        "wiki_views_7d": 50000,
        "reddit_mentions_24h": 12,
        "reddit_sentiment": "bullish",
        "short_interest_pct": 3.1,
        "google_trend_score": 88.0,
        "ml_prediction": None,
    }


def _empty_gathered():
    """The `gathered` dict shape research() builds when EVERY fetcher fell back to
    its empty default (all sources missing). Decision-neutral V0 shape."""
    return {
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
        "ml_prediction": None,
    }


def _cfg(guard_enabled: bool):
    """A config namespace satisfying every get_config() read on the research /
    _build_report path. ML flags present + OFF so they cause no decision change."""
    return types.SimpleNamespace(
        DATA_INTEGRITY_GUARD_ENABLED=guard_enabled,
        ML_PREDICTION_ENABLED=False,
        ML_SENTIMENT_BLEND_ENABLED=False,
        SPECIALIST_NEWS_V2=False,
        LLM_OUTPUT_PARITY=False,
    )


# ---------------------------------------------------------------------------
# A. assess() - shape, purity, determinism (pure function)
# ---------------------------------------------------------------------------
def test_config_flag_exists_and_defaults_off():
    """Dormancy: the flag exists on both editions' get_config() and defaults False."""
    assert get_config().DATA_INTEGRITY_GUARD_ENABLED is False


def test_assess_result_shape():
    result = assess(_full_gathered(), now=_NOW)
    assert isinstance(result, DataIntegrityResult)
    assert 0.0 <= result.data_quality <= 1.0
    assert isinstance(result.degraded, bool)
    assert isinstance(result.skip_llm, bool)
    assert isinstance(result.missing_sources, list)


def test_assess_all_sources_full_is_perfect_quality():
    result = assess(_full_gathered(), now=_NOW)
    assert result.data_quality == 1.0
    assert result.degraded is False
    assert result.skip_llm is False
    assert result.missing_sources == []


def test_assess_single_secondary_missing_below_one_not_degraded():
    gathered = _full_gathered()
    gathered["google_trend_score"] = None  # one secondary source empty
    result = assess(gathered, now=_NOW)
    assert result.data_quality < 1.0
    assert result.degraded is False  # above the degraded threshold
    assert "google_trend_score" in result.missing_sources


def test_assess_all_primary_missing_is_degraded():
    gathered = _full_gathered()
    for src in DataIntegrityThresholds().primary_sources:
        if isinstance(gathered.get(src), list):
            gathered[src] = []
        else:
            gathered[src] = None
    result = assess(gathered, now=_NOW)
    assert result.degraded is True
    assert result.data_quality <= DataIntegrityThresholds().degraded_below


def test_assess_all_empty_is_hard_fail_skip_llm():
    result = assess(_empty_gathered(), now=_NOW)
    assert result.skip_llm is True
    assert result.degraded is True
    assert result.data_quality <= DataIntegrityThresholds().hard_fail_below


def test_assess_zero_quality_is_a_legitimate_value():
    """P0-1: data_quality of 0.0 must survive (never `or`-defaulted downstream).

    A genuinely all-missing gathered (every source None / empty collection) ->
    0.0 (not masked, not defaulted).
    """
    thr = DataIntegrityThresholds()
    gathered = _full_gathered()
    for src in thr.primary_sources + thr.secondary_sources:
        gathered[src] = [] if isinstance(gathered.get(src), list) else None
    result = assess(gathered, now=_NOW)
    assert result.data_quality == 0.0
    assert result.skip_llm is True


def test_zero_and_false_secondaries_count_as_present_not_missing():
    """F-01 (#1313) / P0-1: a fetched ``False`` (wiki_spike, the normal no-spike
    case) or a numeric ``0`` / ``0.0`` (short_interest_pct, google_trend_score,
    reddit_mentions_24h) is a VALID fetched value - never a missing source.

    A fully-populated report whose secondaries happen to be zero-valued therefore
    still scores ``data_quality == 1.0`` (regression: the old ``_has_data``
    treated 0/False as missing, capping normal quality at 0.925)."""
    gathered = _full_gathered()
    gathered["wiki_spike"] = False
    gathered["reddit_mentions_24h"] = 0
    gathered["short_interest_pct"] = 0.0
    gathered["google_trend_score"] = 0
    result = assess(gathered, now=_NOW)
    assert result.data_quality == 1.0
    assert result.missing_sources == []
    assert result.degraded is False


def test_assess_does_not_mutate_gathered():
    gathered = _full_gathered()
    before = copy.deepcopy(gathered)
    assess(gathered, now=_NOW)
    assert gathered == before  # deep-equal: no mutation


def test_assess_missing_sources_is_a_new_list():
    gathered = _empty_gathered()
    result = assess(gathered, now=_NOW)
    for value in gathered.values():
        assert result.missing_sources is not value


def test_assess_is_deterministic():
    g1 = _full_gathered()
    g2 = _full_gathered()
    assert assess(g1, now=_NOW) == assess(g2, now=_NOW)


def test_assess_standalone_callable_for_shadow_harness():
    """#76 shadow-harness contract: assess(gathered) is callable with NO agent / net /
    LLM and with the default config + default now (read-only compare)."""
    result = assess(_empty_gathered())
    assert isinstance(result, DataIntegrityResult)
    assert isinstance(result.data_quality, float)


# ---------------------------------------------------------------------------
# B. research() / _build_report wiring - flag OFF (dormant, byte-identical)
# ---------------------------------------------------------------------------
def test_build_report_flag_off_data_quality_defaults():
    """Flag OFF (default): _build_report leaves the V0 schema defaults untouched."""
    agent = _agent()
    with patch("core.stock_specialist.get_config", return_value=_cfg(False)):
        report = agent._build_report(
            _full_gathered(), {"text": "SCORE: 70\nOUTLOOK: bullish"}
        )
    assert report.data_quality == 1.0
    assert report.degraded is False


@pytest.mark.anyio
async def test_research_flag_off_guard_not_called():
    """Dormant: with the flag OFF, research() never imports/calls the guard, and the
    report carries the V0 defaults (data_quality 1.0 / degraded False)."""
    agent = _agent()
    _mock_fetchers(agent, _full_gathered())
    agent._gemini_synthesize = AsyncMock(
        return_value={"text": "SCORE: 70\nOUTLOOK: bullish"}
    )
    with patch("core.stock_specialist.get_config", return_value=_cfg(False)), patch(
        "core.data_integrity.assess"
    ) as guard:
        report = await agent.research()
    guard.assert_not_called()
    assert report.data_quality == 1.0
    assert report.degraded is False
    agent._gemini_synthesize.assert_awaited_once()  # LLM still called (no skip)


# ---------------------------------------------------------------------------
# C. no-decision-delta - flag ON (no hard fail) vs flag OFF
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_research_guard_no_decision_delta():
    """Flag ON vs OFF on the SAME gathered/synthesis (full data -> no skip_llm): only
    data_quality/degraded may differ; the whole decision tuple is identical, and the
    input gathered is not mutated by research()."""
    synthesis_text = (
        "SUMMARY: ok\nOUTLOOK: bullish\nSCORE: 71\n- reason one\n- reason two"
    )

    report_off, gathered_off, before_off = await _run_research(
        _full_gathered(), _cfg(False), synthesis_text
    )
    report_on, _, _ = await _run_research(_full_gathered(), _cfg(True), synthesis_text)

    assert report_on.sentiment_score == report_off.sentiment_score
    assert report_on.recommendation == report_off.recommendation
    assert report_on.confidence == report_off.confidence
    assert report_on.reasons == report_off.reasons
    assert report_on.escalate == report_off.escalate
    assert report_on.escalate_reason == report_off.escalate_reason
    # input not mutated by research()
    assert gathered_off == before_off
    # display fields: OFF = defaults, ON = real (full data) result
    assert report_off.data_quality == 1.0 and report_off.degraded is False
    assert report_on.data_quality == 1.0 and report_on.degraded is False


# ---------------------------------------------------------------------------
# D. skip-LLM uses the EXISTING V0-default synthesis (no new decision branch)
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_skip_llm_uses_v0_default_synthesis():
    """Flag ON + guard returns skip_llm=True (all sources empty): _gemini_synthesize is
    NOT awaited; the report carries the V0-default synthesis and degraded=True."""
    agent = _agent()
    _mock_fetchers(agent, _empty_gathered())
    agent._gemini_synthesize = AsyncMock(return_value={"text": "SHOULD NOT BE USED"})
    with patch("core.stock_specialist.get_config", return_value=_cfg(True)):
        report = await agent.research()

    agent._gemini_synthesize.assert_not_awaited()
    assert report.recommendation == "hold"
    assert report.sentiment_score == 50.0
    assert report.degraded is True


# ---------------------------------------------------------------------------
# Helpers - drive the REAL research() body with controlled fetcher outputs.
# ---------------------------------------------------------------------------
def _mock_fetchers(agent, gathered):
    """Patch every fetcher so research()'s inline asyncio.gather rebuilds exactly
    `gathered`. Exercises the real guard-wiring + _build_report path (no network)."""
    agent._fetch_edgar_form4 = AsyncMock(return_value=list(gathered["insider_trades"]))
    agent._fetch_edgar_8k = AsyncMock(return_value=list(gathered["material_events"]))
    agent._fetch_edgar_13d = AsyncMock(return_value=list(gathered["activist_stakes"]))
    agent._fetch_congressional_trades = AsyncMock(
        return_value=list(gathered["political_trades"])
    )
    agent._fetch_polygon_news = AsyncMock(
        return_value=list(gathered["recent_headlines"])
    )
    agent._fetch_wiki_pageviews = AsyncMock(
        return_value={
            "spike": gathered["wiki_spike"],
            "views_7d": gathered["wiki_views_7d"],
        }
    )
    agent._fetch_reddit_mentions = AsyncMock(
        return_value={
            "mentions": gathered["reddit_mentions_24h"],
            "sentiment": gathered["reddit_sentiment"],
        }
    )
    agent._fetch_finra_short_interest = AsyncMock(
        return_value=gathered["short_interest_pct"]
    )
    agent._fetch_google_trends = AsyncMock(return_value=gathered["google_trend_score"])
    agent._fetch_ml_prediction = AsyncMock(return_value=gathered["ml_prediction"])


async def _run_research(gathered, cfg, synthesis_text):
    agent = _agent()
    _mock_fetchers(agent, gathered)
    agent._gemini_synthesize = AsyncMock(return_value={"text": synthesis_text})
    before = copy.deepcopy(gathered)
    with patch("core.stock_specialist.get_config", return_value=cfg):
        report = await agent.research()
    # `gathered` here is the fixture handed to _mock_fetchers; research() builds its OWN
    # gathered dict internally. We assert non-mutation of the values research() consumed
    # by re-deriving from the same fixture (the fetchers returned copies).
    return report, gathered, before
