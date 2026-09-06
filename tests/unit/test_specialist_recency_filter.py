# tests/unit/test_specialist_recency_filter.py
"""RQ-1 A2 (#1518): post-fetch recency filter for the specialist EDGAR fetchers.

EDGAR full-text search ranks by relevance, not date, and the cutoff is only sent as the
`startdt` query param -- so stale filings (2009/2012/2017) flowed through and were shown
as "recent this cycle". Each fetcher must drop any hit whose file_date is older than its
lookback cutoff. (Epic #1516, Phase A.)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from core.stock_specialist import StockSpecialistAgent


# RQ-1 B1 (#1521): the fetchers now resolve ticker->CIK and apply a `ciks` membership
# match-back BEFORE the recency filter. These tests isolate the RECENCY behaviour, so they
# force the free-text fallback (resolve_cik -> None); the CIK-path recency is covered by
# tests/unit/test_edgar_cik_fetchers.py::test_recency_enforced_client_side.
@pytest.fixture(autouse=True)
def _force_freetext_fallback():
    with patch("core.stock_specialist.resolve_cik", return_value=None):
        yield


class _FakeResp:
    status_code = 200

    def __init__(self, hits):
        self._hits = hits

    def json(self):
        return {"hits": {"hits": self._hits}}


class _FakeClient:
    """Minimal async-context-manager stand-in for httpx.AsyncClient."""

    def __init__(self, hits):
        self._hits = hits

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None):
        return _FakeResp(self._hits)


def _hits(*dates):
    return [
        {
            "_source": {
                "file_date": d,
                "display_names": [f"Filer {d}"],
                "entity_name": f"Entity {d}",
                "period_of_report": d,
            }
        }
        for d in dates
    ]


class TestRecencyFilter:
    def test_form4_drops_stale_filings(self):
        agent = StockSpecialistAgent("AAPL", "dummy-key")
        recent = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
        with patch(
            "httpx.AsyncClient", lambda **kw: _FakeClient(_hits(recent, "2009-01-01"))
        ):
            result = asyncio.run(agent._fetch_edgar_form4())
        filed = [r["filed"] for r in result]
        assert recent in filed
        assert "2009-01-01" not in filed

    def test_8k_drops_stale_filings(self):
        agent = StockSpecialistAgent("AAPL", "dummy-key")
        recent = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
        with patch(
            "httpx.AsyncClient", lambda **kw: _FakeClient(_hits(recent, "2012-06-01"))
        ):
            result = asyncio.run(agent._fetch_edgar_8k())
        filed = [r["filed"] for r in result]
        assert recent in filed
        assert "2012-06-01" not in filed

    def test_13d_drops_stale_filings(self):
        agent = StockSpecialistAgent("AAPL", "dummy-key")
        recent = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        with patch(
            "httpx.AsyncClient", lambda **kw: _FakeClient(_hits(recent, "2017-03-15"))
        ):
            result = asyncio.run(agent._fetch_edgar_13d())
        filed = [r["filed"] for r in result]
        assert recent in filed
        assert "2017-03-15" not in filed
