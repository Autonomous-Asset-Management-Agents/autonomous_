"""RPAR-T4 (#1268, Epic #1262): flag-gated ADR-014 seam routing for the
Gemini synthesis branch of ``StockSpecialistAgent._gemini_synthesize``.

Today the Gemini branch hand-rolls ``_call_gemini_sync`` (own ``genai.Client``,
temperature 0.3) instead of the ADR-014 provider seam. T4 adds the flag
``LLM_OUTPUT_PARITY`` (default OFF):

  * OFF (default)  -> byte-identical to today: ``_call_gemini_sync`` via
    ``run_in_executor``; the seam ``generate_content_async`` is NEVER called;
    the daily-budget gate + its increment run exactly as before.
  * ON             -> route through ``provider.generate_content_async(prompt,
    max_output_tokens=800)`` inside the semaphore; ``_call_gemini_sync`` is
    NEVER called. The key-guard and the daily-budget gate (incl. increment)
    STILL run BEFORE the branch - Free-Tier protection is not regressed.

The Ollama branch is already seam-routed (G4a-2c) and is UNTOUCHED by T4 - it
behaves identically regardless of the flag. ``_parse_synthesis`` is not touched
(NEWS-8: scoring is byte-equivalent for a given ``{"text": ...}``, no matter which
branch produced the text).

The Gemini OFF path is asyncio-coupled (``run_in_executor``), so - as in
``test_g4a2c_specialist_seam.py`` - these tests drive the coroutine with a plain
``asyncio.run`` (not ``@pytest.mark.anyio``) and stub the shared semaphore so the
tests don't depend on its event-loop binding. The flag is controlled by patching
``core.stock_specialist.get_config`` with a ``SimpleNamespace`` (same technique as
``test_specialist_ml_wiring.py``).
"""

import asyncio
import contextlib
import types
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


def _cfg(parity: bool):
    """A get_config() stand-in pinning only the flag T4 reads."""
    return types.SimpleNamespace(LLM_OUTPUT_PARITY=parity)


def _passing_budget():
    budget = MagicMock()
    budget.check_and_increment.return_value = True
    return budget


