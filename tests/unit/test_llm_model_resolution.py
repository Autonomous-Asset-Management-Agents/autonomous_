# tests/unit/test_llm_model_resolution.py
"""#2962 — provider-true model identity + honest failure detail.

Live finding (mac desktop, 2026-08-19): /engine-diagnostics reported
llm_provider="ollama" together with llm_model_name="gemini-2.5-flash" and
177 anonymous RuntimeError fails. The model name came from a provider-blind
`getattr(config, "GEMINI_MODEL_NAME")` read; the actual http_404 (model not
pulled) was indistinguishable from "daemon down".

Contract pinned here:
- resolved_model_name() resolves EXACTLY like the seam's providers do
  (one source of truth — provider.py consumes the helper)
- _collect_llm reports the provider-true model, never cross-provider
- telemetry keeps a machine-only http_NNN detail (never free-form text)
- the boot probe warns (never aborts) when the ollama model is not pulled
"""

from unittest.mock import MagicMock

import allure
import pytest

import config as config_mod  # noqa: F401 — patched via monkeypatch below
from core.llm.health import resolved_model_name


@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestResolvedModelName:
    @pytest.mark.parametrize(
        "provider, env_vars, exp, pass_prov",
        [
            ("ollama", {}, "llama3.2", True),
            ("ollama", {"LOCAL_LLM_MODEL": "phi3.5"}, "phi3.5", True),
            (
                "gemini",
                {"LLM_PROVIDER": "gemini"},
                config_mod.GEMINI_MODEL_NAME,
                False,
            ),
            ("openai", {}, "gpt-4o-mini", True),
            ("anthropic", {}, "claude-3-5-haiku-latest", True),
        ],
    )
    def test_resolved_model_name_providers(
        self, monkeypatch, provider, env_vars, exp, pass_prov
    ):
        for k in ["LOCAL_LLM_MODEL", "OPENAI_MODEL", "ANTHROPIC_MODEL"]:
            monkeypatch.delenv(k, raising=False)
        for k, v in env_vars.items():
            monkeypatch.setenv(k, v)
        if pass_prov:
            assert resolved_model_name(provider) == exp
        else:
            assert resolved_model_name() == exp

    def test_provider_consumes_helper(self, monkeypatch):
        # One source of truth: the OllamaProvider's own default resolution
        # must be the helper's — diagnostics can never drift from the seam.
        from core.llm.provider import OllamaProvider

        monkeypatch.setenv("LOCAL_LLM_MODEL", "mistral:7b-instruct-v0.3-q4_K_M")
        assert OllamaProvider().model == resolved_model_name("ollama")


@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestCollectLlmProviderTrue:
    def test_ollama_provider_reports_ollama_model(self, monkeypatch):
        from core.engine.api_routes import _collect_llm

        monkeypatch.setenv("LLM_PROVIDER", "ollama")
        monkeypatch.setenv("LOCAL_LLM_MODEL", "phi3.5")
        out = _collect_llm()
        assert out["llm_provider"] == "ollama"
        assert out["llm_model_name"] == "phi3.5", (
            "the live defect: the GEMINI model name was reported while the "
            "engine asked ollama for a different model"
        )

    def test_gemini_provider_keeps_gemini_model(self, monkeypatch):
        from core.engine.api_routes import _collect_llm

        monkeypatch.setenv("LLM_PROVIDER", "gemini")
        out = _collect_llm()
        assert out["llm_model_name"] == config_mod.GEMINI_MODEL_NAME


@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestHttpErrorDetail:
    @pytest.fixture(autouse=True)
    def _reset(self):
        from core.llm import telemetry as t

        t.reset_llm_counters()
        yield
        t.reset_llm_counters()

    def test_http_404_detail_is_kept(self, monkeypatch):
        import httpx

        import core.llm.provider as provider
        from core.llm.telemetry import get_llm_counters

        resp = MagicMock(status_code=404)
        monkeypatch.setattr(httpx, "post", lambda *a, **k: resp)
        assert provider.OllamaProvider(model="llama3.2").generate_content("x") == ""
        counters = get_llm_counters()
        assert counters["llm_last_error"] == "RuntimeError"
        assert (
            counters["llm_last_error_detail"] == "http_404"
        ), "'model missing' must be distinguishable from 'daemon down'"

    def test_free_form_error_text_is_never_stored(self):
        from core.llm.telemetry import get_llm_counters, record_call

        record_call(0.0, RuntimeError("secret prompt leaked: AAPL going up"))
        counters = get_llm_counters()
        assert counters["llm_last_error"] == "RuntimeError"
        assert counters["llm_last_error_detail"] is None, (
            "only the http_NNN pattern may pass — never free-form " "text (privacy)"
        )


@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestOllamaModelPresent:
    @pytest.mark.anyio
    async def test_model_present_true_false_none(self, monkeypatch):
        from unittest.mock import AsyncMock, MagicMock

        import httpx

        from core.llm import health

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"models": [{"name": "phi3.5:latest"}]}

        mock_client_instance = AsyncMock()
        mock_client_instance.__aenter__.return_value = mock_client_instance
        mock_client_instance.__aexit__.return_value = False
        mock_client_instance.get.return_value = mock_resp

        monkeypatch.setattr(
            httpx, "AsyncClient", lambda *args, **kwargs: mock_client_instance
        )
        assert await health.ollama_model_present("phi3.5") is True
        assert await health.ollama_model_present("llama3.2") is False

        mock_client_boom = AsyncMock()
        mock_client_boom.__aenter__.return_value = mock_client_boom
        mock_client_boom.__aexit__.return_value = False
        mock_client_boom.get.side_effect = ConnectionError("down")

        monkeypatch.setattr(
            httpx, "AsyncClient", lambda *args, **kwargs: mock_client_boom
        )
        assert await health.ollama_model_present("phi3.5") is None
