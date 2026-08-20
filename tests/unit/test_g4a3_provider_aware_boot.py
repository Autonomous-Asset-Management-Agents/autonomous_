"""G4a-3 (#1050): provider-aware boot gates.

The engine hard-requires a Gemini key at init (`validate_dependencies`) and
probes Gemini in the startup health check. G4a-3 makes them provider-aware: with
``LLM_PROVIDER=ollama`` (desktop local LLM) no Gemini key is required at init,
and the health check probes Ollama via the shared ``core.llm.health.ollama_reachable``
helper (native ``httpx.AsyncClient``). The default (unset / "gemini") path stays
byte-identical; cloud unaffected.

``LLM_PROVIDER=ollama`` tests strip ``K_SERVICE`` (the GKE self-hosted runner
inherits it from the node) per the project convention.

The ``_check_llm`` direct tests drive the coroutine with ``asyncio.run`` rather
than ``@anyio``: the **Gemini** branch is asyncio-coupled (``asyncio.to_thread``
for the sync ``genai`` call), and the repo's ``anyio_backends`` pyproject key is
not honored by this pytest setup (reported as an unknown config option), so anyio
would otherwise also parametrize trio, where ``run_in_executor`` cannot be awaited.
"""

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import allure
import pytest


@pytest.fixture
def engine():
    """Minimal BotEngine (no real API connections), mirrors test_startup_health_check."""
    with patch("config.GEMINI_API_KEY", "test-key"), patch(
        "core.engine.base.TradingClient", MagicMock()
    ), patch("core.engine.base.StockHistoricalDataClient", MagicMock()), patch(
        "core.engine.base.RedisClient", MagicMock()
    ), patch(
        "core.engine.base.AIMarketScanner", MagicMock()
    ), patch(
        "core.engine.base.AILearningEngine", MagicMock()
    ), patch(
        "core.engine.base.HistoricalDataProvider", MagicMock()
    ), patch(
        "core.engine.base.MarketRegimeModel", MagicMock()
    ), patch(
        "core.engine.base.NewsProcessor", MagicMock()
    ), patch(
        "core.engine.base.AILearnedRules", MagicMock()
    ), patch(
        "core.engine.base.AgentRegistry", MagicMock()
    ), patch(
        "core.engine.base.set_global_registry", MagicMock()
    ), patch(
        "core.engine.base.ComplianceGuardian", MagicMock()
    ), patch(
        "core.engine.base.get_cloud_logger", MagicMock()
    ), patch(
        "core.engine.base.threading.Thread", MagicMock()
    ):
        from core.engine.base import BotEngine

        eng = BotEngine.__new__(BotEngine)
        eng._shutdown_event = MagicMock()
        eng._shutdown_event.is_set.return_value = False
        return eng


@pytest.fixture
def _no_kservice(monkeypatch):
    monkeypatch.delenv("K_SERVICE", raising=False)


def _run(coro):
    return asyncio.run(coro)


@contextlib.asynccontextmanager
async def _acm(obj):
    """Minimal async context manager yielding ``obj`` (mocks ``httpx.AsyncClient``)."""
    yield obj


# ── validate_dependencies — provider-aware (auditor-mandated remediation) ─────
@allure.feature("VC-0 Platform Infrastructure")
@allure.story("LLM Provider Seam")
class TestValidateDependenciesProviderAware:
    def test_ollama_no_gemini_key_does_not_raise(
        self, engine, monkeypatch, _no_kservice
    ):
        """LLM_PROVIDER=ollama + no Gemini key → init must NOT hard-fail."""
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        with patch("config.GEMINI_API_KEY", None):
            engine.validate_dependencies()  # must not raise

    def test_gemini_no_key_raises_as_today(self, engine, monkeypatch, _no_kservice):
        """LLM_PROVIDER unset (gemini) + no key → raises exactly as today."""
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        with patch("config.GEMINI_API_KEY", None):
            with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
                engine.validate_dependencies()

    def test_unknown_provider_typo_fails_fast(self, engine, monkeypatch, _no_kservice):
        """Fail-fast (audit #1183): a typo like 'ollam' is NOT 'ollama' → the seam
        falls back to gemini, so the Gemini key IS still required and init must
        raise immediately — whitelist (== 'ollama'), not blind != exclusion."""
        monkeypatch.setenv("LLM_PROVIDER", "ollam")  # typo
        with patch("config.GEMINI_API_KEY", None):
            with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
                engine.validate_dependencies()


