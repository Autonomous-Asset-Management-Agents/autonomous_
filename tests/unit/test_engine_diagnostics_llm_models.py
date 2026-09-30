"""TDD (ADR-OBS-01 / PR D): ``llm`` + ``models`` subsystems (VC-1 ML/LLM health).

Invariants under test:
  (a) The LLM timing counters update on a SUCCESSFUL and on a FAILING generate call
      (latency recorded, ok/fail counts move, last-error CLASS name captured).
  (b) SAFETY — the timing/counter instrumentation is PURE OBSERVATION: if the counter
      update itself raises (monkeypatched to blow up), the LLM call STILL returns its
      normal result — the timing failure is swallowed and never perturbs the call.
  (c) The ``llm`` + ``models`` subsystems appear in /engine-diagnostics and are fail-soft
      (a raising collector degrades to ``{"_error": ...}``, endpoint stays 200).
  (d) PRIVACY — no prompt/response TEXT ever appears in the counters or the response
      (machine-only: names, latencies, error CLASS names, counts, booleans, ages).

Auth is bypassed via ``app.dependency_overrides`` (same pattern as test_engine_diagnostics.py).
"""

import json

import pytest
from fastapi.testclient import TestClient

import core.engine.api_routes as api_routes_mod
from core.auth import require_engine_key
from core.engine.api_routes import app


@pytest.fixture
def client_authed():
    app.dependency_overrides[require_engine_key] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _reset_llm_counters():
    from core.llm import telemetry as t

    t.reset_llm_counters()
    yield
    t.reset_llm_counters()


# --- (a) LLM timing counters update on success + failure ----------------------


def test_ollama_success_updates_latency_and_ok_count(monkeypatch):
    """A successful Ollama generate call bumps ok_count + records a latency + ok_ts."""
    import core.llm.provider as provider
    from core.llm.telemetry import get_llm_counters

    class _Resp:
        status_code = 200

        def json(self):
            return {"response": "SECRET-MODEL-REPLY"}

    import httpx

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())
    prov = provider.OllamaProvider(model="llama3.2")
    out = prov.generate_content("SECRET-USER-PROMPT")
    assert out == "SECRET-MODEL-REPLY"

    c = get_llm_counters()
    assert c["llm_ok_count"] == 1
    assert c["llm_fail_count"] == 0
    assert c["llm_last_latency_ms"] is not None
    assert c["llm_last_latency_ms"] >= 0
    assert c["llm_last_ok_ts"] is not None
    assert c["llm_last_error"] is None


def test_gemini_failure_updates_fail_count_and_error_class():
    """A raising Gemini generate call bumps fail_count + records the exception CLASS name."""
    from unittest.mock import MagicMock, patch

    from core.gemini_client import GeminiModelWrapper
    from core.llm.telemetry import get_llm_counters

    with patch("core.gemini_client.new_genai") as mock_genai:
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client
        mock_client.models.generate_content.side_effect = ValueError("boom-secret")
        w = GeminiModelWrapper("gemini-2.5-flash")
        w._client = mock_client
        with pytest.raises(ValueError):
            w.generate_content("SECRET-PROMPT")

    c = get_llm_counters()
    assert c["llm_fail_count"] == 1
    assert c["llm_ok_count"] == 0
    # error is the exception CLASS name only — never the message.
    assert c["llm_last_error"] == "ValueError"
    assert "boom-secret" not in json.dumps(c)


# --- (b) SAFETY: a counter failure must NEVER break the LLM call --------------


def test_counter_failure_does_not_break_ollama_call(monkeypatch):
    """Monkeypatch the counter update to raise → the LLM call STILL returns normally."""
    import core.llm.provider as provider
    import core.llm.telemetry as telemetry

    class _Resp:
        status_code = 200

        def json(self):
            return {"response": "REAL-REPLY"}

    import httpx

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())

    def _sabotage(*a, **k):
        raise RuntimeError("counter blew up")

    # Replace the low-level recorder entirely — even a wholly-broken instrument
    # must not raise into the real generate call.
    monkeypatch.setattr(telemetry, "_record", _sabotage)

    prov = provider.OllamaProvider(model="llama3.2")
    out = prov.generate_content("prompt")
    # The call result is UNCHANGED despite the counter exploding.
    assert out == "REAL-REPLY"


