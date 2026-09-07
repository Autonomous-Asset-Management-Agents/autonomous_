# tests/unit/test_run_round_table_rank_e2e.py
# INC (ML-gate outage) — END-TO-END reproduction of the LIVE desktop path.
#
# WHY THIS EXISTS (and why the isolated agent unit tests were not enough):
#   test_lstm_rank_decoupled.py exercises LSTMSignalAgent.vote() in ISOLATION with a
#   fully mocked registry. It proved the rank-first branch votes on the panel standing.
#   BUT the live desktop engine drives the WHOLE run_round_table(state) — the real
#   consensus engine, the real ComplianceGatekeeper, the real strict-ML gate, and the
#   real decision recorder — with the desktop-armed flag group:
#       ROUND_TABLE_DISTINCT_ML_SOURCES=True
#       LSTM_VOTE_USES_CROSS_SECTIONAL_RANK=True
#       FULL_UNIVERSE_TRADING_ENABLED=True
#       LSTM_RANK_PANEL_STRATEGY_INDEPENDENT=True
#       REPORT_GENERATOR_V2_ENABLED=True
#       GATEKEEPER_STRICT_ML_REQUIRES_RL=False
#   The live hotpatch produced 0 round-table decisions in ~14 min. This module drives
#   the REAL run_round_table on the REAL config surface (only the registry + external
#   I/O are mocked) to determine whether the rank-first branch breaks that path.
#
# THE CONTRACT UNDER TEST: run_round_table MUST return a state with NO error, a
# consensus_ranking, and a RECORDED decision (recent_decisions store) in BOTH:
#   * case A — an EMPTY/COLD panel  → the LSTM voice abstains GRACEFULLY (weight 0,
#     "EXCLUDED"), the decision is still recorded (no-trade), nothing raises;
#   * case B — a BROAD dispersive panel → the LSTM voice RANK-votes (weight > 0), the
#     decision is recorded.
# A break here (exception / no recorded decision) reproduces the live 0-decisions.
#
# Deterministic: no network / LLM / GPU / Redis. The registry, the LLM provider, the
# specialist registry and the Senate logger are stubbed; the REAL panel store runs.

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import config
from core.report.lstm_panel_store import get_store
from core.round_table.recent_decisions import (
    clear_recent_round_table_decisions,
    get_round_table_decision,
)
from core.round_table.runner import boot_engine, run_round_table

_SYMBOL = "AAPL"

# The exact flag group the desktop shell (native-engine-manager._childEnv) arms. Applied
# to the REAL config singleton so every consumer (runner, agents, gatekeeper) reads the
# live-armed surface — not a 6-field SimpleNamespace that would AttributeError elsewhere.
_LIVE_ARM_FLAGS = {
    "ROUND_TABLE_DISTINCT_ML_SOURCES": True,
    "LSTM_VOTE_USES_CROSS_SECTIONAL_RANK": True,
    "FULL_UNIVERSE_TRADING_ENABLED": True,
    "LSTM_RANK_PANEL_STRATEGY_INDEPENDENT": True,
    "REPORT_GENERATOR_V2_ENABLED": True,
    "GATEKEEPER_STRICT_ML_REQUIRES_RL": False,
}


def _arm_live_flags(monkeypatch) -> None:
    cfg = config.get_config()
    for key, value in _LIVE_ARM_FLAGS.items():
        # raising=False: the flag exists on both editions, but be defensive.
        monkeypatch.setattr(cfg, key, value, raising=False)


def _none_source():
    """The live standby "LSTMDynamic" ranker: evaluate_for_symbol returns None per
    symbol (the symbol is not in its per-symbol cache) — the exact live behaviour."""
    strat = MagicMock()
    strat.evaluate_for_symbol = AsyncMock(return_value=None)
    return strat


def _raising_source():
    """A resolved "LSTMDynamic" whose per-symbol evaluate RAISES (not just returns
    None) — the other live shape for a symbol absent from the ranker's cache (e.g. a
    "no bars"/KeyError inside the strategy). In rank mode this raise must NOT derail
    the vote: the score/weight come from the PANEL, the signal is enrichment only."""
    strat = MagicMock()
    strat.evaluate_for_symbol = AsyncMock(
        side_effect=RuntimeError("no bars for symbol (not in per-symbol cache)")
    )
    return strat


