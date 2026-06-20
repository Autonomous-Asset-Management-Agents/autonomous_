# tests/unit/test_xai_airlock.py
# XAI-1 / XAI-T7 (#1336) — Command-Airlock. Pins: fail-closed PLT-3 gate, actionable-intent
# detection (commands, not questions), NEVER direct execution (block while PLT-3 required;
# frozen draft + MFA once verified), and agent-core enforcement (commands screened BEFORE
# routing). Detection is best-effort, NOT a safety boundary (documented gaps below).
import os
from dataclasses import FrozenInstanceError
from unittest.mock import patch

import allure
import pytest

from core.xai.agent_core import XaiAgentCore, XaiRequest, boot_xai
from core.xai.airlock import (
    CommandAirlock,
    PendingTransaction,
    is_actionable,
    is_plt3_required,
)


@allure.feature("XAI-1 Transparency Window")
@allure.story("Command-Airlock (XAI-T7)")
class TestPlt3Gate:
    def test_required_by_default_fail_closed(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("XAI_REQUIRE_PLT3_AUTH", None)
            assert is_plt3_required() is True

    @pytest.mark.parametrize("val", ["0", "false", "no", "off", "FALSE", " off "])
    def test_explicit_disable_only(self, val):
        with patch.dict(os.environ, {"XAI_REQUIRE_PLT3_AUTH": val}, clear=False):
            assert is_plt3_required() is False

    @pytest.mark.parametrize("val", ["1", "true", "garbage", "", "   ", "disable"])
    def test_anything_else_stays_required(self, val):
        with patch.dict(os.environ, {"XAI_REQUIRE_PLT3_AUTH": val}, clear=False):
            assert is_plt3_required() is True


@allure.feature("XAI-1 Transparency Window")
@allure.story("Command-Airlock (XAI-T7)")
class TestIsActionable:
    @pytest.mark.parametrize(
        "text",
        [
            "sell all my AAPL",
            "Liquidate my portfolio now",
            "Please buy 10 shares of TSLA",
            "close my position",
            "cancel my open order",
            "Could you sell all my AAPL",  # polite-modal command (no '?')
            "Can you sell my AAPL?",  # polite-modal command WITH '?'
            "dump all my AAPL",  # synonym
            "go short NVDA",  # periphrastic
            "get rid of my TSLA",  # periphrastic
        ],
    )
    def test_commands_are_actionable(self, text):
        assert is_actionable(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "why did we sell AAPL?",
            "what's my strategy?",
            "show me my trading history",
            "Should I sell AAPL?",  # advice question, not a command
            "I think we should sell",  # not imperative head
            "Could you explain the strategy?",  # modal question, no action verb
            "",
        ],
    )
    def test_reads_are_not_actionable(self, text):
        assert is_actionable(text) is False


@allure.feature("XAI-1 Transparency Window")
@allure.story("Command-Airlock (XAI-T7)")
class TestKnownDetectionGaps:
    # Documented limitation of keyword detection. ACCEPTABLE: nothing in XAI executes, so a
    # missed command merely routes to a READ provider — never an order. Detection is NOT the
    # safety boundary (the gate + the absence of any execution path are).
    @pytest.mark.parametrize(
        "text", ["move everything to cash", "convert my AAPL to USD"]
    )
    def test_known_misses_are_not_a_safety_hole(self, text):
        assert is_actionable(text) is False


