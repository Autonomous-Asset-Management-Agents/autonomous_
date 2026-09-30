# tests/unit/test_report_data_coverage_honesty.py
# #2587 — Data-Coverage-Karte: Report-Ehrlichkeit.
#
# THE LIVE DEFECTS (verified on 0.3.9-rc1 / 0.3.10-beta, 2026-07-30):
#   A. "LSTM ranking — " on the coverage card while the Round-Table verdict on the
#      SAME page shows "rank 91 of 501": the serializer called
#      ``get_store().cross_section(...)`` — a method that exists only on the
#      ``EngineLstmPanelReader`` adapter, NOT on ``LstmPanelStore`` (the store method
#      is ``cross_section_at``). The AttributeError was swallowed by the
#      panel-unavailable except -> honest-looking None triple, every cycle. The
#      Round-Table vote uses the CORRECT call (agents.py ``cross_section_at``), hence
#      the contradiction on one page. Same misuse sat in stock_specialist's
#      model-signal-lines block.
#   B. "Short interest — ": FINRA request -> HTTP 404, silently (non-200 fell through
#      to ``return None`` with NO log; the except logged at DEBUG). House rule:
#      fallback values log at WARNING, never DEBUG/silent.
#   C. "Congress trades 0" / "Reddit mentions 0": both sources are DEAD (Quiver
#      returns 404 without an API key we never had; Reddit blocks the unauthenticated
#      search endpoint with 403), but the card claimed "checked, nothing found".
#      Dead-source honesty: the fetchers signal unavailability, the report carries
#      ``sources_unavailable``, and the serializer emits ``null`` counts (the GUI
#      renders "—") instead of a fabricated 0.
#
# Fully deterministic: no network / LLM / GPU / Redis. Only the real panel store runs.

from __future__ import annotations

