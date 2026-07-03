# tests/unit/test_xai_agent_core.py
# XAI-1 / XAI-T1 (#1330) — Agent-Core skeleton. Pins the guarantees this module exists for:
# edition-gated DI, LATCHED flag-gated dormancy, fail-closed routing, fail-safe unknown
# intent, async-or-sync classifier, pure pass-through, and import-lightness (no torch).
import os
import subprocess
import sys
from unittest.mock import patch

import allure
import pytest

from core.xai.agent_core import (
    Edition,
    XaiAgentCore,
    XaiProviderUnavailable,
    XaiRequest,
    XaiResponse,
    boot_xai,
    is_agent_core_enabled,
    resolve_edition,
)
from core.xai.interfaces import (
    IDomainProvider,
    IExplainabilitySource,
    IFaqSource,
    ISenateLogReader,
    ISpecialistReportSource,
)


class _StubProvider(IDomainProvider):
    def __init__(self, payload):
        self._payload = payload
        self.calls = 0

    async def answer(self, request):
        self.calls += 1
        return self._payload


def _core(providers=None, classifier=lambda t: "trading_history", *, enabled):
    """Construct a core with the dormancy flag LATCHED to `enabled` at construction time."""
    with patch.dict(os.environ, {}, clear=False):
        if enabled:
            os.environ["XAI_AGENT_CORE"] = "1"
        else:
            os.environ.pop("XAI_AGENT_CORE", None)
        return XaiAgentCore(
            providers=providers or boot_xai(None), classifier=classifier
        )


@allure.feature("XAI-1 Transparency Window")
@allure.story("Agent-Core (XAI-T1)")
class TestEditionResolution:
    def test_oss_without_license(self):
        assert resolve_edition(None) is Edition.OSS
        assert resolve_edition("") is Edition.OSS

    def test_blank_license_is_oss(self):
        # whitespace-only must NOT resolve to ENTERPRISE (entitlement fail-open).
        assert resolve_edition("   ") is Edition.OSS

    def test_enterprise_with_license(self):
        assert resolve_edition("ent-key") is Edition.ENTERPRISE


