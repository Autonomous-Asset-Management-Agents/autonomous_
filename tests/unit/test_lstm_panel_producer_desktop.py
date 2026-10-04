# tests/unit/test_lstm_panel_producer_desktop.py
# RTR / #2388 Part A — the Desktop LSTM/RL panel-producer opt-in, proven USABLE.
#
# THE DORMANCY THIS PINS (verified against main @ 688fba0489):
#   The cross-section panel the auditable report LEADS WITH is written by exactly
#   one place — LSTMDynamicStrategy.update_lstm_rankings.record_cycle — and that
#   write is behind TWO independent gates, both default OFF, so the shipped desktop
#   build produces NO panel:
#     (1) LSTM_RANK_PANEL_STRATEGY_INDEPENDENT (config.py:1150) — whether the
#         standby producer is even built/driven (ACTIVE_STRATEGY defaults to RLAgent,
#         which has no update_lstm_rankings, so without this flag nothing runs);
#     (2) REPORT_GENERATOR_V2_ENABLED (config.py:1017) — whether update_lstm_rankings
#         actually CALLS record_cycle (lstm_strategy.py:478). Flag (1) alone runs the
#         producer but writes NOTHING, so the panel stays empty and LSTMSignalAgent's
#         cross-sectional path abstains all the same.
#   => the Desktop opt-in MUST set BOTH envs, not just (1). This module proves that
#      empirically: with the report flag OFF the very same producer run leaves the
#      panel empty; with it ON the panel is broad + dispersive and the agent's way
#      is free. That is the "belegt zusätzlich nötig" evidence, not an assertion.
#
# The VOTE path (LSTMSignalAgent voting ON the panel) additionally hangs on
# LSTM_VOTE_USES_CROSS_SECTIONAL_RANK (config.py:1199, agents.py:917) — the most
# SUBSTANTIVE trading-behaviour flag, walk-forward-gated. This module documents that
# state (test_the_panel_is_telemetry_until_the_vote_flag_flips) and DELIBERATELY does
# not include it in the producer opt-in group: producing the panel is telemetry for
# the auditable report; changing what the board votes on is a separate, armed step.
#
# Fully deterministic: no network / LLM / GPU / Redis. The torch model + inference
# are mocked; only the real numpy ranking math and the real panel store run.

from __future__ import annotations

import importlib.util
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import config
from core.report.lstm_panel_store import get_store
from core.round_table.agents import _MIN_PANEL_SYMBOLS, LSTMSignalAgent


