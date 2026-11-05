# tests/unit/test_specialist_form4_enrichment.py
"""RQ-1 B3b (#1536): flag-gated Form 4 direction enrichment of the insider fetcher rows.

Default OFF -> a strict no-op (no extra SEC request, row keys unchanged -> the B1 key contract
and BORA parity hold). ON -> each insider row gains a parsed buy/sell direction. (Epic #1516.)
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from core.stock_specialist import StockSpecialistAgent


def _run(coro):
    return asyncio.run(coro)


def _sell_doc():
    return (
        "<nonDerivativeTransaction><transactionCoding>"
        "<transactionCode>S</transactionCode></transactionCoding>"
        "<transactionAmounts><transactionShares><value>1000</value>"
        "</transactionShares></transactionAmounts></nonDerivativeTransaction>"
    )


class TestForm4DirectionEnrichment:
    def setup_method(self):
        self.agent = StockSpecialistAgent("AAPL", "dummy-key")
        self.rows = [{"filed": "2026-06-20", "filer": "CEO", "form": "Form 4"}]
        self.hits = [{"_id": "0000320193-26-000071:form4.xml"}]

    def test_disabled_by_default_is_noop(self):
        """Default (flag OFF): no direction key + no extra SEC request."""
        with patch("httpx.AsyncClient") as cls:
            _run(
                self.agent._enrich_form4_directions(self.rows, self.hits, "0000320193")
            )
        assert "direction" not in self.rows[0]
        cls.assert_not_called()

    def test_enabled_sets_direction(self):
        """Flag ON: each row gains a parsed direction from its form4.xml."""
        cfg = type("C", (), {"SPECIALIST_FORM4_DIRECTION_ENABLED": True})()
        resp = MagicMock()
        resp.status_code = 200
        resp.text = _sell_doc()
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.get = AsyncMock(return_value=resp)
        with patch("core.stock_specialist.get_config", return_value=cfg), patch(
            "httpx.AsyncClient", MagicMock(return_value=client)
        ):
            _run(
                self.agent._enrich_form4_directions(self.rows, self.hits, "0000320193")
            )
        assert self.rows[0]["direction"] == "sell"

    def test_no_cik_is_noop_even_when_enabled(self):
        """Flag ON but unknown CIK (free-text fallback) -> no direction, no request."""
        cfg = type("C", (), {"SPECIALIST_FORM4_DIRECTION_ENABLED": True})()
        with patch("core.stock_specialist.get_config", return_value=cfg), patch(
            "httpx.AsyncClient"
        ) as cls:
            _run(self.agent._enrich_form4_directions(self.rows, self.hits, None))
        assert "direction" not in self.rows[0]
        cls.assert_not_called()

    def test_prompt_surfaces_direction(self):
        """The synthesis prompt shows a row's direction so the LLM weighs buys vs sells
        (a sell no longer reads as a generic bullish 'insider filing')."""
        gathered = {
            "insider_trades": [
                {
                    "filed": "2026-06-20",
                    "filer": "CEO",
                    "form": "Form 4",
                    "direction": "sell",
                }
            ],
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
        prompt = self.agent._build_synthesis_prompt(gathered)
        assert "| SELL" in prompt