def test_counter_failure_does_not_break_gemini_call(monkeypatch):
    """Even with a sabotaged recorder, a Gemini call still returns its real text."""
    from unittest.mock import MagicMock, patch

    import core.llm.telemetry as telemetry
    from core.gemini_client import GeminiModelWrapper

    def _sabotage(*a, **k):
        raise RuntimeError("counter blew up")

    monkeypatch.setattr(telemetry, "_record", _sabotage)

    with patch("core.gemini_client.new_genai") as mock_genai:
        mock_client = MagicMock()
        mock_genai.Client.return_value = mock_client
        mock_client.models.generate_content.return_value = MagicMock(text="REAL-TEXT")
        w = GeminiModelWrapper("gemini-2.5-flash")
        w._client = mock_client
        assert w.generate_content("prompt") == "REAL-TEXT"


# --- (c) llm + models subsystems appear + fail-soft ---------------------------


def test_llm_and_models_subsystems_present(client_authed):
    body = client_authed.get("/engine-diagnostics").json()
    assert "llm" in body, "missing subsystem: llm"
    assert "models" in body, "missing subsystem: models"
    # llm subsystem carries the machine counters + read-only provider/model fields.
    llm = body["llm"]
    assert "llm_provider" in llm
    assert "llm_ok_count" in llm
    assert "llm_fail_count" in llm
    # models subsystem carries the null-safe strategy readouts + fallback counter.
    models = body["models"]
    assert "lstm_model_loaded" in models
    assert "rl_model_loaded" in models
    assert "ml_fallback_count" in models


def test_llm_subsystem_is_fail_soft(client_authed, monkeypatch):
    def _boom():
        raise RuntimeError("kaboom")

    monkeypatch.setattr(api_routes_mod, "_collect_llm", _boom)
    r = client_authed.get("/engine-diagnostics")
    assert r.status_code == 200
    assert r.json()["llm"] == {"_error": "RuntimeError"}


def test_models_subsystem_is_fail_soft(client_authed, monkeypatch):
    def _boom():
        raise RuntimeError("kaboom")

    monkeypatch.setattr(api_routes_mod, "_collect_models", _boom)
    r = client_authed.get("/engine-diagnostics")
    assert r.status_code == 200
    assert r.json()["models"] == {"_error": "RuntimeError"}


# --- ml_fallback_count is incremented at the model-not-loaded fallback point --


def test_ml_fallback_counter_bumps_on_model_not_loaded():
    """The fail-safe ml_fallback_count moves at the inference model-not-loaded site."""
    from core.strategies import rl_signal
    from core.strategies.rl_signal import get_ml_fallback_count

    before = get_ml_fallback_count()
    rl_signal._bump_ml_fallback()
    assert get_ml_fallback_count() == before + 1


def test_ml_fallback_bump_is_fail_safe(monkeypatch):
    """A broken ml-fallback counter must never raise into the signal path."""
    from core.strategies import rl_signal

    # Sabotage the underlying store; the guarded bump must still swallow the error.
    monkeypatch.setattr(rl_signal, "_ML_FALLBACK", None)
    rl_signal._bump_ml_fallback()  # must not raise


# --- (d) PRIVACY: no prompt/response text anywhere ----------------------------


def test_privacy_no_prompt_or_response_text_in_body(client_authed, monkeypatch):
    """After exercising an LLM call with known secret text, the diagnostics body and the
    counters contain NONE of it — machine-only telemetry."""
    import core.llm.provider as provider

    class _Resp:
        status_code = 200

        def json(self):
            return {"response": "TOP-SECRET-RESPONSE-TEXT"}

    import httpx

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())
    provider.OllamaProvider(model="llama3.2").generate_content("TOP-SECRET-PROMPT-TEXT")

    body = client_authed.get("/engine-diagnostics").json()
    serialized = json.dumps(body)
    assert "TOP-SECRET-PROMPT-TEXT" not in serialized
    assert "TOP-SECRET-RESPONSE-TEXT" not in serialized
    # no api-key / prompt / response text keys leak into the llm subsystem.
    for forbidden in ("prompt", "response_text", "api_key", "GEMINI_API_KEY"):
        assert forbidden not in serialized
