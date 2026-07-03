# tests/unit/test_stock_specialist.py
# Epic 3.3 — Stock Specialist System: TDD
# Iron Dome Coverage target: ≥40% for core/stock_specialist.py
#                             ≥40% for core/specialist_registry.py
#
# Gherkin (Architect Blueprint):
#   Given: A StockSpecialistAgent with mocked external APIs
#   When:  research() is called
#   Then:  A SpecialistReport with valid fields is returned
#
# Policy: CODING_POLICY.md §11.5 TDD, §1 Compliance-First, §5 KI-Agenten-Lifecycle

from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import allure
import pytest

from core.specialist_registry import StockSpecialistRegistry
from core.stock_specialist import SpecialistReport, StockSpecialistAgent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_report(
    symbol: str = "AAPL",
    score: float = 65.0,
    rec: str = "buy",
    escalate: bool = False,
) -> SpecialistReport:
    return SpecialistReport(
        symbol=symbol,
        updated_at=datetime.now(timezone.utc),
        sentiment_score=score,
        recommendation=rec,  # type: ignore[arg-type]
        escalate=escalate,
        escalate_reason="Heavy insider activity" if escalate else "",
    )


# ---------------------------------------------------------------------------
# 1. SpecialistReport dataclass
# ---------------------------------------------------------------------------


@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestSpecialistReport:
    def test_default_values(self):
        r = SpecialistReport(symbol="MSFT")
        assert r.symbol == "MSFT"
        assert r.sentiment_score == 50.0
        assert r.recommendation == "hold"
        assert r.escalate is False
        assert isinstance(r.updated_at, datetime)

    def test_custom_values(self):
        r = _make_report(symbol="NVDA", score=82.0, rec="buy", escalate=True)
        assert r.symbol == "NVDA"
        assert r.sentiment_score == 82.0
        assert r.recommendation == "buy"
        assert r.escalate is True

    def test_lists_are_empty_by_default(self):
        r = SpecialistReport(symbol="TSLA")
        assert r.insider_trades == []
        assert r.political_trades == []
        assert r.reasons == []


# ---------------------------------------------------------------------------
# 2. StockSpecialistAgent._parse_synthesis
# ---------------------------------------------------------------------------


@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestParseSynthesis:
    def setup_method(self):
        self.agent = StockSpecialistAgent("AAPL", "dummy-key")

    def test_parse_bullish_response(self):
        text = (
            "SUMMARY: Strong earnings beat with record revenue.\n"
            "SIGNALS: Multiple insider buys detected.\n"
            "OUTLOOK: bullish\n"
            "SCORE: 78\n"
            "REASONS:\n"
            "- Record earnings growth\n"
            "- Strong institutional buying\n"
        )
        news_summary, alt_signals, rec, score, confidence, reasons = (
            self.agent._parse_synthesis(text)
        )
        assert rec == "buy"
        assert score == 78.0
        assert confidence > 0.3
        assert len(reasons) >= 1

    def test_parse_bearish_response(self):
        text = "SUMMARY: Missed earnings.\nOUTLOOK: bearish\nSCORE: 25\nREASONS:\n- Revenue miss\n"
        _, _, rec, score, _, _ = self.agent._parse_synthesis(text)
        assert rec == "sell"
        assert score == 25.0

    def test_parse_empty_response(self):
        news_summary, alt_signals, rec, score, confidence, reasons = (
            self.agent._parse_synthesis("")
        )
        assert rec == "hold"
        assert score == 50.0
        assert len(reasons) == 1

    def test_score_clamped_0_to_100(self):
        text = "SCORE: 150\nOUTLOOK: bullish\n"
        _, _, _, score, _, _ = self.agent._parse_synthesis(text)
        assert 0.0 <= score <= 100.0

    def test_recommendation_aligned_with_score_when_mismatched(self):
        """Score >= 70 but OUTLOOK neutral → recommendation auto-corrects to buy."""
        text = "OUTLOOK: neutral\nSCORE: 75\n"
        _, _, rec, _, _, _ = self.agent._parse_synthesis(text)
        assert rec == "buy"

    def test_reasons_limited_to_5(self):
        lines = "\n".join(f"- Reason {i}" for i in range(10))
        text = f"SCORE: 60\nOUTLOOK: bullish\nREASONS:\n{lines}\n"
        _, _, _, _, _, reasons = self.agent._parse_synthesis(text)
        assert len(reasons) <= 5  # _build_report clips to 5


# ---------------------------------------------------------------------------
# 3. StockSpecialistAgent._build_report
# ---------------------------------------------------------------------------


