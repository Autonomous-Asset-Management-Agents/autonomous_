# tests/unit/test_strict_ml_gate_decouple.py
# RTR-6 (#2410) — Gatekeeper-ML-Entkopplung: flag-gated Strict-ML-Gate (Option B).
# TDD Red -> Green. Plan = issue #2410 (plan-approved 2026-07-23).
#
# Gherkin (issue #2410 §4):
#   - Given GATEKEEPER_STRICT_ML_REQUIRES_RL=True (default), Then today's behaviour
#     exactly (BOTH core ML votes required) — test-pinned, byte-identical.
#   - Given Flag=False, LSTM votes weight>0, RL abstains, Then the gate passes; a
#     once-per-session WARNING (§5.6) is logged; the audit reasoning names
#     "RL not required (flag)".
#   - Given Flag=False and BOTH LSTM and RL abstain, Then approved=False (the
#     no-ML protection never lifts).
#
# Async interface (run_round_table / gatekeeper.check) mocked via AsyncMock
# (AGENTS.md Rule 2 / CLAUDE.md §5.2).

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.round_table.base_agent import VoteResult
from core.round_table.gatekeeper import GatekeeperDecision

_AI_BOT = Path(__file__).resolve().parents[2]  # ai_trading_bot/
_OSS_CONFIG = _AI_BOT / "config.oss.py"

_FLAG = "GATEKEEPER_STRICT_ML_REQUIRES_RL"


def _load_oss_config():
    spec = importlib.util.spec_from_file_location("config_oss_strict_ml", _OSS_CONFIG)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _reset_rl_waiver_latch():
    """The once-per-session WARNING latch is module-global — reset around each test."""
    import core.round_table.runner as runner

    runner._STRICT_ML_RL_WAIVED_WARNED = False
    yield
    runner._STRICT_ML_RL_WAIVED_WARNED = False


class _MockConfig(SimpleNamespace):
    def __getattr__(self, name: str):
        return False


def _cfg(requires_rl: bool) -> _MockConfig:
    return _MockConfig(
        GATEKEEPER_STRICT_ML_REQUIRES_RL=requires_rl,
        GATEKEEPER_REQUIRE_CONTEXT=False,
        SHADOW_TFT_VOTE_ENABLED=False,
        DRAWDOWN_GUARD_CONDITIONER_ENABLED=False,
        META_LABEL_FILTER_ENABLED=False,
        META_LABEL_REQUIRE_MODEL=False,
    )


# --------------------------------------------------------------------------- #
# 1. BORA parity: the flag exists in BOTH editions. Round-table-v2 activation
#    flipped the default to False (RL no longer required; env default "False"),
#    identically in both editions (byte-identical BORA parity).
# --------------------------------------------------------------------------- #
def test_flag_default_false_both_editions(monkeypatch):
    monkeypatch.delenv(_FLAG, raising=False)
    import config as enterprise

    ent = enterprise.get_config()
    oss = _load_oss_config().get_config()
    assert getattr(ent, _FLAG) is False, "config.py default must be False (activated)"
    assert getattr(oss, _FLAG) is False, "config.oss.py default must be False (BORA)"


# --------------------------------------------------------------------------- #
# 2. Default (flag True): BOTH ML votes required — byte-identical pin
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "lstm_valid,rl_valid,expected_blocked",
    [
        (True, True, False),  # both vote -> gate passes (today)
        (True, False, True),  # RL abstains -> blocked (today)
        (False, True, True),  # LSTM abstains -> blocked (today)
        (False, False, True),  # nobody votes -> blocked (today)
    ],
)
def test_gate_default_requires_both_ml_votes(lstm_valid, rl_valid, expected_blocked):
    from core.round_table import runner

    with patch("config.get_config", return_value=_cfg(requires_rl=True)):
        assert runner._strict_ml_gate_blocks(lstm_valid, rl_valid) is expected_blocked


# --------------------------------------------------------------------------- #
# 3. Flag False: a valid LSTM vote alone passes; LSTM stays mandatory
# --------------------------------------------------------------------------- #
def test_gate_flag_false_lstm_alone_passes(caplog):
    from core.round_table import runner

    with caplog.at_level(logging.WARNING), patch(
        "config.get_config", return_value=_cfg(requires_rl=False)
    ):
        assert runner._strict_ml_gate_blocks(True, False) is False

    warned = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "RL not required" in r.message
    ]
    assert len(warned) == 1, "§5.6: waived RL must be WARNING-logged (never DEBUG)"


@pytest.mark.parametrize(
    "lstm_valid,rl_valid",
    [
        (False, False),  # BOTH abstain -> protection stays (issue #2410 scenario 3)
        (False, True),  # LSTM abstains -> RL alone never satisfies the gate
    ],
)
def test_gate_flag_false_missing_lstm_still_blocks(lstm_valid, rl_valid):
    from core.round_table import runner

    with patch("config.get_config", return_value=_cfg(requires_rl=False)):
        assert runner._strict_ml_gate_blocks(lstm_valid, rl_valid) is True


# --------------------------------------------------------------------------- #
# 4. The waiver WARNING is rate-limited: once per session, not per symbol
# --------------------------------------------------------------------------- #
def test_rl_waiver_warning_once_per_session(caplog):
    from core.round_table import runner

    with caplog.at_level(logging.WARNING), patch(
        "config.get_config", return_value=_cfg(requires_rl=False)
    ):
        assert runner._strict_ml_gate_blocks(True, False) is False
        assert runner._strict_ml_gate_blocks(True, False) is False
        assert runner._strict_ml_gate_blocks(True, False) is False

    warned = [r for r in caplog.records if "RL not required" in r.message]
    assert len(warned) == 1, "WARNING must fire exactly once per session"