@allure.feature("XAI-1 Transparency Window")
@allure.story("Agent-Core (XAI-T1)")
class TestFlagParsing:
    @pytest.mark.parametrize("val", ["1", "true", "TRUE", " on ", "yes", "Yes"])
    def test_truthy_enables(self, val):
        with patch.dict(os.environ, {"XAI_AGENT_CORE": val}, clear=False):
            assert is_agent_core_enabled() is True

    @pytest.mark.parametrize("val", ["", "0", "false", "no", "off", "2", "enabled"])
    def test_falsey_stays_dormant(self, val):
        with patch.dict(os.environ, {"XAI_AGENT_CORE": val}, clear=False):
            assert is_agent_core_enabled() is False

    def test_unset_is_dormant(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("XAI_AGENT_CORE", None)
            assert is_agent_core_enabled() is False


@allure.feature("XAI-1 Transparency Window")
@allure.story("Agent-Core (XAI-T1)")
class TestProviderInterfacesAreAbstract:
    @pytest.mark.parametrize(
        "iface",
        [
            IDomainProvider,
            ISenateLogReader,
            ISpecialistReportSource,
            IExplainabilitySource,
            IFaqSource,
        ],
    )
    def test_cannot_instantiate(self, iface):
        with pytest.raises(TypeError):
            iface()


@allure.feature("XAI-1 Transparency Window")
@allure.story("Agent-Core (XAI-T1)")
class TestProviderRegistry:
    def test_records_edition(self):
        assert boot_xai(None).edition is Edition.OSS
        assert boot_xai("k").edition is Edition.ENTERPRISE

    def test_unconfigured_provider_fails_closed(self):
        with pytest.raises(XaiProviderUnavailable):
            boot_xai(None).require("trading_history")

    def test_register_then_require_and_get(self):
        providers = boot_xai(None)
        stub = _StubProvider(payload=[{"x": 1}])
        assert providers.get("trading_history") is None
        providers.register("trading_history", stub)
        assert providers.require("trading_history") is stub
        assert providers.get("trading_history") is stub

    def test_register_unknown_domain_raises(self):
        with pytest.raises(ValueError):
            boot_xai(None).register("not_a_domain", _StubProvider(payload=1))

    def test_register_rejects_non_provider(self):
        with pytest.raises(TypeError):
            boot_xai(None).register("support", object())  # not an IDomainProvider


@allure.feature("XAI-1 Transparency Window")
@allure.story("Agent-Core (XAI-T1)")
class TestDormancyLatched:
    @pytest.mark.anyio
    async def test_dormant_by_default_does_not_classify(self):
        seen = {"n": 0}

        def classifier(text):
            seen["n"] += 1
            return "trading_history"

        core = _core(classifier=classifier, enabled=False)
        resp = await core.handle(XaiRequest(text="why sell AAPL?"))

        assert isinstance(resp, XaiResponse)
        assert resp.dormant is True
        assert seen["n"] == 0  # never routed while dormant

    @pytest.mark.anyio
    async def test_latched_dormant_ignores_later_env_enable(self):
        core = _core(enabled=False)  # latched dormant
        with patch.dict(os.environ, {"XAI_AGENT_CORE": "1"}, clear=False):
            resp = await core.handle(XaiRequest(text="why sell AAPL?"))
        assert resp.dormant is True  # not woken by the late env flip

    @pytest.mark.anyio
    async def test_latched_enabled_ignores_later_env_disable(self):
        providers = boot_xai(None)
        providers.register("trading_history", _StubProvider(payload=[{"a": 1}]))
        core = _core(providers=providers, enabled=True)  # latched enabled
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("XAI_AGENT_CORE", None)
            resp = await core.handle(XaiRequest(text="why sell AAPL?"))
        assert resp.dormant is False
        assert resp.domain == "trading_history"


@allure.feature("XAI-1 Transparency Window")
@allure.story("Agent-Core (XAI-T1)")
class TestRouting:
    @pytest.mark.anyio
    async def test_routes_to_classified_domain(self):
        providers = boot_xai(None)
        stub = _StubProvider(payload=[{"symbol": "AAPL", "action": "SELL"}])
        providers.register("trading_history", stub)
        core = _core(
            providers=providers, classifier=lambda t: "trading_history", enabled=True
        )
        resp = await core.handle(XaiRequest(text="why sell AAPL?"))
        assert resp.dormant is False
        assert resp.domain == "trading_history"
        assert resp.payload == [{"symbol": "AAPL", "action": "SELL"}]
        assert stub.calls == 1

    @pytest.mark.anyio
    async def test_async_classifier_is_awaited(self):
        providers = boot_xai(None)
        providers.register("support", _StubProvider(payload="ok"))

        async def aclassifier(text):
            return "support"

        core = _core(providers=providers, classifier=aclassifier, enabled=True)
        resp = await core.handle(XaiRequest(text="help"))
        assert resp.domain == "support"
        assert resp.payload == "ok"

    @pytest.mark.anyio
    @pytest.mark.parametrize("bad", ["garbage", None])
    async def test_unknown_or_none_intent_is_fail_safe(self, bad):
        core = _core(classifier=lambda t: bad, enabled=True)
        resp = await core.handle(XaiRequest(text="??"))
        assert resp.dormant is False
        assert resp.domain is None
        assert resp.text  # a clarify message, not an exception

    @pytest.mark.anyio
    async def test_enabled_but_unconfigured_domain_fails_closed(self):
        core = _core(classifier=lambda t: "stock_research", enabled=True)
        with pytest.raises(XaiProviderUnavailable):
            await core.handle(XaiRequest(text="fundamentals?"))

    @pytest.mark.anyio
    @pytest.mark.parametrize("empty", [None, [], {}])
    async def test_router_passes_through_empty_provider_result(self, empty):
        # Faithful pass-through: a legitimately-empty provider result ("no data") is
        # returned as-is (domain set, dormant False) — NOT coerced into an error.
        providers = boot_xai(None)
        providers.register("trading_history", _StubProvider(payload=empty))
        core = _core(
            providers=providers, classifier=lambda t: "trading_history", enabled=True
        )
        resp = await core.handle(XaiRequest(text="history?"))
        assert resp.dormant is False
        assert resp.domain == "trading_history"
        assert resp.payload == empty


@allure.feature("XAI-1 Transparency Window")
@allure.story("Agent-Core (XAI-T1)")
class TestImportLight:
    def test_no_torch_pulled(self):
        # Importing the agent-core must not drag in a heavy ML runtime (torch).
        root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        code = (
            "import sys\n"
            "import core.xai.agent_core, core.xai.interfaces\n"
            "bad = sorted(m for m in sys.modules if m == 'torch' or m.startswith('torch.'))\n"
            "assert not bad, bad\n"
        )
        r = subprocess.run(
            [sys.executable, "-c", code], cwd=root, capture_output=True, text=True
        )
        assert r.returncode == 0, r.stderr