@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestBuildReport:
    def setup_method(self):
        self.agent = StockSpecialistAgent("AAPL", "dummy-key")

    def test_build_report_basic(self):
        gathered: dict = {
            "insider_trades": [],
            "material_events": [],
            "activist_stakes": [],
            "political_trades": [],
            "recent_headlines": ["Revenue beats estimates"],
            "wiki_spike": False,
            "wiki_views_7d": 0,
            "reddit_mentions_24h": 0,
            "reddit_sentiment": "neutral",
            "short_interest_pct": None,
            "google_trend_score": None,
        }
        synthesis = {"text": "SUMMARY: Good quarter.\nOUTLOOK: bullish\nSCORE: 70\n"}
        report = self.agent._build_report(gathered, synthesis)
        assert report.symbol == "AAPL"
        assert report.sentiment_score >= 70.0
        assert report.recommendation == "buy"

    def test_cluster_insider_bonus(self):
        """RQ-1 B3 (#1523): filing COUNT no longer adds a bonus -- score stays at base."""
        gathered: dict = {
            "insider_trades": [
                {"filed": "2026-01-01", "filer": "CEO", "form": "Form 4"}
            ]
            * 3,
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
        synthesis = {"text": "SCORE: 50\nOUTLOOK: neutral\n"}
        # RQ-1 B3 (#1523): the +4 count bonus is REMOVED -> score stays at the LLM base.
        report = self.agent._build_report(gathered, synthesis)
        assert report.sentiment_score == 50.0  # no count-based inflation

    def test_activist_escalation(self):
        """Activist 13D filing → escalate=True."""
        gathered: dict = {
            "insider_trades": [],
            "material_events": [],
            "activist_stakes": [
                {"filed": "2026-01-01", "filer": "Icahn Capital", "form": "13D"}
            ],
            "political_trades": [],
            "recent_headlines": [],
            "wiki_spike": False,
            "wiki_views_7d": 0,
            "reddit_mentions_24h": 0,
            "reddit_sentiment": "neutral",
            "short_interest_pct": None,
            "google_trend_score": None,
        }
        synthesis = {"text": "SCORE: 60\nOUTLOOK: bullish\n"}
        report = self.agent._build_report(gathered, synthesis)
        assert report.escalate is True
        assert "Icahn Capital" in report.escalate_reason

    def test_high_short_interest_lowers_score(self):
        """Short interest > 25% subtracts 5 from score."""
        gathered: dict = {
            "insider_trades": [],
            "material_events": [],
            "activist_stakes": [],
            "political_trades": [],
            "recent_headlines": [],
            "wiki_spike": False,
            "wiki_views_7d": 0,
            "reddit_mentions_24h": 0,
            "reddit_sentiment": "neutral",
            "short_interest_pct": 30.0,
            "google_trend_score": None,
        }
        synthesis = {"text": "SCORE: 55\nOUTLOOK: neutral\n"}
        report = self.agent._build_report(gathered, synthesis)
        assert report.sentiment_score <= 50.0  # 55 - 5


# ---------------------------------------------------------------------------
# 4. StockSpecialistRegistry
# ---------------------------------------------------------------------------


@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestStockSpecialistRegistry:
    def test_init_and_get_status(self):
        reg = StockSpecialistRegistry(["AAPL", "MSFT"], "dummy-key")
        status = reg.get_status()
        assert status["total_symbols"] == 2
        assert status["reports_cached"] == 0
        assert status["running"] is False

    def test_get_report_missing(self):
        reg = StockSpecialistRegistry(["AAPL"], "dummy-key")
        assert reg.get_report("AAPL") is None

    def test_add_and_remove_symbol(self):
        reg = StockSpecialistRegistry(["AAPL"], "dummy-key")
        reg.add_symbol("TSLA")
        assert "TSLA" in reg._symbols
        reg.remove_symbol("TSLA")
        assert "TSLA" not in reg._symbols

    def test_add_duplicate_no_op(self):
        reg = StockSpecialistRegistry(["AAPL"], "dummy-key")
        reg.add_symbol("AAPL")
        assert reg._symbols.count("AAPL") == 1

    def test_update_priority(self):
        reg = StockSpecialistRegistry(["AAPL", "MSFT", "NVDA"], "dummy-key")
        reg.update_priority(["AAPL", "MSFT"])
        assert "AAPL" in reg._high_priority
        assert "MSFT" in reg._high_priority
        assert "NVDA" not in reg._high_priority

    def test_get_all_reports_empty(self):
        reg = StockSpecialistRegistry(["AAPL"], "dummy-key")
        assert reg.get_all_reports() == {}

    def test_get_escalations_empty(self):
        reg = StockSpecialistRegistry(["AAPL"], "dummy-key")
        assert reg.get_escalations() == []

    def test_get_escalations_sorted(self):
        reg = StockSpecialistRegistry(["AAPL", "MSFT"], "dummy-key")
        with reg._lock:
            reg._reports["AAPL"] = _make_report("AAPL", score=85.0, escalate=True)
            reg._reports["MSFT"] = _make_report("MSFT", score=90.0, escalate=True)
        escalated = reg.get_escalations()
        assert escalated[0].symbol == "MSFT"  # Higher score first

    def test_get_top_reports_order(self):
        reg = StockSpecialistRegistry(["AAPL", "MSFT", "NVDA"], "dummy-key")
        with reg._lock:
            reg._reports["AAPL"] = _make_report("AAPL", score=70.0)
            reg._reports["NVDA"] = _make_report("NVDA", score=85.0)
        tops = reg.get_top_reports(["AAPL", "NVDA", "MSFT"])  # MSFT missing
        assert len(tops) == 2  # MSFT skipped
        assert tops[0].symbol == "NVDA"  # Higher score first

    def test_estimated_daily_cost_reasonable(self):
        """100 symbols × 2h cycle = ~$0.07/day (key selling point of Epic 3.3)."""
        reg = StockSpecialistRegistry([f"SYM{i}" for i in range(100)], "dummy-key")
        status = reg.get_status()
        # Cost should be well under $1/day
        assert status["est_daily_cost_usd"] < 1.0

    @patch(
        "core.specialist_registry.StockSpecialistRegistry._refresh_symbol",
        new_callable=AsyncMock,
    )
    def test_start_stop(self, mock_refresh):
        reg = StockSpecialistRegistry(["AAPL"], "dummy-key")
        reg.start()
        assert reg._refresh_thread is not None
        assert reg._refresh_thread.is_alive()
        reg.stop()
        # stop() sets the shutdown event (wakes the thread immediately from its
        # _shutdown.wait() sleep) and joins with a 15s timeout — sufficient on
        # any runner. The thread must be dead by the time stop() returns.
        assert not reg._refresh_thread.is_alive()

    def test_double_start_no_duplicate_thread(self):
        reg = StockSpecialistRegistry(["AAPL"], "dummy-key")
        reg.start()
        first_thread = reg._refresh_thread
        reg.start()  # Second start should be a no-op
        assert reg._refresh_thread is first_thread
        reg.stop()

    def test_next_normal_symbol_cycles(self):
        """_next_normal_symbol() returns each symbol in rotation."""
        reg = StockSpecialistRegistry(["AAPL", "MSFT", "NVDA"], "dummy-key")
        seen = {reg._next_normal_symbol() for _ in range(6)}
        assert seen == {"AAPL", "MSFT", "NVDA"}

    def test_next_normal_symbol_skips_high_priority(self):
        reg = StockSpecialistRegistry(["AAPL", "MSFT", "NVDA"], "dummy-key")
        reg.update_priority(["AAPL"])
        for _ in range(10):
            sym = reg._next_normal_symbol()
            assert sym != "AAPL"


# ---------------------------------------------------------------------------
# 5. SpecialistAlphaAgent integration with Registry
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestSpecialistAlphaAgentIntegration:
    async def test_returns_neutral_without_registry(self):
        """P2-Fix: Kein Registry → weight=0.0 (aus Konsens ausgeschlossen), score=0.5 (neutral)."""
        from core.round_table.agents import (
            SpecialistAlphaAgent,
            set_specialist_registry,
        )

        set_specialist_registry(None)
        agent = SpecialistAlphaAgent()
        state = {
            "symbol": "AAPL",
            "ohlc": {
                "open": 100,
                "high": 105,
                "low": 99,
                "close": 103,
                "volume": 1_000_000,
            },
        }
        result = await agent.vote(state)
        # P2-Fix: Kein Report → wird aus Konsens ausgeschlossen (weight=0.0)
        assert (
            result.weight == 0.0
        ), f"Kein Registry → weight=0.0 erwartet (excluded). Got: {result.weight}"
        assert result.score == 0.5
        assert (
            "excluded" in result.reasoning.lower()
            or "ausgeschlossen" in result.reasoning.lower()
        )

    async def test_returns_score_from_registry(self):
        from core.round_table.agents import (
            SpecialistAlphaAgent,
            set_specialist_registry,
        )

        mock_registry = MagicMock()
        mock_registry.get_report.return_value = _make_report(
            "AAPL", score=80.0, rec="buy"
        )
        set_specialist_registry(mock_registry)

        agent = SpecialistAlphaAgent()
        state = {"symbol": "AAPL", "ohlc": {}}
        result = await agent.vote(state)
        assert result.score > 0.75  # 80/100 + buy nudge
        assert "80" in result.reasoning

        set_specialist_registry(None)  # cleanup

    async def test_escalation_flag_in_reasoning(self):
        from core.round_table.agents import (
            SpecialistAlphaAgent,
            set_specialist_registry,
        )

        mock_registry = MagicMock()
        mock_registry.get_report.return_value = _make_report(
            "NVDA", score=88.0, escalate=True
        )
        set_specialist_registry(mock_registry)

        agent = SpecialistAlphaAgent()
        state = {"symbol": "NVDA", "ohlc": {}}
        result = await agent.vote(state)
        assert "ESCALATED" in result.reasoning

        set_specialist_registry(None)  # cleanup
