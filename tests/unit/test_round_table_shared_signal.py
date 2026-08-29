# tests/unit/test_round_table_shared_signal.py
# INC (0-decisions ML-gate) — the two ML voices (LSTMSignalAgent, RLConfidenceAgent)
# each fired their OWN concurrent `active.evaluate_for_symbol` (a heavy get_bars+torch
# call on ONE model) inside the runner's asyncio.gather, with NO shared cache. The two
# concurrent calls returned inconsistent results: RL got a signal, the LSTM voice's copy
# came back None ("AI price model returned no signal this cycle") → LSTM abstained → the
# strict-ML gate blocked EVERY decision.
#
# THE FIX: run_round_table evaluates the active strategy ONCE per symbol (sequentially,
# before the gather) and shares it via state["_shared_active_signal"]; both ML voices read
# that shared signal instead of each calling evaluate again. Consistent + halves ML load.
# Shared only in the default get_active() routing; when ROUND_TABLE_DISTINCT_ML_SOURCES
# splits the voices onto distinct sources, the key stays absent and each resolves its own.
#
# Fully deterministic: no network / LLM / GPU / Redis.

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.helpers.config_patch import _patch_get_config

pytestmark = pytest.mark.anyio


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


def _signal(pred: float, action: str = "HOLD"):
    from core.cloud_logger import DecisionContext
    from core.events import SignalEvent

    return SignalEvent(
        symbol="AAPL",
        action=action,
        decision_context=DecisionContext(
            symbol="AAPL", action=action, lstm_prediction=pred
        ),
    )


def _strategy(pred: float, action: str = "HOLD"):
    strat = MagicMock()
    strat.evaluate_for_symbol = AsyncMock(return_value=_signal(pred, action))
    return strat


def _registry(active, by_name: dict | None = None):
    reg = MagicMock()
    reg.get_active.return_value = active
    reg.get.side_effect = lambda name: (by_name or {}).get(name)
    return reg


def _cfg(distinct: bool = False, rank: bool = False):
    return SimpleNamespace(
        ROUND_TABLE_DISTINCT_ML_SOURCES=distinct,
        LSTM_VOTE_USES_CROSS_SECTIONAL_RANK=rank,
        ACTIVE_STRATEGY="RLAgent",
    )


# =========================================================================== #
# 1. _maybe_share_active_signal — evaluate ONCE, share only in the default routing.
# =========================================================================== #
async def test_share_injects_key_and_evaluates_once(monkeypatch):
    from core.round_table import runner as runner_mod

    strat = _strategy(0.7, action="BUY")
    reg = _registry(active=strat)
    _patch_get_config(monkeypatch, _cfg(distinct=False), consumer_modules=[runner_mod])
    with patch.object(runner_mod, "get_global_registry", return_value=reg):
        out = await runner_mod._maybe_share_active_signal(_make_state())

    assert "_shared_active_signal" in out
    assert out["_shared_active_signal"].decision_context.lstm_prediction == 0.7
    strat.evaluate_for_symbol.assert_awaited_once()  # the de-dup: exactly ONE eval


async def test_share_absent_when_distinct(monkeypatch):
    """DISTINCT_ML_SOURCES splits the voices — sharing one get_active() signal would be
    wrong, so the key must stay absent and each voice resolves its own source."""
    from core.round_table import runner as runner_mod

    strat = _strategy(0.7, action="BUY")
    reg = _registry(active=strat)
    _patch_get_config(monkeypatch, _cfg(distinct=True), consumer_modules=[runner_mod])
    with patch.object(runner_mod, "get_global_registry", return_value=reg):
        out = await runner_mod._maybe_share_active_signal(_make_state())

    assert "_shared_active_signal" not in out
    strat.evaluate_for_symbol.assert_not_awaited()


async def test_share_absent_when_registry_missing(monkeypatch):
    """No registry → do NOT share (key absent) so the voices keep their own
    DependencyLost/kill-switch path instead of a silent abstain."""
    from core.round_table import runner as runner_mod

    _patch_get_config(monkeypatch, _cfg(distinct=False), consumer_modules=[runner_mod])
    with patch.object(runner_mod, "get_global_registry", return_value=None):
        out = await runner_mod._maybe_share_active_signal(_make_state())
    assert "_shared_active_signal" not in out


async def test_share_absent_when_eval_raises(monkeypatch):
    """Eval error → key absent (voices fall back), never propagate the exception."""
    from core.round_table import runner as runner_mod

    boom = MagicMock()
    boom.evaluate_for_symbol = AsyncMock(side_effect=RuntimeError("torch blew up"))
    reg = _registry(active=boom)
    _patch_get_config(monkeypatch, _cfg(distinct=False), consumer_modules=[runner_mod])
    with patch.object(runner_mod, "get_global_registry", return_value=reg):
        out = await runner_mod._maybe_share_active_signal(_make_state())
    assert "_shared_active_signal" not in out


