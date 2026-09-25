# tests/unit/test_round_table_ml_sources.py
# ADR-RT-MLSRC-01 — the Round Table's two ML voices must be able to read DISTINCT
# registered models, behind ROUND_TABLE_DISTINCT_ML_SOURCES (dark, default OFF).
#
# THE DEFECT (verified, LIVE — see test_round_table_source_resolution.py + agent_registry.py):
#   LSTMSignalAgent.vote   -> registry.get_active()  (agents.py:879)
#   RLConfidenceAgent.vote -> registry.get_active()  (agents.py:1016)
#   config.ACTIVE_STRATEGY defaults to "RLAgent" (config.oss.py:362 / config.py:246)
#   => BOTH agents read the SAME object (the RL trader). The LSTM voice is actually RL —
#      the collapse the registry's own get() docstring calls out. Observed live:
#      lstm_prediction=0 in every decision.
#
# THE SEAM: AgentRegistry.get(name) already resolves a model BY NAME (the standby
# "LSTMDynamic" ranker is registered at monitor_loop.py:77, set_active=False). This flag
# is the first reader: when ON, LSTMSignalAgent resolves "LSTMDynamic" and RLConfidenceAgent
# resolves the active trader (ACTIVE_STRATEGY, default "RLAgent") — two names, two sources.
#
# ⚠ TRADING-BEHAVIOUR flag: switching an agent's source changes its vote -> changes trading.
# Default OFF (byte-identical), armed only via the desktop _childEnv; LIVE arming needs a
# walk-forward. These tests PROVE both states.
#
# Fully deterministic: no network / LLM / GPU / Redis.

from __future__ import annotations

import importlib.util
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import config
from tests.helpers.config_patch import _patch_get_config

# _LSTM_VOTE_SCALE = 3.6: a strongly negative pred maps well below 0.5, a strongly
# positive one well above — so "bearish source" vs "bullish source" is unambiguous.
_X_BEARISH = -10.0  # the DISTINCT LSTMDynamic source's prediction
_Y_BULLISH = 10.0  # the active RL trader's prediction (action BUY)


