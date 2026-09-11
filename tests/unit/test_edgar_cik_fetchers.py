# tests/unit/test_edgar_cik_fetchers.py
"""RQ-1 B1 (#1521): CIK-scoped EDGAR fetchers + ciks-membership entity match-back.

The 3 fetchers now resolve ticker->CIK, scope the efts query with &ciks=, and drop any hit
whose `ciks` array does not contain the resolved issuer CIK -- so a 3-letter ticker can no
longer match an unrelated registrant ("Spy Inc.", "Magnum Opus"). Output dict keys are
unchanged (serializer/prompt contract). (Epic #1516, Phase B.)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from core.stock_specialist import StockSpecialistAgent


def _run(coro):
    return asyncio.run(coro)


def _client_returning(hits, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.json = MagicMock(return_value={"hits": {"hits": hits}})
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.get = AsyncMock(return_value=resp)
    return MagicMock(return_value=client), client


def _hit(days_ago, ciks, names=("APPLE INC",)):
    filed = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    return {
        "_source": {
            "file_date": filed,
            "display_names": list(names),
            "ciks": list(ciks),
        }
    }


class TestCikFetchers:
    def setup_method(self):
        self.agent = StockSpecialistAgent("AAPL", "dummy-key")

    def test_queries_by_cik_not_freetext_only(self):
        factory, client = _client_returning([])
        with patch(
            "core.stock_specialist.resolve_cik", return_value="0000320193"
        ), patch("httpx.AsyncClient", factory):
            _run(self.agent._fetch_edgar_form4())
        url = client.get.call_args.args[0]
        assert "ciks=0000320193" in url
        assert "forms=4" in url

    def test_ciks_membership_match_back_drops_wrong_cik(self):
        hits = [
            _hit(10, ["0000320193"]),  # keep: right CIK, in-window
            _hit(3, ["0001999999"]),  # drop: wrong CIK
            _hit(400, ["0000320193"]),  # drop: right CIK but stale
            _hit(5, ["0001045810", "0000320193"]),  # keep: issuer among many CIKs
        ]
        factory, _ = _client_returning(hits)
        with patch(
            "core.stock_specialist.resolve_cik", return_value="0000320193"
        ), patch("httpx.AsyncClient", factory):
            result = _run(self.agent._fetch_edgar_form4())
        assert len(result) == 2  # only the two right-CIK in-window rows

    def test_recency_enforced_client_side(self):
        hits = [_hit(10, ["0000320193"]), _hit(400, ["0000320193"])]
        factory, _ = _client_returning(hits)
        with patch(
            "core.stock_specialist.resolve_cik", return_value="0000320193"
        ), patch("httpx.AsyncClient", factory):
            result = _run(self.agent._fetch_edgar_form4())
        assert len(result) == 1  # the 400-day-old filing dropped (45d window)

    def test_etf_short_circuits_no_io(self):
        agent = StockSpecialistAgent("SPY", "dummy-key")
        with patch("httpx.AsyncClient") as cls, patch(
            "core.stock_specialist.resolve_cik"
        ) as rc:
            result = _run(agent._fetch_edgar_form4())
        assert result == []
        cls.assert_not_called()  # no network for an ETF
        rc.assert_not_called()  # resolver not even consulted

    def test_unknown_ticker_falls_back_to_freetext(self, caplog):
        factory, client = _client_returning([])
        with patch("core.stock_specialist.resolve_cik", return_value=None), patch(
            "httpx.AsyncClient", factory
        ):
            _run(self.agent._fetch_edgar_form4())
        url = client.get.call_args.args[0]
        assert "q=%22AAPL%22" in url and "ciks=" not in url
        assert any(r.levelname == "WARNING" for r in caplog.records)

    def test_non_200_returns_empty(self):
        factory, _ = _client_returning([], status=503)
        with patch(
            "core.stock_specialist.resolve_cik", return_value="0000320193"
        ), patch("httpx.AsyncClient", factory):
            assert _run(self.agent._fetch_edgar_form4()) == []

    def test_output_dict_keys_unchanged(self):
        factory, _ = _client_returning([_hit(5, ["0000320193"])])
        with patch(
            "core.stock_specialist.resolve_cik", return_value="0000320193"
        ), patch("httpx.AsyncClient", factory):
            result = _run(self.agent._fetch_edgar_form4())
        assert set(result[0].keys()) == {"filed", "filer", "form", "period"}
