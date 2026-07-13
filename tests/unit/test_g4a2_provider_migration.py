"""G4a-2 (#1050): consumer migration to the LLM provider seam.

Each LLM consumer must resolve its model through
``core.llm.provider.get_llm_provider`` (the single sanctioned seam, ADR-014)
rather than calling ``get_gemini_instance()`` directly — otherwise
``LLM_PROVIDER=ollama`` (the desktop "local LLM" option) never reaches them.

The default (unset / "gemini") path stays byte-identical: the seam delegates
to ``get_gemini_instance()`` and returns its exact object (identity proven in
tests/unit/test_llm_provider.py). These tests only assert the *wiring* — that
the consumers route through the seam, provider-agnostically.

RED on origin/main: the consumers still import ``get_gemini_instance`` (or hold
the ``_gemini_module`` reference) and have no ``get_llm_provider`` binding.
"""

import importlib
from unittest.mock import AsyncMock, MagicMock, patch

import allure
import pytest

# Consumers migrated in G4a-2. (core.stock_specialist is G4a-2c — its own PR —
# and the gemini_client chat fns import the seam lazily, covered behaviourally
# by tests/unit/test_gemini_client.py through the seam's delegation.)
_CONSUMER_MODULES = [
    "core.round_table.agents",
    "core.learning.engine",
    "core.market_scanner",
    "core.news_processor",
]


@allure.feature("VC-1 Research & Analysis")
@allure.story("LLM Provider Seam")
class TestConsumerSeamWiring:
    """Every consumer binds the seam and drops the direct gemini symbols."""

    @pytest.mark.parametrize("modname", _CONSUMER_MODULES)
    def test_consumer_binds_the_seam(self, modname):
        mod = importlib.import_module(modname)
        assert hasattr(
            mod, "get_llm_provider"
        ), f"{modname} must resolve its LLM via the get_llm_provider seam"

    @pytest.mark.parametrize("modname", _CONSUMER_MODULES)
    def test_consumer_drops_direct_gemini_symbols(self, modname):
        mod = importlib.import_module(modname)
        assert not hasattr(mod, "get_gemini_instance"), (
            f"{modname} must not import get_gemini_instance directly "
            "(bypasses the seam — LLM_PROVIDER would be ignored)"
        )
        assert not hasattr(
            mod, "_gemini_module"
        ), f"{modname} must not hold the core.gemini_client module reference"


@allure.feature("VC-1 Research & Analysis")
@allure.story("LLM Provider Seam")
class TestNewsSentimentAgentProviderAgnostic:
    """The NewsSentimentAgent vote resolves its model through the seam, so a
    local-LLM (Ollama) reply parses through the exact same extraction path as a
    Gemini reply — proving the migration is provider-agnostic, not Gemini-bound.
    """

    @pytest.mark.anyio
    async def test_vote_resolves_via_seam(self):
        from core.round_table.agents import NewsSentimentAgent

        # A provider stub — could be Gemini OR Ollama; the agent must not care.
        stub = MagicMock()
        stub.generate_content_async = AsyncMock(return_value="0.66")
        state = {
            "symbol": "AAPL",
            "ohlc": {
                "open": 150.0,
                "high": 155.0,
                "low": 148.0,
                "close": 152.0,
                "volume": 1_000_000.0,
            },
        }
        with patch("core.round_table.agents.get_llm_provider", return_value=stub):
            agent = NewsSentimentAgent()
            result = await agent.vote(state)

        assert abs(result.score - 0.66) < 1e-3
        assert result.weight > 0.0, "a real LLM signal must count (weight>0)"
        stub.generate_content_async.assert_awaited_once()
