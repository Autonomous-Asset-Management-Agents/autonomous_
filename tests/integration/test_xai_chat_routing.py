# tests/integration/test_xai_chat_routing.py
# XAI-1 / XAI-T9a (#1401) — pins the engine /chat wiring: with XAI_AGENT_CORE on, /chat routes
# through the glass-box core; with it off (default) the path is byte-identical to the legacy
# answer_chat_with_fallback. Calls the chat coroutine directly (bypassing the FastAPI auth
# deps) with a stub engine, so it stays fast and hermetic.
import os
from unittest.mock import patch

import allure
import pytest

import core.engine.api_routes as api

_LEGACY_SENTINEL = "LEGACY_FALLBACK_SHOULD_NOT_APPEAR"


class _StubEngine:
    specialist_registry = None

    def get_chat_context(self):
        return "stub-context"


def _reset(monkeypatch):
    api.engine = _StubEngine()
    monkeypatch.setattr(
        api, "answer_chat_with_fallback", lambda ctx, msg: _LEGACY_SENTINEL
    )


@allure.feature("XAI-1 Transparency Window")
@allure.story("Engine /chat routing (XAI-T9a)")
@pytest.mark.anyio
async def test_chat_routes_through_glass_box_when_enabled(monkeypatch):
    _reset(monkeypatch)
    with patch.dict(os.environ, {"XAI_AGENT_CORE": "1"}, clear=False):
        out = await api.chat({"message": "how do I connect alpaca?"})
    # support domain (StaticFaqSource) answered -> NOT the legacy fallback.
    assert out["reply"] != _LEGACY_SENTINEL
    assert isinstance(out["reply"], str) and out["reply"].strip()


@allure.feature("XAI-1 Transparency Window")
@allure.story("Engine /chat routing (XAI-T9a)")
@pytest.mark.anyio
async def test_chat_byte_identical_fallback_when_disabled(monkeypatch):
    _reset(monkeypatch)
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("XAI_AGENT_CORE", None)
        out = await api.chat({"message": "how do I connect alpaca?"})
    # flag off -> the glass-box core is never consulted; legacy path verbatim.
    assert out["reply"] == _LEGACY_SENTINEL


@allure.feature("XAI-1 Transparency Window")
@allure.story("Engine /chat routing (XAI-T9a)")
@pytest.mark.anyio
async def test_empty_message_short_circuits(monkeypatch):
    _reset(monkeypatch)
    with patch.dict(os.environ, {"XAI_AGENT_CORE": "1"}, clear=False):
        out = await api.chat({"message": "   "})
    assert "ask a question" in out["reply"].lower()


@allure.feature("XAI-1 Transparency Window")
@allure.story("Engine /chat routing (XAI-T9a)")
@pytest.mark.anyio
async def test_specialist_registry_is_read_live_each_call(monkeypatch):
    # The core is rebuilt per call so a registry that appears AFTER warm-up (only set when
    # SPECIALIST_REGISTRY_ENABLED) is picked up — a cached core would pin the warm-up None.
    _reset(monkeypatch)
    seen = []
    real_boot = api.boot_xai_runtime

    def _spy(**kwargs):
        seen.append(kwargs.get("specialist_registry"))
        return real_boot(**kwargs)

    monkeypatch.setattr(api, "boot_xai_runtime", _spy)
    later_registry = object()
    with patch.dict(os.environ, {"XAI_AGENT_CORE": "1"}, clear=False):
        api.engine.specialist_registry = None
        await api.chat({"message": "how do I connect alpaca?"})
        api.engine.specialist_registry = later_registry
        await api.chat({"message": "how do I connect alpaca?"})
    assert seen == [None, later_registry]


@allure.feature("XAI-1 Transparency Window")
@allure.story("Engine /chat routing (XAI-T9a)")
@pytest.mark.anyio
async def test_xai_path_error_falls_back_to_legacy(monkeypatch):
    # A raise on the XAI path must degrade to the legacy chat, never to the generic
    # error reply — flag-on is never worse than flag-off.
    _reset(monkeypatch)

    async def _boom(message, *, core):
        raise RuntimeError("xai exploded")

    monkeypatch.setattr(api, "answer_via_xai", _boom)
    with patch.dict(os.environ, {"XAI_AGENT_CORE": "1"}, clear=False):
        out = await api.chat({"message": "how do I connect alpaca?"})
    assert out["reply"] == _LEGACY_SENTINEL
