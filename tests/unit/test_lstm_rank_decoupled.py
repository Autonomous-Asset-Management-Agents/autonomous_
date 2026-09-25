# tests/unit/test_lstm_rank_decoupled.py
# INC (ML-gate outage) — decouple the cross-sectional RANK vote from the per-symbol
# evaluate_for_symbol signal.
#
# THE LIVE DEFECT (verified on the running desktop engine):
#   The desktop shell arms ROUND_TABLE_DISTINCT_ML_SOURCES=True AND
#   LSTM_VOTE_USES_CROSS_SECTIONAL_RANK=True. In distinct-ML-sources mode
#   LSTMSignalAgent.vote resolves its source via registry.get("LSTMDynamic"); that
#   ranker's evaluate_for_symbol(symbol) returns None per symbol (the symbol is not in
#   its per-symbol cache). In vote(), the "signal is None" abstain
#   ("AI price model returned no signal this cycle", weight 0) sat UPSTREAM of the
#   cross-sectional-rank branch, so the rank vote was NEVER reached. The LSTM voice
#   abstained -> the strict-ML gate blocked EVERY decision.
#
# THE FIX: the cross-sectional RANK is a PANEL property (lstm_panel_store), independent
# of any per-symbol evaluate signal. When LSTM_VOTE_USES_CROSS_SECTIONAL_RANK is ON, the
# LSTM voice must vote via _vote_on_rank(...) EVEN IF the resolved source returned None.
# The signal (if present) enriches only the reasoning string; the score/weight come from
# the panel standing and its existing abstain guards (thin panel / symbol absent).
#
# Fully deterministic: no network / LLM / GPU / Redis. Only the real panel store runs.

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.report.lstm_panel_store import get_store
from tests.helpers.config_patch import _patch_get_config

_TEST_SYMBOL = "S017"


def _cfg(*, distinct: bool, rank: bool) -> SimpleNamespace:
    """Parity with the get_config() surface the LSTM vote path reads."""
    return SimpleNamespace(
        ROUND_TABLE_DISTINCT_ML_SOURCES=distinct,
        LSTM_VOTE_USES_CROSS_SECTIONAL_RANK=rank,
        ACTIVE_STRATEGY="RLAgent",
    )


def _none_source():
    """A resolved source whose per-symbol evaluate_for_symbol returns None — the exact
    live behaviour of the standby LSTMDynamic ranker for a symbol not in its cache."""
    strat = MagicMock()
    strat.evaluate_for_symbol = AsyncMock(return_value=None)
    return strat


def _signal_source(pred: float, action: str = "HOLD"):
    """A source whose evaluate returns a SignalEvent carrying ``pred`` in
    decision_context.lstm_prediction (the always-set field the vote reads)."""
    from core.cloud_logger import DecisionContext
    from core.events import SignalEvent

    strat = MagicMock()
    strat.evaluate_for_symbol = AsyncMock(
        return_value=SignalEvent(
            symbol=_TEST_SYMBOL,
            action=action,
            decision_context=DecisionContext(
                symbol=_TEST_SYMBOL, action=action, lstm_prediction=pred
            ),
        )
    )
    return strat


def _registry(active, by_name: dict):
    reg = MagicMock()
    reg.get_active.return_value = active
    reg.get.side_effect = lambda name: by_name.get(name)
    return reg


def _make_state(symbol: str = _TEST_SYMBOL) -> dict:
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


def _seed_broad_panel(symbol: str) -> None:
    """A BROAD, DISPERSED panel (>= _MIN_PANEL_SYMBOLS, >= 2 distinct scores) that
    INCLUDES ``symbol`` — recorded under today's UTC date (what _vote_on_rank reads)."""
    ranked = [(f"P{i:03d}", float(i) * 0.05 - 1.0) for i in range(34)]
    ranked.append((symbol, 0.42))
    get_store().reset()
    get_store().record_cycle(datetime.now(timezone.utc).date(), ranked)


def _seed_thin_panel(symbol: str) -> None:
    """A thin panel (n < _MIN_PANEL_SYMBOLS): the guard inside _vote_on_rank must
    abstain (weight 0) rather than dress a thin panel as a universe standing."""
    ranked = [(f"P{i:03d}", float(i)) for i in range(4)]
    ranked.append((symbol, 2.5))
    get_store().reset()
    get_store().record_cycle(datetime.now(timezone.utc).date(), ranked)