@allure.feature("XAI-1 Transparency Window")
@allure.story("Command-Airlock (XAI-T7)")
class TestScreen:
    def test_read_is_allowed(self):
        d = CommandAirlock().screen("why did we sell AAPL?")
        assert d.kind == "allow"
        assert d.draft is None

    def test_actionable_blocked_while_plt3_required(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("XAI_REQUIRE_PLT3_AUTH", None)  # fail-closed default
            d = CommandAirlock().screen("sell all my AAPL")
        assert d.kind == "blocked"
        assert d.draft is None
        assert "plt-3" in d.message.lower() or "read-only" in d.message.lower()

    def test_actionable_drafts_with_mfa_once_plt3_verified(self):
        with patch.dict(os.environ, {"XAI_REQUIRE_PLT3_AUTH": "0"}, clear=False):
            d = CommandAirlock().screen("sell all my AAPL")
        assert d.kind == "pending_confirmation"
        assert d.draft is not None
        assert d.draft.requires_mfa is True
        assert d.draft.executed is False  # NEVER executed


@allure.feature("XAI-1 Transparency Window")
@allure.story("Command-Airlock (XAI-T7)")
class TestPendingTransactionImmutable:
    def test_default_is_unexecuted_with_mfa(self):
        p = PendingTransaction(raw_request="sell everything")
        assert p.executed is False
        assert p.requires_mfa is True

    def test_executed_cannot_be_constructed(self):
        with pytest.raises(TypeError):
            PendingTransaction(raw_request="x", executed=True)

    def test_draft_is_frozen(self):
        p = PendingTransaction(raw_request="x")
        with pytest.raises(FrozenInstanceError):
            p.executed = True  # type: ignore[misc]


@allure.feature("XAI-1 Transparency Window")
@allure.story("Command-Airlock (XAI-T7)")
class TestAgentCoreEnforcement:
    @pytest.mark.anyio
    async def test_actionable_command_is_blocked_not_routed(self):
        seen = {"n": 0}

        def classifier(text):
            seen["n"] += 1
            return "trading_history"

        with patch.dict(os.environ, {"XAI_AGENT_CORE": "1"}, clear=False):
            os.environ.pop("XAI_REQUIRE_PLT3_AUTH", None)  # fail-closed
            core = XaiAgentCore(providers=boot_xai(None), classifier=classifier)
            resp = await core.handle(XaiRequest(text="sell all my AAPL"))

        assert resp.dormant is False
        assert resp.domain == "command"
        assert resp.payload.kind == "blocked"
        assert seen["n"] == 0  # command short-circuits BEFORE routing

    @pytest.mark.anyio
    async def test_actionable_command_drafts_never_executes_when_gate_open(self):
        seen = {"n": 0}

        def classifier(text):
            seen["n"] += 1
            return "trading_history"

        with patch.dict(
            os.environ,
            {"XAI_AGENT_CORE": "1", "XAI_REQUIRE_PLT3_AUTH": "0"},
            clear=False,
        ):
            core = XaiAgentCore(providers=boot_xai(None), classifier=classifier)
            resp = await core.handle(XaiRequest(text="sell all my AAPL"))

        assert resp.domain == "command"
        assert resp.payload.kind == "pending_confirmation"
        assert resp.payload.draft.executed is False  # still NEVER executed
        assert seen["n"] == 0  # still never routed

    @pytest.mark.anyio
    async def test_read_query_proceeds_to_routing(self):
        seen = {"n": 0}

        def classifier(text):
            seen["n"] += 1
            return None  # unknown -> clarify (proves the read path was reached)

        with patch.dict(os.environ, {"XAI_AGENT_CORE": "1"}, clear=False):
            core = XaiAgentCore(providers=boot_xai(None), classifier=classifier)
            resp = await core.handle(XaiRequest(text="why did we sell AAPL?"))

        assert resp.domain is None
        assert seen["n"] == 1  # classifier WAS called (not blocked by the airlock)


@allure.feature("XAI-1 Transparency Window")
@allure.story("Command-Airlock (XAI-T7)")
class TestImportLight:
    def test_no_torch_pulled(self):
        import subprocess
        import sys

        root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        code = (
            "import sys\n"
            "import core.xai.airlock\n"
            "bad = sorted(m for m in sys.modules if m == 'torch' or m.startswith('torch.'))\n"
            "assert not bad, bad\n"
        )
        r = subprocess.run(
            [sys.executable, "-c", code], cwd=root, capture_output=True, text=True
        )
        assert r.returncode == 0, r.stderr
