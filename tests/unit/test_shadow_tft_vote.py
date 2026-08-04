# core/round_table + core/orchestration — Shadow-TFT-Vote (Fusion, dormant)
# TDD Red → Green. implementation_plan 2026-06-09-tft-state-shadow-vote.
#
# Gherkin:
#   - Given no specialist registry, When _fetch_context_node runs, Then state["ml"] is
#     None and no error is set (byte-identical decision).
#   - Given a report with ml_direction="up", When _fetch_context_node runs, Then
#     state["ml"]["tft_direction"]=="up" and only scalars are present (no model/DataFrame).
#   - Given SHADOW_TFT_VOTE_ENABLED=False, When the round-table records, Then NO shadow
#     vote is written.
#   - Given SHADOW_TFT_VOTE_ENABLED=True with state["ml"] present, Then exactly one
#     shadow record {tft_vote, real_action, agreement} is appended; order/signal untouched.
#   - On I/O failure the recorder logs at WARNING (AGENTS.md Rule 5) and never raises.
#
# Async interfaces (`_fetch_context_node`) are mocked / awaited per AGENTS.md Rule 2;
# AsyncMock is used wherever an async interface is replaced.

import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _base_state(symbol: str = "AAPL") -> dict:
    return {
        "symbol": symbol,
        "ohlc": {
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 1000.0,
        },
        "market_data_keys": [],
        "current_time": "2026-06-09T00:00:00Z",
        "signal": None,
        "error": None,
        "round_table_scores": None,
        "consensus_ranking": None,
    }


# ---------------------------------------------------------------------------
# 1. SymbolEvalState carries the optional `ml` scalar field
# ---------------------------------------------------------------------------
def test_symbol_eval_state_has_ml_field():
    from core.orchestration.graph import SymbolEvalState

    assert "ml" in SymbolEvalState.__annotations__


# ---------------------------------------------------------------------------
# 2. _fetch_context_node: no registry → ml=None, decision unaffected
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_fetch_context_no_registry_ml_none():
    from core.orchestration import graph

    with patch("core.round_table.agents._specialist_registry_instance", None):
        out = await graph._fetch_context_node(_base_state())

    assert out.get("ml") is None
    assert out.get("error") is None


# ---------------------------------------------------------------------------
# 3. _fetch_context_node: report present → state["ml"] has TFT scalars only
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_fetch_context_populates_tft_scalars():
    from core.orchestration import graph

    report = SimpleNamespace(
        ml_direction="up",
        ml_base_return_pct=1.2,
        ml_confidence=0.7,
        forecast_vol=0.03,
    )
    registry = MagicMock()
    registry.get_report.return_value = report

    with patch("core.round_table.agents._specialist_registry_instance", registry):
        out = await graph._fetch_context_node(_base_state())

    ml = out.get("ml")
    assert ml is not None
    assert ml["tft_direction"] == "up"
    assert ml["tft_base_return_pct"] == 1.2
    assert ml["tft_confidence"] == 0.7
    # Scalars only — no objects / arrays leak into the LangGraph state (BORA / §5.9)
    for value in ml.values():
        assert value is None or isinstance(value, (int, float, str))


# ---------------------------------------------------------------------------
# 4. TFT-only vote mapping (no sentiment, no Gemini)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "direction,expected",
    [
        ("up", "BUY"),
        ("down", "SELL"),
        ("neutral", "HOLD"),
        ("unavailable", "HOLD"),
        (None, "HOLD"),
    ],
)
def test_tft_vote_from_direction(direction, expected):
    from core.round_table.shadow_tft_recorder import _tft_vote_from_direction

    assert _tft_vote_from_direction(direction) == expected


# ---------------------------------------------------------------------------
# 5. record_shadow_tft_vote writes exactly one record with agreement
# ---------------------------------------------------------------------------
def test_record_shadow_tft_vote_writes_one_line(tmp_path):
    from core.round_table.shadow_tft_recorder import record_shadow_tft_vote

    chain = tmp_path / "nested" / "shadow_tft_votes.jsonl"
    record_shadow_tft_vote(
        symbol="AAPL",
        ml={"tft_direction": "up", "tft_confidence": 0.7, "tft_base_return_pct": 1.2},
        consensus_score=0.72,
        real_action="BUY",
        chain_path=str(chain),
    )

    lines = chain.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["symbol"] == "AAPL"
    assert rec["tft_vote"] == "BUY"
    assert rec["real_action"] == "BUY"
    assert rec["agreement"] is True


# ---------------------------------------------------------------------------
# 6. Recorder failure → WARNING (AGENTS.md Rule 5), never silent, never raises
# ---------------------------------------------------------------------------
def test_record_shadow_tft_vote_logs_warning_on_failure(tmp_path, caplog):
    from core.round_table.shadow_tft_recorder import record_shadow_tft_vote

    # chain_path is an existing DIRECTORY → open("a") raises → must log WARNING, not crash
    with caplog.at_level(logging.WARNING):
        record_shadow_tft_vote(
            symbol="AAPL",
            ml={"tft_direction": "down"},
            consensus_score=0.2,
            real_action="SELL",
            chain_path=str(tmp_path),
        )

    assert any(
        "shadow" in r.message.lower() and r.levelno == logging.WARNING
        for r in caplog.records
    )