def _load_config_oss():
    oss_path = os.path.join(os.path.dirname(config.__file__), "config.oss.py")
    spec = importlib.util.spec_from_file_location("config_oss_ml_sources", oss_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_state(symbol: str = "AAPL") -> dict:
    return {
        "symbol": symbol,
        "ohlc": {
            "open": 150.0,
            "high": 155.0,
            "low": 148.0,
            "close": 152.0,
            "volume": 1_000_000.0,
        },
        "vix": None,
        "market_data_keys": [],
        "current_time": "2026-03-10T06:00:00+00:00",
        "signal": None,
        "error": None,
        "round_table_scores": None,
    }


def _strategy(pred: float, action: str = "HOLD"):
    """A strategy whose evaluate_for_symbol returns a SignalEvent carrying ``pred``
    in decision_context.lstm_prediction — the ALWAYS-set field both ML agents read."""
    from core.cloud_logger import DecisionContext
    from core.events import SignalEvent

    strat = MagicMock()
    strat.evaluate_for_symbol = AsyncMock(
        return_value=SignalEvent(
            symbol="AAPL",
            action=action,
            decision_context=DecisionContext(
                symbol="AAPL", action=action, lstm_prediction=pred
            ),
        )
    )
    return strat


def _registry(active, by_name: dict):
    reg = MagicMock()
    reg.get_active.return_value = active
    reg.get.side_effect = lambda name: by_name.get(name)
    return reg


def _cfg(distinct: bool):
    # Parity with the real get_config() surface the vote paths touch (CI-Prevention R4):
    # the flag under test + the two neighbours LSTM/RL vote() read.
    return SimpleNamespace(
        ROUND_TABLE_DISTINCT_ML_SOURCES=distinct,
        LSTM_VOTE_USES_CROSS_SECTIONAL_RANK=False,
        ACTIVE_STRATEGY="RLAgent",
    )


# =========================================================================== #
# 1. ACTIVATED DEFAULT — the round-table-v2 constellation is now ON by default,
#    both editions (BORA parity). config.py:1254 / config.oss.py:1152 default
#    the env fallback to "True". The OFF path stays reachable via env (see §3).
# =========================================================================== #
def test_flag_default_on_enterprise():
    assert config.get_config().ROUND_TABLE_DISTINCT_ML_SOURCES is True
    assert "ROUND_TABLE_DISTINCT_ML_SOURCES" in config.RuntimeConfigState.model_fields


def test_flag_default_on_oss():
    oss = _load_config_oss()
    assert oss.ROUND_TABLE_DISTINCT_ML_SOURCES is True
    assert oss.get_config().ROUND_TABLE_DISTINCT_ML_SOURCES is True


# =========================================================================== #
# 2. FLAG ON — the two ML voices read DISTINCT sources and now differ.
# =========================================================================== #
@pytest.mark.anyio
async def test_flag_on_lstm_reads_lstmdynamic_rl_reads_rl(monkeypatch):
    """The point of the flag: LSTMSignalAgent votes on the DISTINCT "LSTMDynamic"
    source (bearish X), RLConfidenceAgent on the active RL trader (bullish Y). They
    were one object before; now they diverge."""
    from core.round_table import agents as agents_mod
    from core.round_table.agents import LSTMSignalAgent, RLConfidenceAgent

    rl = _strategy(_Y_BULLISH, action="BUY")
    lstm = _strategy(_X_BEARISH, action="HOLD")
    reg = _registry(active=rl, by_name={"LSTMDynamic": lstm, "RLAgent": rl})
    _patch_get_config(monkeypatch, _cfg(True), consumer_modules=[agents_mod])

    with patch("core.round_table.agents.get_global_registry", return_value=reg):
        lstm_res = await LSTMSignalAgent().vote(_make_state())
        rl_res = await RLConfidenceAgent().vote(_make_state())

    # LSTM reflects X (bearish, the DISTINCT source), NOT Y.
    assert (
        lstm_res.score < 0.5
    ), f"LSTM must reflect the bearish LSTMDynamic source, got {lstm_res.score}"
    assert lstm_res.weight == pytest.approx(
        0.40
    ), "a present prediction is a full-weight vote"
    # RL reflects Y (bullish, the active RL trader).
    assert (
        rl_res.score > 0.5
    ), f"RL must reflect the bullish RL source, got {rl_res.score}"
    # The whole reason for the fix: they are no longer the same number.
    assert lstm_res.score != rl_res.score
    # The distinct source was actually consulted by name.
    assert any(
        c.args and c.args[0] == "LSTMDynamic" for c in reg.get.call_args_list
    ), "LSTM agent must resolve its own model via registry.get('LSTMDynamic')"


# =========================================================================== #
# 3. FLAG OFF — byte-identical to today: both read get_active(); get() untouched.
# =========================================================================== #
@pytest.mark.anyio
async def test_flag_off_both_read_get_active(monkeypatch):
    """OFF (default): both agents resolve get_active() (the RL trader, Y). Even though a
    DISTINCT bearish "LSTMDynamic" is registered, the LSTM agent MUST NOT read it — the
    named lookup is never taken. This is the byte-identical-today contract."""
    from core.round_table import agents as agents_mod
    from core.round_table.agents import LSTMSignalAgent, RLConfidenceAgent

    rl = _strategy(_Y_BULLISH, action="BUY")
    lstm = _strategy(_X_BEARISH, action="HOLD")  # present but must be ignored when OFF
    reg = _registry(active=rl, by_name={"LSTMDynamic": lstm, "RLAgent": rl})
    _patch_get_config(monkeypatch, _cfg(False), consumer_modules=[agents_mod])

    with patch("core.round_table.agents.get_global_registry", return_value=reg):
        lstm_res = await LSTMSignalAgent().vote(_make_state())
        rl_res = await RLConfidenceAgent().vote(_make_state())

    # Both read the active RL source (Y, bullish) — the LSTM voice is RL, as today.
    assert (
        lstm_res.score > 0.5
    ), f"OFF: LSTM must read get_active() (bullish Y), got {lstm_res.score}"
    assert rl_res.score > 0.5
    # The named lookup for a distinct source must never be taken with the flag off.
    assert not any(
        c.args and c.args[0] == "LSTMDynamic" for c in reg.get.call_args_list
    ), "OFF path must not consult registry.get('LSTMDynamic')"


# =========================================================================== #
# 4. ABSTENTION — a missing named source must abstain (weight 0), never a fake vote.
# =========================================================================== #
@pytest.mark.anyio
async def test_flag_on_missing_lstmdynamic_abstains(monkeypatch):
    """Producer not registered yet (get('LSTMDynamic') is None) -> abstain at weight 0,
    NEVER a 0.5-fake at full weight that pollutes the consensus mean."""
    from core.round_table import agents as agents_mod
    from core.round_table.agents import LSTMSignalAgent

    rl = _strategy(_Y_BULLISH, action="BUY")
    reg = _registry(active=rl, by_name={"RLAgent": rl})  # no "LSTMDynamic"
    _patch_get_config(monkeypatch, _cfg(True), consumer_modules=[agents_mod])

    with patch("core.round_table.agents.get_global_registry", return_value=reg):
        res = await LSTMSignalAgent().vote(_make_state())

    assert res.weight == 0.0, "missing LSTMDynamic must abstain (weight 0)"
    assert "abstention" in res.reasoning.lower()


@pytest.mark.anyio
async def test_flag_on_missing_rl_source_abstains(monkeypatch):
    """The RL voice likewise abstains if its named source is absent, rather than
    collapsing back onto whatever get_active() happens to be."""
    from core.round_table import agents as agents_mod
    from core.round_table.agents import RLConfidenceAgent

    lstm = _strategy(_X_BEARISH, action="HOLD")
    # get_active() is a valid strategy, but the RL name resolves to None.
    reg = _registry(active=lstm, by_name={"LSTMDynamic": lstm})  # no "RLAgent"
    _patch_get_config(monkeypatch, _cfg(True), consumer_modules=[agents_mod])

    with patch("core.round_table.agents.get_global_registry", return_value=reg):
        res = await RLConfidenceAgent().vote(_make_state())

    assert res.weight == 0.0, "missing RL source must abstain (weight 0)"
    assert "abstention" in res.reasoning.lower()
