# tests/unit/test_xai_runtime.py
# XAI-1 / XAI-T9a (#1401) — OSS agent-core composition. Pins that boot_xai_runtime wires all
# four concrete OSS read-seams (T3..T6) behind their domain providers, builds the
# deterministic-first IntentRouter (T2) as classifier, and returns a routable, flag-gated
# XaiAgentCore (T1) — the seam that makes /chat answer via the glass box.
import os
from unittest.mock import patch

import allure
import pytest

from core.xai.agent_core import DOMAINS, Edition, XaiAgentCore, XaiRequest, XaiResponse
from core.xai.interfaces import IDomainProvider
from core.xai.runtime import (
    answer_via_xai,
    boot_xai_runtime,
    build_oss_providers,
    render_response,
)


@allure.feature("XAI-1 Transparency Window")
@allure.story("Agent-Core composition (XAI-T9a)")
class TestOssProviderComposition:
    def test_registers_all_four_domains(self):
        providers = build_oss_providers()
        for domain in DOMAINS:
            assert isinstance(providers.require(domain), IDomainProvider)

    def test_edition_is_oss_without_license(self):
        assert build_oss_providers().edition is Edition.OSS


@allure.feature("XAI-1 Transparency Window")
@allure.story("Agent-Core composition (XAI-T9a)")
class TestRuntimeFactory:
    def test_boot_returns_enabled_core_when_flag_on(self):
        with patch.dict(os.environ, {"XAI_AGENT_CORE": "1"}, clear=False):
            core = boot_xai_runtime()
        assert isinstance(core, XaiAgentCore)
        assert core.enabled is True

    def test_boot_dormant_by_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("XAI_AGENT_CORE", None)
            core = boot_xai_runtime()
        assert core.enabled is False

    @pytest.mark.anyio
    async def test_support_query_routes_to_support_domain(self):
        # support's StaticFaqSource is always populated -> a crash-free end-to-end proof that
        # composition + deterministic routing + provider wiring all hang together.
        with patch.dict(os.environ, {"XAI_AGENT_CORE": "1"}, clear=False):
            core = boot_xai_runtime()
        resp = await core.handle(XaiRequest(text="how do I connect alpaca?"))
        assert resp.dormant is False
        assert resp.domain == "support"
        assert isinstance(resp.payload, dict)

    @pytest.mark.anyio
    async def test_history_query_degrades_without_crash_on_empty_log(self):
        # trading_history over an absent senate log must degrade to a dict, never crash.
        env = {"XAI_AGENT_CORE": "1", "SENATE_LOG_DIR": "/nonexistent_xai_t9a_dir"}
        with patch.dict(os.environ, env, clear=False):
            core = boot_xai_runtime(senate_log_dir="/nonexistent_xai_t9a_dir")
        resp = await core.handle(XaiRequest(text="who voted on the last decision?"))
        assert resp.dormant is False
        assert resp.domain == "trading_history"
        assert isinstance(resp.payload, dict)


@allure.feature("XAI-1 Transparency Window")
@allure.story("Engine /chat bridge (XAI-T9a)")
class TestRenderResponse:
    def test_domain_answer_uses_payload_text(self):
        resp = XaiResponse(
            dormant=False, domain="support", payload={"text": "Connect Alpaca like so."}
        )
        assert render_response(resp) == "Connect Alpaca like so."

    def test_clarify_uses_response_text(self):
        resp = XaiResponse(
            dormant=False, domain=None, text="Could not determine intent — rephrase."
        )
        assert render_response(resp) == "Could not determine intent — rephrase."

    def test_airlock_block_uses_response_text(self):
        # airlock-block carries its message in .text and a decision object in .payload;
        # the user must see the block message, never the raw decision.
        resp = XaiResponse(
            dormant=False,
            domain="command",
            text="Blocked: actions require PLT-3 verification.",
            payload=object(),
        )
        assert render_response(resp) == "Blocked: actions require PLT-3 verification."

    def test_domain_payload_without_text_falls_back_to_safe_string(self):
        resp = XaiResponse(dormant=False, domain="support", payload={"hits": []})
        out = render_response(resp)
        assert isinstance(out, str) and out.strip()


@allure.feature("XAI-1 Transparency Window")
@allure.story("Engine /chat bridge (XAI-T9a)")
class TestAnswerViaXai:
    @pytest.mark.anyio
    async def test_dormant_core_returns_none_for_legacy_fallback(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("XAI_AGENT_CORE", None)
            core = boot_xai_runtime()
        assert await answer_via_xai("anything at all", core=core) is None

    @pytest.mark.anyio
    async def test_enabled_core_returns_rendered_text(self):
        with patch.dict(os.environ, {"XAI_AGENT_CORE": "1"}, clear=False):
            core = boot_xai_runtime()
        out = await answer_via_xai("how do I connect alpaca?", core=core)
        assert isinstance(out, str) and out.strip()