# ---------------------------------------------------------------------------
# 7. Runner hook: flag OFF → recorder NOT called (dormant)
# ---------------------------------------------------------------------------
def test_runner_hook_flag_off_does_not_record():
    from core.round_table import runner

    cfg = SimpleNamespace(
        SHADOW_TFT_VOTE_ENABLED=False, SHADOW_TFT_VOTE_CHAIN_PATH="x.jsonl"
    )
    with patch("config.get_config", return_value=cfg), patch(
        "core.round_table.shadow_tft_recorder.record_shadow_tft_vote"
    ) as rec:
        runner._maybe_record_shadow_tft_vote(
            _base_state(), "AAPL", 0.72, SimpleNamespace(action="BUY")
        )

    rec.assert_not_called()


# ---------------------------------------------------------------------------
# 8. Runner hook: flag ON → recorder called once with the real action
# ---------------------------------------------------------------------------
def test_runner_hook_flag_on_records_once():
    from core.round_table import runner

    cfg = SimpleNamespace(
        SHADOW_TFT_VOTE_ENABLED=True, SHADOW_TFT_VOTE_CHAIN_PATH="shadow.jsonl"
    )
    state = _base_state()
    state["ml"] = {"tft_direction": "up", "tft_confidence": 0.7}
    with patch("config.get_config", return_value=cfg), patch(
        "core.round_table.shadow_tft_recorder.record_shadow_tft_vote"
    ) as rec:
        runner._maybe_record_shadow_tft_vote(
            state, "AAPL", 0.72, SimpleNamespace(action="BUY")
        )

    rec.assert_called_once()
    kwargs = rec.call_args.kwargs
    assert kwargs["symbol"] == "AAPL"
    assert kwargs["ml"] == {"tft_direction": "up", "tft_confidence": 0.7}
    assert kwargs["real_action"] == "BUY"


# ---------------------------------------------------------------------------
# 9. Runner hook never raises into the order path even if the recorder explodes
# ---------------------------------------------------------------------------
def test_runner_hook_swallows_recorder_errors(caplog):
    from core.round_table import runner

    cfg = SimpleNamespace(
        SHADOW_TFT_VOTE_ENABLED=True, SHADOW_TFT_VOTE_CHAIN_PATH="x.jsonl"
    )
    with caplog.at_level(logging.WARNING), patch(
        "config.get_config", return_value=cfg
    ), patch(
        "core.round_table.shadow_tft_recorder.record_shadow_tft_vote",
        side_effect=RuntimeError("boom"),
    ):
        # must NOT raise — order path is never affected
        runner._maybe_record_shadow_tft_vote(
            _base_state(), "AAPL", 0.72, SimpleNamespace(action="BUY")
        )

    # Rule 5: the hook's own except must log at WARNING, never silent
    assert any(
        "shadow" in r.message.lower() and r.levelno == logging.WARNING
        for r in caplog.records
    )


# ---------------------------------------------------------------------------
# 10. The ml scalars attached in _fetch_context_node survive into run_round_table.
#     Mocks the ASYNC interface (_run_round_table) with AsyncMock (AGENTS.md Rule 2).
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_ml_scalars_flow_into_strategy_node():
    from core.orchestration import graph

    state = _base_state()
    state["ml"] = {"tft_direction": "up", "tft_confidence": 0.7}

    # Async interface → AsyncMock (never a bare `async def` stub — Rule 2 / §5.2)
    mock_round_table = AsyncMock(side_effect=lambda passed_state: passed_state)
    with patch.object(graph, "_run_round_table", mock_round_table), patch.object(
        graph, "_ROUND_TABLE_AVAILABLE", True
    ):
        out = await graph._run_strategy_node(state)

    mock_round_table.assert_awaited_once()
    forwarded_state = mock_round_table.await_args.args[0]
    assert forwarded_state.get("ml") == {"tft_direction": "up", "tft_confidence": 0.7}
    assert out.get("ml") == {"tft_direction": "up", "tft_confidence": 0.7}


# ---------------------------------------------------------------------------
# 11. Behaviour-preservation: on a validation error, _attach_specialist_scalars
#     must NOT run and the existing error path is byte-identical (ml absent/None).
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_fetch_context_validation_error_skips_attach():
    from core.orchestration import graph

    bad_state = _base_state()
    bad_state["ohlc"]["close"] = 0.000001  # below the plausibility floor → raises

    with patch.object(
        graph, "_attach_specialist_scalars", wraps=graph._attach_specialist_scalars
    ) as mock_attach:
        out = await graph._fetch_context_node(bad_state)

    mock_attach.assert_not_called()
    assert out.get("error") is not None
    assert out.get("ml") is None