import inspect
import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.engine.api_routes as api_routes
from core.report.lstm_panel_store import get_store
from core.stock_specialist import StockSpecialistAgent

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _card_cfg(**over):
    """The get_config() surface _serialize_specialist_report reads (card flag ON)."""
    base = dict(
        REPORT_QUALITY_BADGE_ENABLED=False,
        REPORT_GENERATOR_V2_ENABLED=False,
        SPECIALIST_SYNTHESIS_CARD_ENABLED=True,
        SPECIALIST_FRESHNESS_ENABLED=False,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _stub_report(**over):
    ns = SimpleNamespace(
        symbol="NVDA",
        sentiment_score=61.0,
        recommendation="hold",
        confidence=0.4,
        escalate=False,
        escalate_reason="",
        reasons=[],
        bull_case="",
        bear_case="",
        investment_thesis="",
        synthesis_citations=[],
        synthesis_dropped=[],
        grounding_ok=True,
        vol_band_low_pct=None,
        vol_band_base_pct=None,
        vol_band_high_pct=None,
        vol_band_sigma_pct=None,
        scenario_basis="",
    )
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


def _mock_async_client(response):
    """A ``patch("httpx.AsyncClient")`` stand-in whose ``async with`` yields a client
    with ``get = AsyncMock(...)`` (AsyncMock per house rule — never handwritten)."""
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    cls = MagicMock()
    cls.return_value.__aenter__ = AsyncMock(return_value=client)
    cls.return_value.__aexit__ = AsyncMock(return_value=False)
    return cls, client


@pytest.fixture()
def clean_panel():
    store = get_store()
    store.reset()
    yield store
    store.reset()


# ---------------------------------------------------------------------------
# A. LSTM ranking — coverage card reads the SAME panel the verdict votes on
# ---------------------------------------------------------------------------


class TestLstmRankFromPanel:
    def test_serializer_emits_rank_when_panel_has_symbol(self, clean_panel):
        """Panel seeded exactly like the engine writer (record_cycle) -> the DTO must
        carry the standing the Round-Table vote reads, not a None triple."""
        clean_panel.record_cycle(
            datetime.now(timezone.utc),
            [("NVDA", 2.0), ("AAPL", 1.0), ("MSFT", 0.5)],
        )
        with patch.object(api_routes.config, "get_config", return_value=_card_cfg()):
            dto = api_routes._serialize_specialist_report("NVDA", _stub_report())
        assert dto["lstm_xsec_rank"] == 1
        assert dto["lstm_xsec_universe"] == 3
        assert dto["lstm_xsec_percentile"] == pytest.approx(66.7, abs=0.1)

    def test_serializer_abstains_honestly_on_empty_panel(self, clean_panel):
        with patch.object(api_routes.config, "get_config", return_value=_card_cfg()):
            dto = api_routes._serialize_specialist_report("NVDA", _stub_report())
        assert dto["lstm_xsec_rank"] is None
        assert dto["lstm_xsec_universe"] is None
        assert dto["lstm_xsec_percentile"] is None

    def test_no_store_cross_section_misuse_left(self):
        """``LstmPanelStore`` has ``cross_section_at``; ``cross_section`` lives only on
        the reader adapter. Any ``get_store().cross_section(`` call raises
        AttributeError into a broad except -> silent None triple. Both producers must
        use the store method (the same one agents.py votes on)."""
        import core.stock_specialist as ss

        for mod in (api_routes, ss):
            src = inspect.getsource(mod)
            assert "get_store().cross_section(" not in src, (
                f"{mod.__name__} calls the reader-only .cross_section() on the "
                "store singleton — use .cross_section_at() (panel source of the "
                "Round-Table vote)"
            )


# ---------------------------------------------------------------------------
# B. FINRA short interest — a dead endpoint must WARN, never fail silently
# ---------------------------------------------------------------------------


class TestFinraShortInterestHonesty:
    @pytest.mark.asyncio
    async def test_404_returns_none_and_logs_warning(self, caplog):
        agent = StockSpecialistAgent("AAPL", gemini_api_key="x")
        cls, _client = _mock_async_client(SimpleNamespace(status_code=404))
        with patch("httpx.AsyncClient", cls):
            with caplog.at_level(logging.WARNING, logger="core.stock_specialist"):
                result = await agent._fetch_finra_short_interest()
        assert result is None
        assert any(
            "FINRA" in rec.message and "404" in rec.message
            for rec in caplog.records
            if rec.levelno >= logging.WARNING
        ), "a dead short-interest endpoint must be WARNING-logged, not silent"

    @pytest.mark.asyncio
    async def test_exception_logs_warning_not_debug(self, caplog):
        agent = StockSpecialistAgent("AAPL", gemini_api_key="x")
        cls = MagicMock()
        cls.return_value.__aenter__ = AsyncMock(side_effect=RuntimeError("boom"))
        cls.return_value.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", cls):
            with caplog.at_level(logging.WARNING, logger="core.stock_specialist"):
                result = await agent._fetch_finra_short_interest()
        assert result is None
        assert any(
            "FINRA" in rec.message
            for rec in caplog.records
            if rec.levelno >= logging.WARNING
        ), "fallback values log at WARNING, never DEBUG (house rule)"


# ---------------------------------------------------------------------------
# C. Congress / Reddit — dead source signals unavailability (None), never a fake 0
# ---------------------------------------------------------------------------


class TestDeadSourceSignalling:
    @pytest.mark.asyncio
    async def test_congress_non_200_returns_none_and_warns(self, caplog):
        agent = StockSpecialistAgent("AAPL", gemini_api_key="x")
        cls, _client = _mock_async_client(SimpleNamespace(status_code=404))
        with patch("httpx.AsyncClient", cls):
            with caplog.at_level(logging.WARNING, logger="core.stock_specialist"):
                result = await agent._fetch_congressional_trades()
        assert result is None, "dead source must be None (unavailable), not [] (zero)"
        assert any(
            "ongress" in rec.message and "404" in rec.message
            for rec in caplog.records
            if rec.levelno >= logging.WARNING
        )

    @pytest.mark.asyncio
    async def test_congress_200_empty_list_stays_a_real_zero(self):
        resp = SimpleNamespace(status_code=200, json=lambda: [])
        agent = StockSpecialistAgent("AAPL", gemini_api_key="x")
        cls, _client = _mock_async_client(resp)
        with patch("httpx.AsyncClient", cls):
            result = await agent._fetch_congressional_trades()
        assert result == [], "a checked-and-empty source is a REAL 0, not unavailable"

    @pytest.mark.asyncio
    async def test_reddit_403_returns_none_mentions_and_warns(self, caplog):
        agent = StockSpecialistAgent("AAPL", gemini_api_key="x")
        cls, _client = _mock_async_client(SimpleNamespace(status_code=403))
        with patch("httpx.AsyncClient", cls):
            with caplog.at_level(logging.WARNING, logger="core.stock_specialist"):
                result = await agent._fetch_reddit_mentions()
        assert (
            result["mentions"] is None
        ), "a blocked source must be None (unavailable), not a fake 0"
        assert result["sentiment"] == "neutral"
        assert any(
            "eddit" in rec.message and "403" in rec.message
            for rec in caplog.records
            if rec.levelno >= logging.WARNING
        )


# ---------------------------------------------------------------------------
# C (cont.) — the report carries the unavailability; the DTO nulls the counts
# ---------------------------------------------------------------------------


def _minimal_gathered(**over):
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
    base.update(over)
    return base


class TestReportAndSerializerHonesty:
    def test_build_report_carries_sources_unavailable(self):
        agent = StockSpecialistAgent("AAPL", gemini_api_key="x")
        gathered = _minimal_gathered(
            sources_unavailable=["congressional_trades", "reddit_mentions"]
        )
        report = agent._build_report(gathered, {"text": ""})
        assert report.sources_unavailable == [
            "congressional_trades",
            "reddit_mentions",
        ]

    def test_build_report_default_is_all_sources_available(self):
        agent = StockSpecialistAgent("AAPL", gemini_api_key="x")
        report = agent._build_report(_minimal_gathered(), {"text": ""})
        assert report.sources_unavailable == []

    def test_serializer_nulls_counts_for_unavailable_sources(self):
        r = _stub_report(
            political_trades=[],
            reddit_mentions=0,
            sources_unavailable=["congressional_trades", "reddit_mentions"],
        )
        cfg = _card_cfg(SPECIALIST_SYNTHESIS_CARD_ENABLED=False)
        with patch.object(api_routes.config, "get_config", return_value=cfg):
            dto = api_routes._serialize_specialist_report("NVDA", r)
        assert dto["political_trades_count"] is None
        assert dto["reddit_mentions"] is None

    def test_serializer_keeps_real_zero_counts_when_sources_ok(self):
        r = _stub_report(political_trades=[], reddit_mentions=0, sources_unavailable=[])
        cfg = _card_cfg(SPECIALIST_SYNTHESIS_CARD_ENABLED=False)
        with patch.object(api_routes.config, "get_config", return_value=cfg):
            dto = api_routes._serialize_specialist_report("NVDA", r)
        assert dto["political_trades_count"] == 0
        assert dto["reddit_mentions"] == 0

    def test_serializer_legacy_report_without_field_keeps_ints(self):
        """Reports built before this change (no sources_unavailable attr) keep the
        historical int counts — additive-only, byte-identical for old inputs."""
        r = _stub_report(political_trades=[{"politician": "X"}], reddit_mentions=3)
        cfg = _card_cfg(SPECIALIST_SYNTHESIS_CARD_ENABLED=False)
        with patch.object(api_routes.config, "get_config", return_value=cfg):
            dto = api_routes._serialize_specialist_report("NVDA", r)
        assert dto["political_trades_count"] == 1
        assert dto["reddit_mentions"] == 3