# --------------------------------------------------------------------------- #
# run_round_table wiring (E2E through the real gate code path)
# --------------------------------------------------------------------------- #
def _vote(agent_name: str, symbol: str, weight: float) -> VoteResult:
    return VoteResult(
        agent_name=agent_name,
        symbol=symbol,
        score=0.5,
        weight=weight,
        reasoning=f"{agent_name} unit-test vote",
    )


def _mk_state(symbol: str = "AAPL") -> dict:
    return {
        "symbol": symbol,
        "ohlc": {
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 1000.0,
        },
        "signal": None,
        "error": None,
        "round_table_scores": None,
        "consensus_ranking": None,
    }


def _agent_pair(symbol: str, rl_weight: float):
    """Real LSTM/RL class instances (isinstance()-checked in the runner, I-3 #944)
    without their heavy __init__ — vote() replaced by AsyncMock (Rule 2)."""
    from core.round_table import runner

    lstm = object.__new__(runner.LSTMSignalAgent)
    lstm.vote = AsyncMock(return_value=_vote("LSTMSignalAgent", symbol, 1.0))
    rl = object.__new__(runner.RLConfidenceAgent)
    rl.vote = AsyncMock(return_value=_vote("RLConfidenceAgent", symbol, rl_weight))
    return lstm, rl


def _wire_runner(monkeypatch, symbol: str, rl_weight: float):
    from core.round_table import runner

    lstm, rl = _agent_pair(symbol, rl_weight)
    consensus = MagicMock()
    consensus.check_distribution.return_value = (True, "ok")
    consensus.aggregate.return_value = 0.5  # HOLD band — signal path stays trivial
    gatekeeper = MagicMock()
    gatekeeper.check = AsyncMock(
        return_value=GatekeeperDecision(
            approved=True,
            reason="AllChecksPassed: PDT, concentration and daily limit OK",
            symbol=symbol,
        )
    )
    senate = MagicMock()
    senate.log_session = AsyncMock()
    monkeypatch.setattr(runner, "_consensus_engine", consensus)
    monkeypatch.setattr(runner, "_gatekeeper", gatekeeper)
    monkeypatch.setattr(runner, "_senate", senate)
    monkeypatch.setattr(runner, "_active_agents", [lstm, rl])
    return runner, senate


def _patch_get_config(monkeypatch, requires_rl: bool):
    cfg_obj = _cfg(requires_rl=requires_rl)
    import sys

    for mod_name, mod in list(sys.modules.items()):
        if mod_name == "config" or mod_name.endswith(".config"):
            if mod is not None and not mod_name.startswith("pydantic"):
                try:
                    if hasattr(mod, "get_config"):
                        monkeypatch.setattr(mod, "get_config", lambda: cfg_obj)
                except Exception:
                    pass


@pytest.mark.anyio
async def test_run_round_table_lstm_only_passes_and_annotates_audit(monkeypatch):
    """Flag=False + RL abstain (weight 0) -> approved, reasoning names the waiver."""
    symbol = "AAPL"
    runner, senate = _wire_runner(monkeypatch, symbol, rl_weight=0.0)
    _patch_get_config(monkeypatch, requires_rl=False)

    with patch("core.decision_capture.capture.attach_round_table_capture_fields"):
        out = await runner.run_round_table(_mk_state(symbol))

    assert out.get("error") is None
    session = senate.log_session.call_args.args[0]
    assert session.gatekeeper_approved is True
    assert "RL not required (flag)" in session.gatekeeper_reason


@pytest.mark.anyio
async def test_run_round_table_default_still_blocks_on_rl_abstain(monkeypatch):
    """Regression pin (byte-identical default): flag True + RL abstain -> veto with
    the EXACT pre-#2410 reason string."""
    symbol = "AAPL"
    runner, senate = _wire_runner(monkeypatch, symbol, rl_weight=0.0)
    _patch_get_config(monkeypatch, requires_rl=True)

    with patch("core.decision_capture.capture.attach_round_table_capture_fields"):
        out = await runner.run_round_table(_mk_state(symbol))

    assert out.get("error") is None
    session = senate.log_session.call_args.args[0]
    assert session.gatekeeper_approved is False
    assert (
        session.gatekeeper_reason
        == "Missing core ML votes (LSTM/RL failed or excluded)"
    )


@pytest.mark.anyio
async def test_run_round_table_flag_false_both_abstain_stays_vetoed(monkeypatch):
    """Flag=False + BOTH ML votes abstain -> approved=False (protection stays)."""
    symbol = "AAPL"
    from core.round_table import runner as _r
    from core.round_table.agents import LSTMSignalAgent, RLConfidenceAgent

    lstm = object.__new__(LSTMSignalAgent)
    lstm.vote = AsyncMock(return_value=_vote("LSTMSignalAgent", symbol, 0.0))
    rl = object.__new__(RLConfidenceAgent)
    rl.vote = AsyncMock(return_value=_vote("RLConfidenceAgent", symbol, 0.0))

    runner, senate = _wire_runner(monkeypatch, symbol, rl_weight=0.0)
    monkeypatch.setattr(runner, "_active_agents", [lstm, rl])
    _patch_get_config(monkeypatch, requires_rl=False)

    with patch("core.decision_capture.capture.attach_round_table_capture_fields"):
        out = await runner.run_round_table(_mk_state(symbol))

    assert out.get("error") is None
    session = senate.log_session.call_args.args[0]
    assert session.gatekeeper_approved is False
    assert "Missing core ML votes" in session.gatekeeper_reason