def _rl_source(pred: float = 8.0, action: str = "BUY"):
    """The active RL trader: a valid SignalEvent so RLConfidenceAgent votes weight > 0."""
    from core.cloud_logger import DecisionContext
    from core.events import SignalEvent

    strat = MagicMock()
    strat.evaluate_for_symbol = AsyncMock(
        return_value=SignalEvent(
            symbol=_SYMBOL,
            action=action,
            decision_context=DecisionContext(
                symbol=_SYMBOL, action=action, lstm_prediction=pred
            ),
        )
    )
    return strat


def _registry(*, lstm_raises: bool = False):
    """Distinct-ML-sources registry: LSTM voice -> get("LSTMDynamic") (returns None per
    symbol, or RAISES when ``lstm_raises``); RL voice -> get("RLAgent") / get_active()
    (a valid signal)."""
    lstm = _raising_source() if lstm_raises else _none_source()
    rl = _rl_source()
    reg = MagicMock()
    reg.get_active.return_value = rl
    reg.get.side_effect = lambda name: {"LSTMDynamic": lstm, "RLAgent": rl}.get(name)
    return reg


def _make_state(symbol: str = _SYMBOL) -> dict:
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
        "current_time": "2026-03-10T07:00:00+00:00",
        "signal": None,
        "error": None,
        "round_table_scores": None,
        "consensus_ranking": None,
    }


def _seed_broad_panel(symbol: str) -> None:
    """A BROAD, DISPERSED panel (>= _MIN_PANEL_SYMBOLS, >= 2 distinct scores) INCLUDING
    ``symbol`` — recorded under today's UTC date (what _vote_on_rank reads)."""
    ranked = [(f"P{i:03d}", float(i) * 0.05 - 1.0) for i in range(34)]
    ranked.append((symbol, 0.42))
    get_store().reset()
    get_store().record_cycle(datetime.now(timezone.utc).date(), ranked)


async def _drive(monkeypatch, *, seed_broad: bool, lstm_raises: bool = False) -> dict:
    """Boot the real engine, arm the live flags, seed the panel, drive the REAL
    run_round_table with only the registry + external I/O stubbed."""
    _arm_live_flags(monkeypatch)
    boot_engine(None)
    clear_recent_round_table_decisions()
    if seed_broad:
        _seed_broad_panel(_SYMBOL)
    else:
        get_store().reset()  # case A: empty / cold panel

    reg = _registry(lstm_raises=lstm_raises)
    mock_senate = MagicMock()
    mock_senate.log_session = AsyncMock()

    try:
        with (
            patch("core.round_table.agents.get_global_registry", return_value=reg),
            patch("core.round_table.runner.get_global_registry", return_value=reg),
            # No API key in CI: force the news LLM path to the graceful fallback rather
            # than attempting a real provider call (offline determinism).
            patch("core.round_table.agents.get_llm_provider", None),
            patch("core.round_table.agents._specialist_registry_instance", None),
            # Keep the Senate log out of the local JSON file / event loop teardown.
            patch("core.round_table.runner._senate", mock_senate),
        ):
            result = await run_round_table(_make_state())
    finally:
        get_store().reset()
    return result


def _lstm_vote(result: dict) -> dict:
    scores = result.get("round_table_scores") or []
    votes = [v for v in scores if v.get("agent_name") == "LSTMSignalAgent"]
    assert votes, f"LSTMSignalAgent vote missing from round_table_scores: {scores}"
    return votes[0]