async def test_share_key_present_none_when_model_abstains(monkeypatch):
    """A resolvable active that genuinely returns None → key PRESENT with value None,
    so both voices abstain CONSISTENTLY (the whole point — no LSTM/RL divergence)."""
    from core.round_table import runner as runner_mod

    quiet = MagicMock()
    quiet.evaluate_for_symbol = AsyncMock(return_value=None)
    reg = _registry(active=quiet)
    _patch_get_config(monkeypatch, _cfg(distinct=False), consumer_modules=[runner_mod])
    with patch.object(runner_mod, "get_global_registry", return_value=reg):
        out = await runner_mod._maybe_share_active_signal(_make_state())
    assert "_shared_active_signal" in out and out["_shared_active_signal"] is None


# =========================================================================== #
# 3. Both ML voices READ the shared signal (and do not re-evaluate).
# =========================================================================== #
async def test_lstm_votes_from_shared_signal_no_reeval(monkeypatch):
    from core.round_table import agents as agents_mod
    from core.round_table.agents import LSTMSignalAgent

    active = _strategy(2.0, action="BUY")  # if the agent (wrongly) re-evaluated
    reg = _registry(active=active)
    _patch_get_config(monkeypatch, _cfg(), consumer_modules=[agents_mod])

    state = {**_make_state(), "_shared_active_signal": _signal(1.5, action="BUY")}
    with patch("core.round_table.agents.get_global_registry", return_value=reg):
        res = await LSTMSignalAgent().vote(state)

    assert res.weight == pytest.approx(
        0.40
    ), "a present shared prediction is a full vote"
    assert res.score > 0.5, "bullish shared pred → bullish LSTM vote (not 'no signal')"
    assert "no signal" not in res.reasoning.lower()
    active.evaluate_for_symbol.assert_not_awaited()  # used the shared signal, not its own


async def test_rl_votes_from_shared_signal_no_reeval(monkeypatch):
    from core.round_table import agents as agents_mod
    from core.round_table.agents import RLConfidenceAgent

    active = _strategy(2.0, action="BUY")
    reg = _registry(active=active)
    _patch_get_config(monkeypatch, _cfg(), consumer_modules=[agents_mod])

    # RL_CONFIDENCE_WEIGHT now defaults to 0.0 (RL vote muted as the direction-blind
    # interim guard, config.py/config.oss.py) → RLConfidenceAgent freezes
    # default/min/max weight to 0.0 at import, so a live vote clamps to weight 0.0.
    # This test exercises the *counted* RL vote reading the shared signal, which is the
    # RL_CONFIDENCE_WEIGHT>0 path — pin the pre-mute weight on the class so the shared-
    # signal plumbing (bullish shared pred → counted bullish RL vote, no re-eval) is
    # still validated; the weight-0.0 mute default is covered by the RL weight tests.
    monkeypatch.setattr(RLConfidenceAgent, "default_weight", 0.40)
    monkeypatch.setattr(RLConfidenceAgent, "min_weight", 0.15)
    monkeypatch.setattr(RLConfidenceAgent, "max_weight", 1.50)

    state = {**_make_state(), "_shared_active_signal": _signal(1.5, action="BUY")}
    with patch("core.round_table.agents.get_global_registry", return_value=reg):
        res = await RLConfidenceAgent().vote(state)

    assert res.weight > 0.0 and res.score > 0.5
    active.evaluate_for_symbol.assert_not_awaited()


async def test_shared_signal_none_abstains_consistently(monkeypatch):
    """Sentinel PRESENT but value None (the active eval genuinely produced nothing) →
    BOTH voices abstain (weight 0), and must NOT fall back to re-evaluating."""
    from core.round_table import agents as agents_mod
    from core.round_table.agents import LSTMSignalAgent, RLConfidenceAgent

    active = _strategy(2.0, action="BUY")  # present, but must be ignored
    reg = _registry(active=active)
    _patch_get_config(monkeypatch, _cfg(), consumer_modules=[agents_mod])

    state = {**_make_state(), "_shared_active_signal": None}
    with patch("core.round_table.agents.get_global_registry", return_value=reg):
        lstm = await LSTMSignalAgent().vote(state)
        rl = await RLConfidenceAgent().vote(state)

    assert lstm.weight == 0.0
    active.evaluate_for_symbol.assert_not_awaited()


async def test_backcompat_no_key_falls_back_to_own_eval(monkeypatch):
    """No shared key in state (old caller / distinct routing) → the agent resolves and
    evaluates its own active, byte-identical to today."""
    from core.round_table import agents as agents_mod
    from core.round_table.agents import LSTMSignalAgent

    active = _strategy(1.5, action="BUY")
    reg = _registry(active=active)
    _patch_get_config(monkeypatch, _cfg(), consumer_modules=[agents_mod])

    with patch("core.round_table.agents.get_global_registry", return_value=reg):
        res = await LSTMSignalAgent().vote(_make_state())  # no _shared_active_signal

    active.evaluate_for_symbol.assert_awaited_once()
    assert res.weight == pytest.approx(0.40) and res.score > 0.5