def _load_config_oss():
    oss_path = os.path.join(os.path.dirname(config.__file__), "config.oss.py")
    spec = importlib.util.spec_from_file_location("config_oss_panel_desktop", oss_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_strategy():
    """Minimal LSTMDynamicStrategy double — heavy ML init bypassed, real numpy so the
    ranking/collapse math runs for real; only _get_torch_prediction is mocked."""
    import numpy as np

    from core.strategies.lstm_strategy import LSTMDynamicStrategy

    strategy = LSTMDynamicStrategy.__new__(LSTMDynamicStrategy)
    strategy.torch_model = MagicMock()  # truthy → update_lstm_rankings proceeds
    strategy.np = np
    strategy._lstm_rank_cache = []
    strategy._allocation_weights = {}
    return strategy


def _now():
    return datetime(2024, 6, 1, tzinfo=timezone.utc)


def _snapshots(symbols):
    snaps = {}
    for s in symbols:
        snap = MagicMock()
        snap.latest_trade.p = 100.0
        snaps[s] = snap
    return snaps


def _dispersive_symbols(n):
    """n symbols with strictly distinct, well-separated predictions (never a
    collapse, never a tie) so the panel is genuinely broad + dispersive."""
    syms = [f"S{i:03d}" for i in range(n)]
    preds = {s: float(i) * 0.05 - 1.0 for i, s in enumerate(syms)}  # -1.0 .. +
    return syms, preds


class _ReportCfg:
    """A get_config() double carrying only the one flag update_lstm_rankings reads.
    Mirrors the class-double idiom already used in test_lstm_abstention_ranking.py so
    the config read is patched at its single call site (config.get_config())."""

    def __init__(self, report_on: bool):
        self.REPORT_GENERATOR_V2_ENABLED = report_on


async def _run_producer(strategy, symbols, preds, report_on: bool):
    """Drive the REAL producer path (update_lstm_rankings → record_cycle) with mocked
    inference and the report flag set as given."""

    async def fake_pred(symbol, current_time, market_data):
        return preds[symbol], None

    strategy._get_torch_prediction = AsyncMock(side_effect=fake_pred)
    with patch("config.get_config", return_value=_ReportCfg(report_on)):
        await strategy.update_lstm_rankings(
            symbols, _snapshots(symbols), {"vix": 20.0}, _now()
        )


@pytest.fixture(autouse=True)
def _clean_panel():
    get_store().reset()
    yield
    get_store().reset()


# =========================================================================== #
# 1. ACTIVATION / BORA parity — the round-table-v2 opt-in group's two flags are now
#    default ON in BOTH editions (owner activation decision). This documents the new
#    ship default. LSTM_RANK_PANEL_STRATEGY_INDEPENDENT parity is pinned in
#    test_lstm_rank_panel_producer.py; this pins the SECOND, less-obvious flag
#    the dormancy analysis proved is also required — now armed alongside it.
# =========================================================================== #
def test_report_generator_flag_default_on_enterprise():
    # RTR activation: REPORT_GENERATOR_V2_ENABLED default flipped OFF→ON in
    # config.py:1028 (os.getenv(..., "True")). The panel writer's report gate is
    # now armed by default; the OFF path stays reachable via env (see
    # test_report_flag_off_writes_no_panel_so_the_agent_abstains, which forces it off).
    assert config.get_config().REPORT_GENERATOR_V2_ENABLED is True
    assert "REPORT_GENERATOR_V2_ENABLED" in config.RuntimeConfigState.model_fields


def test_report_generator_flag_default_on_oss():
    # BORA parity: config.oss.py:1014 flipped to the same ON default.
    oss = _load_config_oss()
    assert oss.REPORT_GENERATOR_V2_ENABLED is True
    assert oss.get_config().REPORT_GENERATOR_V2_ENABLED is True


def test_both_opt_in_flags_default_on_together():
    """Post-activation ship default: the Desktop opt-in group is now BOTH ON by
    default (owner decision), so the shipped build produces + records the panel.
    The byte-identical no-panel behaviour stays reachable by forcing either flag off
    via env (proven in test_report_flag_off_writes_no_panel_so_the_agent_abstains)."""
    cfg = config.get_config()
    assert cfg.LSTM_RANK_PANEL_STRATEGY_INDEPENDENT is True
    assert cfg.REPORT_GENERATOR_V2_ENABLED is True


# =========================================================================== #
# 2. THE EVIDENCE — why the opt-in group is {producer flag + report flag}, not
#    the producer flag alone. Same producer run, two report-flag states.
# =========================================================================== #
@pytest.mark.anyio
async def test_producer_with_report_flag_on_yields_broad_dispersive_panel():
    """A1-2 (Plan §5): the producer, given fixture data, writes a panel that is BROAD
    (n ≥ _MIN_PANEL_SYMBOLS) and DISPERSIVE (≥ 2 distinct scores) — the exact
    breadth/dispersion LSTMSignalAgent's guard (agents.py:821) requires to vote."""
    n = _MIN_PANEL_SYMBOLS + 5
    symbols, preds = _dispersive_symbols(n)
    strategy = _make_strategy()

    await _run_producer(strategy, symbols, preds, report_on=True)

    xsec = get_store().cross_section_at(datetime.now(timezone.utc).date())
    assert len(xsec) >= _MIN_PANEL_SYMBOLS, "panel must be broad enough to vote on"
    assert len(set(xsec.values())) >= 2, "panel must be dispersive, not collapsed"


@pytest.mark.anyio
async def test_lstm_signal_agent_way_is_free_once_the_panel_exists():
    """The point of A1: with the produced panel present, LSTMSignalAgent's
    cross-sectional path clears its breadth/dispersion guard and votes with
    weight > 0 — no 'EXCLUDED'/abstention. (Weight>0 is the Gherkin A1 criterion.)"""
    n = _MIN_PANEL_SYMBOLS + 5
    symbols, preds = _dispersive_symbols(n)
    strategy = _make_strategy()
    await _run_producer(strategy, symbols, preds, report_on=True)

    agent = LSTMSignalAgent()
    # a mid-panel symbol: unambiguously present, neither the top nor the bottom
    mid = symbols[n // 2]
    score, weight, reasoning = agent._vote_on_rank(mid, preds[mid], "BUY")

    assert weight > 0.0, "a broad dispersive panel must free the vote (no abstention)"
    assert "EXCLUDED" not in reasoning
    assert 0.0 <= score <= 1.0


@pytest.mark.anyio
async def test_report_flag_off_writes_no_panel_so_the_agent_abstains():
    """THE dormancy proof: flip ONLY the producer on (report flag OFF) and the very
    same producer run writes NOTHING — so LSTMSignalAgent abstains exactly as in the
    shipped build. This is why the Desktop opt-in must set REPORT_GENERATOR_V2_ENABLED
    too, not merely LSTM_RANK_PANEL_STRATEGY_INDEPENDENT."""
    n = _MIN_PANEL_SYMBOLS + 5
    symbols, preds = _dispersive_symbols(n)
    strategy = _make_strategy()

    await _run_producer(strategy, symbols, preds, report_on=False)

    # decision-path ranking ran (byte-identical), but the panel store is untouched:
    assert strategy._lstm_rank_cache, "ranking still computed (decision path intact)"
    xsec = get_store().cross_section_at(datetime.now(timezone.utc).date())
    assert xsec == {}, "report flag OFF ⇒ record_cycle never called ⇒ empty panel"

    agent = LSTMSignalAgent()
    _score, weight, reasoning = agent._vote_on_rank(
        symbols[0], preds[symbols[0]], "BUY"
    )
    assert weight == 0.0, "empty panel ⇒ abstention (shipped-build behaviour)"
    assert "EXCLUDED" in reasoning


# =========================================================================== #
# 3. HONEST SCOPE — the produced panel is telemetry until the vote flag flips.
#    Producing the panel does NOT, by itself, change what the board votes on;
#    LSTM_VOTE_USES_CROSS_SECTIONAL_RANK does, and it stays OFF (walk-forward-gated,
#    documented in the PR Arm-Checklist). This is not activated here.
# =========================================================================== #
def test_the_vote_flag_is_now_armed_and_still_gates_the_rank_scorer():
    """The vote-on-rank path (agents.py:917) is entered behind
    LSTM_VOTE_USES_CROSS_SECTIONAL_RANK. That flag was the SUBSTANTIVE, walk-forward-
    gated trading-behaviour switch and is now default ON in BOTH editions (owner
    activation decision, config.py:1234 / config.oss.py:1139) — so the board now votes
    on the produced panel, not merely the tanh fallback. The gating structure is
    UNCHANGED: the rank scorer is still reachable only behind the flag, and the #1969
    tanh path remains the code the vote falls back to when the flag is forced off."""
    import inspect

    from core.round_table import agents

    # New activated default (was False): the vote-on-rank path is armed by default.
    assert config.get_config().LSTM_VOTE_USES_CROSS_SECTIONAL_RANK is True
    oss = _load_config_oss()
    assert oss.get_config().LSTM_VOTE_USES_CROSS_SECTIONAL_RANK is True

    src = inspect.getsource(agents.LSTMSignalAgent)
    # the rank scorer is reachable ONLY behind the vote flag; the #1969 tanh path is
    # the fallback used when the flag is forced off via env.
    assert "if config.get_config().LSTM_VOTE_USES_CROSS_SECTIONAL_RANK:" in src
    assert "0.5 + 0.5 * math.tanh(pred / _LSTM_VOTE_SCALE)" in src


def test_producer_flag_and_report_flag_are_the_documented_writer_gates():
    """Pins the two gates that guard the panel's single writer (record_cycle) so a
    future reader is told mechanically, not left to rediscover the dormancy."""
    import inspect

    from core.strategies import lstm_strategy

    src = inspect.getsource(lstm_strategy.LSTMDynamicStrategy.update_lstm_rankings)
    # record_cycle is gated on the report flag inside the sole writer
    assert "REPORT_GENERATOR_V2_ENABLED" in src
    assert ".record_cycle(" in src