# ── base.py _check_llm — provider-aware (delegates Ollama to the shared helper) ─
@allure.feature("VC-0 Platform Infrastructure")
@allure.story("LLM Provider Seam")
class TestCheckLlm:
    def test_ollama_reachable_returns_true(self, engine, monkeypatch, _no_kservice):
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        with patch("core.engine.base.ollama_reachable", AsyncMock(return_value=True)):
            assert _run(engine._check_llm()) is True

    def test_ollama_unreachable_returns_false(self, engine, monkeypatch, _no_kservice):
        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        with patch("core.engine.base.ollama_reachable", AsyncMock(return_value=False)):
            assert _run(engine._check_llm()) is False

    def test_gemini_branch_unchanged(self, engine, monkeypatch, _no_kservice):
        """LLM_PROVIDER unset → the existing Gemini probe path (byte-identical)."""
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        with patch("config.GEMINI_API_KEY", "k"), patch(
            "google.genai.Client"
        ) as client_cls:
            client_cls.return_value.models.generate_content.return_value = MagicMock()
            assert _run(engine._check_llm()) is True


# ── _startup_health_check — critical key is now "llm" ─────────────────────────
@allure.feature("VC-0 Platform Infrastructure")
@allure.story("LLM Provider Seam")
class TestStartupHealthCheckKey:
    @pytest.mark.anyio
    async def test_critical_failure_lists_llm(self, engine):
        with patch.object(
            engine, "_check_redis", AsyncMock(return_value=True)
        ), patch.object(
            engine, "_check_llm", AsyncMock(return_value=False)
        ), patch.object(
            engine, "_check_model_files", return_value=True
        ), patch(
            "core.engine.base.send_slack_alert", MagicMock()
        ):
            with pytest.raises(RuntimeError, match="llm"):
                await engine._startup_health_check()


# ── shadow_boot._check_llm — boot-time pre-flight + degrade asymmetry ─────────
@allure.feature("VC-0 Platform Infrastructure")
@allure.story("LLM Provider Seam")
class TestShadowBootCheckLlm:
    """The Ollama degrade-asymmetry (paper degrades, live hard-fails) is the most
    safety-sensitive branch in G4a-3 — covered here in tests/unit so it actually
    runs in CI (tests/test_shadow_boot.py lives in the uncollected tests/ root).
    PAPER_TRADING is set via monkeypatch.setattr on the config module (audit
    #1183: the old code looked up a bogus env var named "config.PAPER_TRADING")."""

    def test_ollama_reachable_returns_true(self, monkeypatch, _no_kservice):
        from scripts import shadow_boot

        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        with patch(
            "scripts.shadow_boot.ollama_reachable", AsyncMock(return_value=True)
        ):
            assert _run(shadow_boot._check_llm()) is True

    def test_ollama_unreachable_degrades_on_paper(self, monkeypatch, _no_kservice):
        from scripts import shadow_boot

        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        monkeypatch.setattr("config.PAPER_TRADING", True)
        with patch(
            "scripts.shadow_boot.ollama_reachable", AsyncMock(return_value=False)
        ):
            assert _run(shadow_boot._check_llm()) is True  # paper → degrade

    def test_ollama_unreachable_hard_fails_on_live(self, monkeypatch, _no_kservice):
        from scripts import shadow_boot

        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        monkeypatch.setattr("config.PAPER_TRADING", False)
        with patch(
            "scripts.shadow_boot.ollama_reachable", AsyncMock(return_value=False)
        ):
            assert _run(shadow_boot._check_llm()) is False  # live → hard-fail


# ── core.llm.health.ollama_reachable — native async, never raises ─────────────
@allure.feature("VC-0 Platform Infrastructure")
@allure.story("LLM Provider Seam")
class TestOllamaReachableHelper:
    def test_reachable_uses_native_async_client(self):
        """200 from /api/tags → True, via the NATIVE httpx.AsyncClient (audit
        #1183: no sync httpx.get + asyncio.to_thread thread-pool abuse)."""
        from core.llm.health import ollama_reachable

        resp = MagicMock()
        resp.status_code = 200
        client = MagicMock()
        client.get = AsyncMock(return_value=resp)
        with patch("httpx.AsyncClient", return_value=_acm(client)) as async_client:
            assert _run(ollama_reachable("http://x:11434")) is True
        async_client.assert_called_once()  # native AsyncClient was used
        client.get.assert_awaited_once()

    def test_unreachable_returns_false(self):
        from core.llm.health import ollama_reachable

        with patch("httpx.AsyncClient", side_effect=OSError("connection refused")):
            assert _run(ollama_reachable("http://x:11434")) is False
