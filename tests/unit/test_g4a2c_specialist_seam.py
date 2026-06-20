"""G4a-2c (#1050): stock_specialist synthesis through the LLM provider seam.

``StockSpecialistAgent._gemini_synthesize`` bypassed the seam with its own
``genai.Client``. G4a-2c adds an **Ollama branch** (so a desktop local-LLM user
gets specialist synthesis instead of a silent ``{}``) while the **Gemini branch
stays byte-identical** — own client, ``temperature=0.3``, the daily-budget gate.
The budget gate stays **Gemini-only** (Ollama is local/free; intentional, ADR).

The Gemini branch is asyncio-coupled (``asyncio.run_in_executor``), so these
tests drive the coroutine with a plain ``asyncio.run`` rather than ``@anyio`` —
the repo's ``anyio_backends`` pyproject key is a typo, so anyio would otherwise
also parametrize trio, where ``run_in_executor`` cannot be awaited. The shared
``asyncio.Semaphore`` is stubbed so the tests don't depend on its loop binding.

RED on the G4a-2 base: stock_specialist has no ``get_llm_provider`` import and no
provider branch — patching ``core.stock_specialist.get_llm_provider`` raises.
"""

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import allure

from core.llm.provider import OllamaProvider
from core.stock_specialist import StockSpecialistAgent


@contextlib.asynccontextmanager
async def _no_semaphore():
    """Backend-agnostic stand-in for the process-wide asyncio.Semaphore."""
    yield


def _run(coro):
    return asyncio.run(coro)


@allure.feature("VC-1 Research & Analysis")
@allure.story("LLM Provider Seam")
class TestSpecialistSynthesisSeam:
    def test_gemini_path_uses_budget_and_direct_client(self):
        """LLM_PROVIDER unset → Gemini branch byte-identical: the daily-budget
        gate fires once and synthesis goes through the unchanged
        ``_call_gemini_sync`` (own client, temperature 0.3)."""
        agent = StockSpecialistAgent("AAPL", "dummy-key")
        budget = MagicMock()
        budget.check_and_increment.return_value = True
        with patch(
            "core.stock_specialist.get_llm_provider", return_value=MagicMock()
        ), patch("core.gemini_budget.get_budget", return_value=budget), patch.object(
            agent, "get_semaphore", return_value=_no_semaphore()
        ), patch.object(
            agent, "_call_gemini_sync", return_value={"text": "gemini synth"}
        ) as cgs:
            result = _run(agent._gemini_synthesize({}))
        assert result == {"text": "gemini synth"}
        budget.check_and_increment.assert_called_once()
        cgs.assert_called_once()

    def test_ollama_path_runs_without_budget(self):
        """LLM_PROVIDER=ollama → Ollama branch: the provider answers and the
        Gemini daily-budget gate is NOT consulted (local/free)."""
        agent = StockSpecialistAgent("AAPL", "dummy-key")
        prov = OllamaProvider()
        prov.generate_content_async = AsyncMock(return_value="ollama synth")
        budget = MagicMock()
        with patch("core.stock_specialist.get_llm_provider", return_value=prov), patch(
            "core.gemini_budget.get_budget", return_value=budget
        ), patch.object(agent, "get_semaphore", return_value=_no_semaphore()):
            result = _run(agent._gemini_synthesize({}))
        assert result == {"text": "ollama synth"}
        prov.generate_content_async.assert_awaited_once()
        budget.check_and_increment.assert_not_called()

    def test_ollama_synthesizes_without_gemini_key(self):
        """Desktop 'fully local': no Gemini key + Ollama → synthesis still runs
        (the old key-guard returned {} → zero specialist LLM synthesis)."""
        agent = StockSpecialistAgent("AAPL", "")  # no Gemini key
        prov = OllamaProvider()
        prov.generate_content_async = AsyncMock(return_value="local synth")
        with patch(
            "core.stock_specialist.get_llm_provider", return_value=prov
        ), patch.object(agent, "get_semaphore", return_value=_no_semaphore()):
            result = _run(agent._gemini_synthesize({}))
        assert result == {"text": "local synth"}

    def test_gemini_path_no_key_returns_empty(self):
        """No Gemini key + Gemini branch → {} exactly as today (byte-identical guard)."""
        agent = StockSpecialistAgent("AAPL", "")  # no key
        with patch("core.stock_specialist.get_llm_provider", return_value=MagicMock()):
            result = _run(agent._gemini_synthesize({}))
        assert result == {}