# =========================================================================== #
# 1. THE LIVE BUG — rank mode + a source whose evaluate returns None + a broad
#    panel: the vote MUST fire on the panel standing (weight > 0, RANK reasoning),
#    NOT abstain "no signal". This is RED before the fix.
# =========================================================================== #
@pytest.mark.anyio
async def test_rank_mode_none_signal_broad_panel_votes_on_rank(monkeypatch):
    from core.round_table import agents as agents_mod
    from core.round_table.agents import LSTMSignalAgent

    src = _none_source()
    reg = _registry(active=src, by_name={"LSTMDynamic": src})
    _patch_get_config(
        monkeypatch, _cfg(distinct=True, rank=True), consumer_modules=[agents_mod]
    )
    _seed_broad_panel(_TEST_SYMBOL)
    try:
        with patch("core.round_table.agents.get_global_registry", return_value=reg):
            res = await LSTMSignalAgent().vote(_make_state())
    finally:
        get_store().reset()

    assert res.weight > 0.0, (
        "rank mode + broad panel must vote on the cross-sectional standing even when "
        f"the per-symbol signal is None, got weight={res.weight}"
    )
    assert 0.0 <= res.score <= 1.0
    assert "rank" in res.reasoning.lower(), res.reasoning
    assert "no signal" not in res.reasoning.lower(), res.reasoning
    assert "EXCLUDED" not in res.reasoning, res.reasoning


# =========================================================================== #
# 2. GUARD PRESERVED — rank mode + a THIN panel must still abstain (weight 0),
#    now via _vote_on_rank's own guard ("EXCLUDED"), not the old "no signal".
# =========================================================================== #
@pytest.mark.anyio
async def test_rank_mode_thin_panel_abstains(monkeypatch):
    from core.round_table import agents as agents_mod
    from core.round_table.agents import LSTMSignalAgent

    src = _none_source()
    reg = _registry(active=src, by_name={"LSTMDynamic": src})
    _patch_get_config(
        monkeypatch, _cfg(distinct=True, rank=True), consumer_modules=[agents_mod]
    )
    _seed_thin_panel(_TEST_SYMBOL)
    try:
        with patch("core.round_table.agents.get_global_registry", return_value=reg):
            res = await LSTMSignalAgent().vote(_make_state())
    finally:
        get_store().reset()

    assert res.weight == 0.0, "a thin panel must abstain (guard preserved)"
    assert "EXCLUDED" in res.reasoning, res.reasoning


# =========================================================================== #
# 3. FLAG OFF (per-symbol mode) + signal None -> byte-identical to today:
#    still abstains "AI price model returned no signal this cycle" (weight 0).
# =========================================================================== #
@pytest.mark.anyio
async def test_flag_off_none_signal_still_abstains_no_signal(monkeypatch):
    from core.round_table import agents as agents_mod
    from core.round_table.agents import LSTMSignalAgent

    src = _none_source()
    # distinct OFF -> get_active(); a broad panel is present but must be IGNORED
    # in per-symbol mode (the rank path must not be taken with the flag off).
    reg = _registry(active=src, by_name={})
    _patch_get_config(
        monkeypatch, _cfg(distinct=False, rank=False), consumer_modules=[agents_mod]
    )
    _seed_broad_panel(_TEST_SYMBOL)
    try:
        with patch("core.round_table.agents.get_global_registry", return_value=reg):
            res = await LSTMSignalAgent().vote(_make_state())
    finally:
        get_store().reset()

    assert res.weight == 0.0, "flag-off + None signal must abstain (weight 0)"
    assert "no signal" in res.reasoning.lower(), res.reasoning
    assert "rank" not in res.reasoning.lower(), (
        "flag-off path must not consult the cross-sectional rank: " + res.reasoning
    )


# =========================================================================== #
# 4. FLAG OFF + a valid per-symbol signal -> the per-symbol tanh vote, unchanged.
# =========================================================================== #
@pytest.mark.anyio
async def test_flag_off_valid_signal_per_symbol_vote(monkeypatch):
    from core.round_table import agents as agents_mod
    from core.round_table.agents import LSTMSignalAgent

    src = _signal_source(10.0, action="BUY")  # strongly bullish
    reg = _registry(active=src, by_name={})
    _patch_get_config(
        monkeypatch, _cfg(distinct=False, rank=False), consumer_modules=[agents_mod]
    )
    with patch("core.round_table.agents.get_global_registry", return_value=reg):
        res = await LSTMSignalAgent().vote(_make_state())

    assert res.weight == pytest.approx(
        0.40
    ), "a present prediction is a full-weight vote"
    assert res.score > 0.5, f"bullish pred must score > 0.5, got {res.score}"
    assert "[src: LSTM model output]" in res.reasoning, res.reasoning
    assert "rank" not in res.reasoning.lower(), res.reasoning