@allure.feature("VC-1 Research & Analysis")
@allure.story("RPAR-T4 LLM Output Parity Seam Routing")
class TestSpecialistProviderRouting:
    # -- OFF (default) -> hand-rolled sync path, seam never called ----------
    def test_parity_flag_off_uses_handrolled_sync(self):
        """Flag OFF (default), Gemini provider, key set, budget passes ->
        ``_call_gemini_sync`` is called exactly once and the seam
        ``generate_content_async`` is NEVER awaited. OFF == today."""
        agent = StockSpecialistAgent("AAPL", "dummy-key")
        provider = MagicMock()  # NOT an OllamaProvider -> Gemini branch
        provider.generate_content_async = AsyncMock(return_value="seam text")
        budget = _passing_budget()
        with patch("core.stock_specialist.get_config", return_value=_cfg(False)), patch(
            "core.stock_specialist.get_llm_provider", return_value=provider
        ), patch("core.gemini_budget.get_budget", return_value=budget), patch.object(
            agent, "get_semaphore", return_value=_no_semaphore()
        ), patch.object(
            agent, "_call_gemini_sync", return_value={"text": "handrolled"}
        ) as cgs:
            result = _run(agent._gemini_synthesize({}))
        assert result == {"text": "handrolled"}
        cgs.assert_called_once()
        provider.generate_content_async.assert_not_awaited()
        # Budget gate + increment run byte-identical to today.
        budget.check_and_increment.assert_called_once()

    # -- ON -> route through the ADR-014 seam, sync never called ------------
    def test_parity_flag_on_routes_through_seam(self):
        """Flag ON, Gemini provider -> ``generate_content_async(prompt,
        max_output_tokens=800)`` is awaited exactly once, ``_call_gemini_sync``
        is NEVER called, and the return is ``{"text": <seam-output>}``."""
        agent = StockSpecialistAgent("AAPL", "dummy-key")
        provider = MagicMock()
        provider.generate_content_async = AsyncMock(
            return_value="SUMMARY: ok\nSCORE: 60"
        )
        budget = _passing_budget()
        with patch("core.stock_specialist.get_config", return_value=_cfg(True)), patch(
            "core.stock_specialist.get_llm_provider", return_value=provider
        ), patch("core.gemini_budget.get_budget", return_value=budget), patch.object(
            agent, "get_semaphore", return_value=_no_semaphore()
        ), patch.object(
            agent, "_call_gemini_sync"
        ) as cgs:
            result = _run(agent._gemini_synthesize({"x": 1}))
        assert result == {"text": "SUMMARY: ok\nSCORE: 60"}
        provider.generate_content_async.assert_awaited_once()
        # Decoding param pinned: max_output_tokens=800, prompt positional.
        _args, kwargs = provider.generate_content_async.call_args
        assert kwargs.get("max_output_tokens") == 800
        cgs.assert_not_called()

    def test_parity_flag_on_seam_exception_returns_empty(self):
        """Flag ON: a seam exception is logged WARNING and yields {} (graceful
        degrade to raw-data scoring), same shape as a failed sync call."""
        agent = StockSpecialistAgent("AAPL", "dummy-key")
        provider = MagicMock()
        provider.generate_content_async = AsyncMock(side_effect=RuntimeError("boom"))
        budget = _passing_budget()
        with patch("core.stock_specialist.get_config", return_value=_cfg(True)), patch(
            "core.stock_specialist.get_llm_provider", return_value=provider
        ), patch("core.gemini_budget.get_budget", return_value=budget), patch.object(
            agent, "get_semaphore", return_value=_no_semaphore()
        ):
            result = _run(agent._gemini_synthesize({}))
        assert result == {}

    # -- ON -> budget gate still active (Free-Tier protection) --------------
    def test_parity_on_still_honors_budget_gate(self):
        """Flag ON but ``check_and_increment`` -> False: returns {} and the seam
        is NEVER awaited (Free-Tier guard not regressed)."""
        agent = StockSpecialistAgent("AAPL", "dummy-key")
        provider = MagicMock()
        provider.generate_content_async = AsyncMock(return_value="should-not-run")
        budget = MagicMock()
        budget.check_and_increment.return_value = False
        with patch("core.stock_specialist.get_config", return_value=_cfg(True)), patch(
            "core.stock_specialist.get_llm_provider", return_value=provider
        ), patch("core.gemini_budget.get_budget", return_value=budget), patch.object(
            agent, "get_semaphore", return_value=_no_semaphore()
        ):
            result = _run(agent._gemini_synthesize({}))
        assert result == {}
        provider.generate_content_async.assert_not_awaited()

    def test_parity_on_no_api_key_returns_empty(self):
        """Flag ON but no Gemini key -> {} (key-guard before the branch), and the
        seam is NEVER awaited."""
        agent = StockSpecialistAgent("AAPL", "")  # no key
        provider = MagicMock()
        provider.generate_content_async = AsyncMock(return_value="should-not-run")
        with patch("core.stock_specialist.get_config", return_value=_cfg(True)), patch(
            "core.stock_specialist.get_llm_provider", return_value=provider
        ), patch.object(agent, "get_semaphore", return_value=_no_semaphore()):
            result = _run(agent._gemini_synthesize({}))
        assert result == {}
        provider.generate_content_async.assert_not_awaited()

    # -- Ollama branch unchanged regardless of the flag -------------------
    def test_ollama_branch_unchanged_regardless_of_flag(self):
        """Provider is an OllamaProvider -> both OFF and ON take the same Ollama
        seam path: no budget gate, no ``_call_gemini_sync``."""
        for parity in (False, True):
            agent = StockSpecialistAgent("AAPL", "dummy-key")
            prov = OllamaProvider()
            prov.generate_content_async = AsyncMock(return_value="ollama synth")
            budget = MagicMock()
            with patch(
                "core.stock_specialist.get_config", return_value=_cfg(parity)
            ), patch(
                "core.stock_specialist.get_llm_provider", return_value=prov
            ), patch(
                "core.gemini_budget.get_budget", return_value=budget
            ), patch.object(
                agent, "get_semaphore", return_value=_no_semaphore()
            ), patch.object(
                agent, "_call_gemini_sync"
            ) as cgs:
                result = _run(agent._gemini_synthesize({}))
            assert result == {"text": "ollama synth"}, f"parity={parity}"
            prov.generate_content_async.assert_awaited_once()
            budget.check_and_increment.assert_not_called()
            cgs.assert_not_called()

    # -- NEWS-8: _parse_synthesis is unchanged / branch-agnostic ----------
    def test_parse_synthesis_unchanged(self):
        """The same ``{"text": ...}`` text parses to identical
        news_summary / sentiment_score / recommendation regardless of which
        branch produced it - T4 does not touch _parse_synthesis (NEWS-8)."""
        agent = StockSpecialistAgent("AAPL", "dummy-key")
        text = "SUMMARY: Strong demand\nOUTLOOK: bullish\nSCORE: 72\n- catalyst"
        (
            news_summary,
            _alt,
            recommendation,
            sentiment_score,
            _conf,
            _reasons,
        ) = agent._parse_synthesis(text)
        assert news_summary == "Strong demand"
        assert sentiment_score == 72.0
        assert recommendation == "buy"


@allure.feature("VC-1 Research & Analysis")
@allure.story("RPAR-T4 Config Parity")
class TestConfigParity:
    def test_oss_config_parity_llm_output_parity(self):
        """``LLM_OUTPUT_PARITY`` exists in config.py (RuntimeConfigState) AND
        config.oss.py, both default False, both env-overridable. config.oss.py
        is loaded explicitly via importlib (its name has a dot -> not a plain
        module), mirroring test_config_oss_get_secret_str.py."""
        import importlib.util
        import os

        import config as config_py

        oss_path = os.path.abspath(
            os.path.join(
                os.path.dirname(__file__), os.pardir, os.pardir, "config.oss.py"
            )
        )

        def _load_oss():
            spec = importlib.util.spec_from_file_location("config_oss", oss_path)
            assert spec and spec.loader, f"Could not load {oss_path}"
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod

        # Defaults: both editions default False.
        cfg = config_py.get_config()
        assert hasattr(cfg, "LLM_OUTPUT_PARITY")
        assert cfg.LLM_OUTPUT_PARITY is False
        oss_mod = _load_oss()
        assert hasattr(oss_mod, "LLM_OUTPUT_PARITY")
        assert oss_mod.LLM_OUTPUT_PARITY is False

        # Env-overridable in both editions (Enterprise reads it on instantiation,
        # OSS reads it at module exec -> re-exec the spec under the patched env).
        with patch.dict(os.environ, {"LLM_OUTPUT_PARITY": "true"}):
            assert config_py.RuntimeConfigState().LLM_OUTPUT_PARITY is True
            assert _load_oss().LLM_OUTPUT_PARITY is True
