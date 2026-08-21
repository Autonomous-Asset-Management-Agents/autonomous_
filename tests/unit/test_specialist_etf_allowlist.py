# tests/unit/test_specialist_etf_allowlist.py
"""RQ-1 A1 (#1517): ETF allowlist for the specialist EDGAR fetchers.

ETFs (SPY/QQQ/DIA/IWM ...) have no own insider / Form-4 / 13D / 8-K *issuer* filings.
The fetchers in core/stock_specialist.py search EDGAR by the bare ticker string
(`q="{symbol}"`), so for an ETF they surface unrelated registrants ("Spy Inc.",
"Magnum Opus", "Creative Learning Corp") as fake filings, which then inflate sentiment.
They must short-circuit to [] WITHOUT any network call. (Epic #1516, Phase A.)
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from core.stock_specialist import StockSpecialistAgent


class TestEtfAllowlist:
    @pytest.mark.parametrize("etf", ["SPY", "QQQ", "DIA", "IWM"])
    def test_etf_short_circuits_all_edgar_fetchers(self, etf):
        """An ETF returns [] from all three EDGAR fetchers and never touches the network."""
        agent = StockSpecialistAgent(etf, "dummy-key")
        with patch("httpx.AsyncClient") as mock_client:
            assert asyncio.run(agent._fetch_edgar_form4()) == []
            assert asyncio.run(agent._fetch_edgar_8k()) == []
            assert asyncio.run(agent._fetch_edgar_13d()) == []
        mock_client.assert_not_called()

    def test_non_etf_still_attempts_edgar(self):
        """A real stock (AAPL) must NOT be short-circuited -- it still attempts the fetch
        (guards against the allowlist over-blocking real issuers)."""
        agent = StockSpecialistAgent("AAPL", "dummy-key")
        with patch(
            "httpx.AsyncClient", side_effect=RuntimeError("network blocked")
        ) as mock_client:
            assert asyncio.run(agent._fetch_edgar_form4()) == []
        mock_client.assert_called()