# =========================================================================== #
# CASE A — EMPTY / COLD panel (the first live cycles, before the standby producer
# has warmed the panel). The LSTM voice must abstain GRACEFULLY and the round table
# must STILL run to completion and RECORD a decision. A raise or a missing recorded
# decision here reproduces the live 0-decisions.
# =========================================================================== #
@pytest.mark.anyio
async def test_e2e_empty_panel_records_decision_lstm_abstains(monkeypatch):
    result = await _drive(monkeypatch, seed_broad=False)

    assert (
        result.get("error") is None
    ), f"run_round_table errored: {result.get('error')}"
    assert result.get("consensus_ranking") is not None
    assert 0.0 <= result["consensus_ranking"] <= 1.0

    # The whole point of the "0 decisions" symptom: a decision MUST be recorded.
    rec = get_round_table_decision(_SYMBOL)
    assert (
        rec is not None
    ), "no round-table decision recorded (this is the live symptom)"

    lstm = _lstm_vote(result)
    assert lstm["weight"] == 0.0, f"cold panel: LSTM must abstain, got {lstm}"
    assert "EXCLUDED" in lstm["reasoning"], lstm["reasoning"]


# =========================================================================== #
# CASE B — BROAD dispersive panel: the LSTM voice RANK-votes (weight > 0) and the
# decision is recorded. This is the path the fix was built to enable.
# =========================================================================== #
@pytest.mark.anyio
async def test_e2e_broad_panel_records_decision_lstm_rank_votes(monkeypatch):
    result = await _drive(monkeypatch, seed_broad=True)

    assert (
        result.get("error") is None
    ), f"run_round_table errored: {result.get('error')}"
    assert result.get("consensus_ranking") is not None
    assert 0.0 <= result["consensus_ranking"] <= 1.0

    rec = get_round_table_decision(_SYMBOL)
    assert rec is not None, "no round-table decision recorded"

    lstm = _lstm_vote(result)
    assert lstm["weight"] > 0.0, f"broad panel: LSTM must rank-vote, got {lstm}"
    assert "rank" in lstm["reasoning"].lower(), lstm["reasoning"]
    assert "EXCLUDED" not in lstm["reasoning"], lstm["reasoning"]


# =========================================================================== #
# CASE C — guard against a silent regression of the "0 decisions" symptom itself:
# under the exact live-armed flags with a cold panel, the LSTM abstention must NOT
# empty valid_votes (which WOULD early-return without recording). At least the 8
# non-LSTM voices plus the abstaining LSTM must survive into the serialized votes.
# =========================================================================== #
@pytest.mark.anyio
async def test_e2e_cold_panel_does_not_starve_valid_votes(monkeypatch):
    result = await _drive(monkeypatch, seed_broad=False)

    assert result.get("error") is None, result.get("error")
    scores = result.get("round_table_scores") or []
    assert len(scores) >= 2, (
        "the round table must still aggregate the non-LSTM voices even when the LSTM "
        f"voice abstains on a cold panel; got {len(scores)} votes"
    )


# =========================================================================== #
# CASE D — the ROBUSTNESS gap the isolated agent tests never covered: in rank mode
# the best-effort per-symbol evaluate can RAISE (the live standby ranker throws for a
# symbol not in its cache), not just return None. A raise must NOT derail the rank
# vote — the score/weight come from the PANEL, the signal is enrichment only. Before
# the robustness fix this abstains (the raise escapes to the outer except); after, the
# LSTM voice still rank-votes on the broad panel (weight > 0).
# =========================================================================== #
@pytest.mark.anyio
async def test_e2e_evaluate_raises_rank_mode_still_rank_votes(monkeypatch):
    result = await _drive(monkeypatch, seed_broad=True, lstm_raises=True)

    assert (
        result.get("error") is None
    ), f"run_round_table errored: {result.get('error')}"
    rec = get_round_table_decision(_SYMBOL)
    assert rec is not None, "no round-table decision recorded"

    lstm = _lstm_vote(result)
    assert lstm["weight"] > 0.0, (
        "a raising best-effort evaluate must NOT derail the rank vote — the panel "
        f"standing still drives it; got {lstm}"
    )
    assert "rank" in lstm["reasoning"].lower(), lstm["reasoning"]
    assert "EXCLUDED" not in lstm["reasoning"], lstm["reasoning"]
    assert "error" not in lstm["reasoning"].lower(), (
        "the rank vote must read as a real standing, not a swallowed-error abstain: "
        + lstm["reasoning"]
    )
