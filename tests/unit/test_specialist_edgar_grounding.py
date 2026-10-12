# tests/unit/test_specialist_edgar_grounding.py
"""RQ-1 B6 (#1526): golden-input regression gate for the specialist EDGAR grounding.

Locks in the invariants A1-B3 established against the original Reports failures (wrong-entity
"Spy Inc.", decade-stale filings shown as recent, ETF noise, uniform count-driven 94/100). A
regression in B1 (CIK match-back / ETF allowlist), A2 (recency) or B3 (no count bonus) trips
exactly one of these. Behaviour is already correct on main, so this is a GREEN gate, not a
red-first feature. (Epic #1516, Phase B.)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from core.stock_specialist import StockSpecialistAgent

_AAPL_CIK = "0000320193"
_WRONG_CIK = "0001999999"  # an unrelated registrant -- the "Spy Inc." style collision


def _run(coro):
    return asyncio.run(coro)


def _client(hits, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.json = MagicMock(return_value={"hits": {"hits": hits}})
    c = MagicMock()
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    c.get = AsyncMock(return_value=resp)
    return MagicMock(return_value=c), c


def _hit(days_ago, ciks, filer="APPLE INC"):
    filed = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    return {
        "_source": {"file_date": filed, "display_names": [filer], "ciks": list(ciks)}
    }


def _maxed_gathered():
    """Same maxed-out filing COUNTS for any symbol -- the pre-fix uniform-94 trigger."""
    return {
        "insider_trades": [{"filed": "2026-06-20", "filer": "x", "form": "Form 4"}]
        * 10,
        "material_events": [{"filed": "2026-06-20", "entity": "x"}] * 5,
        "activist_stakes": [{"filed": "2026-06-20", "filer": "x", "form": "13D"}] * 5,
        "political_trades": [],
        "recent_headlines": ["news"],
        "wiki_spike": False,
        "wiki_views_7d": 0,
        "reddit_mentions_24h": 0,
        "reddit_sentiment": "neutral",
        "short_interest_pct": None,
        "google_trend_score": None,
    }


class TestEdgarGroundingInvariants:
    def test_inv1_etf_yields_no_issuer_filings(self):
        """ETF -> no insider/13D/8-K (kills the 'Spy Inc.' / 'Magnum Opus' noise)."""
        agent = StockSpecialistAgent("SPY", "dummy-key")
        with patch("httpx.AsyncClient") as cls, patch(
            "core.stock_specialist.resolve_cik"
        ) as rc:
            assert _run(agent._fetch_edgar_form4()) == []
        cls.assert_not_called()
        rc.assert_not_called()

    def test_inv2_wrong_entity_collision_dropped(self):
        """A hit whose CIK != the symbol's resolved CIK is dropped (entity match-back)."""
        agent = StockSpecialistAgent("AAPL", "dummy-key")
        hits = [_hit(5, [_AAPL_CIK]), _hit(5, [_WRONG_CIK], filer="SPY INC")]
        factory, _ = _client(hits)
        with patch("core.stock_specialist.resolve_cik", return_value=_AAPL_CIK), patch(
            "httpx.AsyncClient", factory
        ):
            result = _run(agent._fetch_edgar_form4())
        assert [r["filer"] for r in result] == ["APPLE INC"]  # the collision is gone

    def test_inv3_stale_filing_dropped(self):
        """A filing older than the lookback window is dropped (recency)."""
        agent = StockSpecialistAgent("AAPL", "dummy-key")
        hits = [_hit(5, [_AAPL_CIK]), _hit(400, [_AAPL_CIK])]
        factory, _ = _client(hits)
        with patch("core.stock_specialist.resolve_cik", return_value=_AAPL_CIK), patch(
            "httpx.AsyncClient", factory
        ):
            assert len(_run(agent._fetch_edgar_form4())) == 1

    def test_inv4_no_uniform_count_inflation(self):
        """Identical maxed filing counts but different LLM SCOREs -> distinct scores (no +9
        count bonus collapsing both to a uniform ~94)."""
        ra = StockSpecialistAgent("AAPL", "dummy-key")._build_report(
            _maxed_gathered(), {"text": "SCORE: 55\nOUTLOOK: neutral\n"}
        )
        rb = StockSpecialistAgent("MSFT", "dummy-key")._build_report(
            _maxed_gathered(), {"text": "SCORE: 70\nOUTLOOK: bullish\n"}
        )
        assert ra.sentiment_score == 55.0  # no count bonus
        assert rb.sentiment_score == 70.0
        assert ra.sentiment_score != rb.sentiment_score
