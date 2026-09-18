# tests/unit/test_position_context.py
# #2672 (TRD / P1) — Reconcile broker positions into the round-table decision state.
#
# Today the board decides POSITION-BLIND: live_trading_loop builds _symbol_state
# (trading_loop.py ~L1528) with news/OHLC only — no position fields — so every
# board decision persists in_position=0 even for held symbols (field evidence
# 31.07.–04.08.: 4,202/4,209 decisions blind; 241 approved HOOD SELLs on 14.07.
# executed 0×). Broker positions ARE fetched per cycle, but only behind unrelated
# gates (rank funnel `if _top_k > 0`, GAP9 `GATEKEEPER_PORTFOLIO_CONTEXT_ENABLED`)
# and never reach the per-symbol eval state.
#
# Fix (#2672 plan Rev. 2, Option A — dark, default OFF):
#   1. trading_loop._fetch_position_snapshot(api): ONE unconditional per-cycle
#      get_all_positions → ({sym: {qty, avg_entry, unrealized_pnl}}, confirmed).
#      No TOP_K / feature-flag gate INSIDE the helper — decoupling by construction
#      (Archon audit Critical 1). Failure/no-api → ({}, False) + WARNING (§5.6).
#   2. trading_loop._position_context_state_keys(...): flag-gated producer for the
#      five DECLARED SymbolEvalState channels (in_position, position_qty,
#      position_avg_price, unrealized_pnl, position_context_confirmed). House
#      pattern #1949: flag OFF → all five None = byte-identical dark ship.
#      Fail-closed contract: confirmed=False → position fields None ("unknown"),
#      NEVER False/0 ("flat").
#   3. runner._position_fields_from_state(state): maps the channels into
#      DecisionContext kwargs; unconfirmed context additionally surfaces as a
#      "[position context UNCONFIRMED]" reasoning prefix so audit rows are
#      self-describing.
#   4. Config parity (BORA): ROUND_TABLE_POSITION_CONTEXT_ENABLED default False
#      in config.py AND config.oss.py.
#
# Issue #2672 test plan mapping:
#   T1 state carries position context  → R4/R5 (+R8 channel declaration)
#   T2 decoupled from rank funnel      → R1–R3 (helper has no TOP_K gate; the
#                                        loop-wiring assertion follows in Green)
#   T3 decision row records position   → R9/R10 (runner mapping seam)
#   T4 unconfirmed fails closed        → R2/R3/R7/R11
#   T5 flag OFF byte-identical         → R6/R12
#   T6 config parity                   → R13/R14

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_AI_BOT = Path(__file__).resolve().parents[2]  # ai_trading_bot/

POSITION_CHANNELS = (
    "in_position",
    "position_qty",
    "position_avg_price",
    "unrealized_pnl",
    "position_context_confirmed",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _arm(monkeypatch, enabled: bool) -> None:
    """Pin ROUND_TABLE_POSITION_CONTEXT_ENABLED via config.get_config —
    the finance-core's only config seam (CODING_POLICY §2.10)."""
    import config

    monkeypatch.setattr(
        config,
        "get_config",
        lambda: SimpleNamespace(ROUND_TABLE_POSITION_CONTEXT_ENABLED=enabled),
    )


def _broker_position(symbol: str, qty: float, avg: float, upl: float):
    """Shape-compatible stand-in for an alpaca Position (attribute access)."""
    pos = MagicMock()
    pos.symbol = symbol
    pos.qty = str(qty)  # SDK returns strings — helper must float-coerce
    pos.avg_entry_price = str(avg)
    pos.unrealized_pl = str(upl)
    return pos


def _snapshot_bny():
    return {
        "BNY": {"qty": 128.938538, "avg_entry": 155.23, "unrealized_pnl": 261.80},
    }


# ---------------------------------------------------------------------------
# 1. _fetch_position_snapshot — unconditional, fail-closed (R1–R3)
# ---------------------------------------------------------------------------


async def test_r1_fetch_snapshot_returns_positions_confirmed():
    """Happy path: broker book → per-symbol dict + confirmed=True."""
    from core.engine.trading_loop import _fetch_position_snapshot

    api = MagicMock()
    api.get_all_positions.return_value = [
        _broker_position("BNY", 128.938538, 155.23, 261.80),
        _broker_position("XOM", 129.149261, 154.82, -336.81),
    ]

    positions, confirmed = await _fetch_position_snapshot(api)

    assert confirmed is True
    assert set(positions) == {"BNY", "XOM"}
    assert positions["BNY"]["qty"] == pytest.approx(128.938538)
    assert positions["BNY"]["avg_entry"] == pytest.approx(155.23)
    assert positions["BNY"]["unrealized_pnl"] == pytest.approx(261.80)


async def test_r2_fetch_snapshot_failure_unconfirmed_warns(caplog):
    """Broker error → ({}, False) + WARNING (§5.6: visible, never DEBUG-only)."""
    from core.engine.trading_loop import _fetch_position_snapshot

    api = MagicMock()
    api.get_all_positions.side_effect = RuntimeError("alpaca down")

    with caplog.at_level(logging.WARNING):
        positions, confirmed = await _fetch_position_snapshot(api)

    assert positions == {}
    assert confirmed is False
    assert any(
        rec.levelno >= logging.WARNING and "position" in rec.getMessage().lower()
        for rec in caplog.records
    ), "fetch failure must be visible at WARNING (§5.6)"


async def test_r3_fetch_snapshot_no_api_unconfirmed(caplog):
    """No client at all → ({}, False) + WARNING — unconfirmed, not silently flat."""
    from core.engine.trading_loop import _fetch_position_snapshot

    with caplog.at_level(logging.WARNING):
        positions, confirmed = await _fetch_position_snapshot(None)

    assert positions == {}
    assert confirmed is False
    assert any(rec.levelno >= logging.WARNING for rec in caplog.records)


# ---------------------------------------------------------------------------
# 2. _position_context_state_keys — flag-gated producer (R4–R7, R12)
# ---------------------------------------------------------------------------


def test_r4_state_keys_flag_on_held_symbol(monkeypatch):
    """Flag ON + confirmed book: held symbol carries its real position."""
    from core.engine.trading_loop import _position_context_state_keys

    _arm(monkeypatch, enabled=True)
    keys = _position_context_state_keys(_snapshot_bny(), True, "BNY")

    assert keys["in_position"] is True
    assert keys["position_qty"] == pytest.approx(128.938538)
    assert keys["position_avg_price"] == pytest.approx(155.23)
    assert keys["unrealized_pnl"] == pytest.approx(261.80)
    assert keys["position_context_confirmed"] is True


def test_r5_state_keys_flag_on_flat_symbol(monkeypatch):
    """Flag ON + confirmed book: a symbol NOT in the book is genuinely flat."""
    from core.engine.trading_loop import _position_context_state_keys

    _arm(monkeypatch, enabled=True)
    keys = _position_context_state_keys(_snapshot_bny(), True, "AAPL")

    assert keys["in_position"] is False
    assert keys["position_qty"] == 0.0
    assert keys["position_avg_price"] == 0.0
    assert keys["unrealized_pnl"] == 0.0
    assert keys["position_context_confirmed"] is True


def test_r6_state_keys_flag_off_all_none(monkeypatch):
    """Flag OFF (default): all five channels None — byte-identical dark ship
    (house pattern #1949: declared channels, neutral producer values)."""
    from core.engine.trading_loop import _position_context_state_keys

    _arm(monkeypatch, enabled=False)
    keys = _position_context_state_keys(_snapshot_bny(), True, "BNY")

    assert set(keys) == set(POSITION_CHANNELS)
    assert all(keys[k] is None for k in POSITION_CHANNELS)


def test_r7_state_keys_unconfirmed_fails_closed(monkeypatch):
    """Flag ON + UNCONFIRMED book: position fields are None ("unknown") — a
    consumer must never read an unconfirmed context as "flat" (audit Warning 3)."""
    from core.engine.trading_loop import _position_context_state_keys

    _arm(monkeypatch, enabled=True)
    keys = _position_context_state_keys({}, False, "BNY")

    assert keys["position_context_confirmed"] is False
    assert keys["in_position"] is None, "unconfirmed must not present as flat"
    assert keys["position_qty"] is None
    assert keys["position_avg_price"] is None
    assert keys["unrealized_pnl"] is None


def test_r12_state_keys_broken_config_fails_safe_off(monkeypatch):
    """Config seam raising → treated as flag OFF (fail-safe, mirrors #1949)."""
    import config
    from core.engine.trading_loop import _position_context_state_keys

    def _boom():
        raise ValueError("broken config")

    monkeypatch.setattr(config, "get_config", _boom)
    keys = _position_context_state_keys(_snapshot_bny(), True, "BNY")

    assert all(keys[k] is None for k in POSITION_CHANNELS)


# ---------------------------------------------------------------------------
# 3. SymbolEvalState channel declaration (R8)
# ---------------------------------------------------------------------------


def test_r8_symbol_eval_state_declares_position_channels():
    """langgraph==1.0.10 drops undeclared input keys before any node runs —
    the five position channels MUST be declared (CLAUDE.md §5.9, #1949)."""
    from core.orchestration.graph import SymbolEvalState

    declared = set(SymbolEvalState.__annotations__)
    missing = set(POSITION_CHANNELS) - declared
    assert (
        not missing
    ), f"undeclared position channels (LangGraph drops them): {missing}"


# ---------------------------------------------------------------------------
# 4. runner mapping seam (R9–R11)
# ---------------------------------------------------------------------------


def _eval_state(**overrides) -> dict:
    state = {
        "symbol": "BNY",
        "ohlc": {
            "open": 155.0,
            "high": 157.0,
            "low": 154.0,
            "close": 156.0,
            "volume": 1_000_000.0,
        },
        "market_data_keys": [],
        "current_time": "2026-08-05T14:00:00+00:00",
        "signal": None,
        "error": None,
        "round_table_scores": None,
        "consensus_ranking": None,
        "in_position": None,
        "position_qty": None,
        "position_avg_price": None,
        "unrealized_pnl": None,
        "position_context_confirmed": None,
    }
    state.update(overrides)
    return state


def test_r9_runner_maps_held_position_into_decision_fields():
    """Confirmed held position → DecisionContext kwargs carry the real book."""
    from core.round_table.runner import _position_fields_from_state

    fields, reasoning_prefix = _position_fields_from_state(
        _eval_state(
            in_position=True,
            position_qty=128.938538,
            position_avg_price=155.23,
            unrealized_pnl=261.80,
            position_context_confirmed=True,
        )
    )

    assert fields["in_position"] is True
    assert fields["position_qty"] == pytest.approx(128.938538)
    assert fields["position_avg_price"] == pytest.approx(155.23)
    assert fields["unrealized_pnl"] == pytest.approx(261.80)
    assert fields["position_context_confirmed"] is True
    assert reasoning_prefix == ""


def test_r10_decision_context_accepts_confirmed_field():
    """DecisionContext must expose position_context_confirmed (additive,
    Optional, default None — DB writer drops column-less keys, forecast_vol
    precedent cloud_logger.py:104-110)."""
    from core.cloud_logger import DecisionContext

    ctx = DecisionContext(symbol="BNY", position_context_confirmed=True)
    assert ctx.position_context_confirmed is True
    assert DecisionContext(symbol="BNY").position_context_confirmed is None


def test_r11_runner_unconfirmed_context_yields_audit_marker():
    """Unconfirmed context → neutral defaults + self-describing audit marker
    in the reasoning prefix (audit Warning 3: fail-closed, visible)."""
    from core.round_table.runner import _position_fields_from_state

    fields, reasoning_prefix = _position_fields_from_state(
        _eval_state(position_context_confirmed=False)
    )

    assert fields["position_context_confirmed"] is False
    # neutral defaults — never fabricated position data
    assert fields.get("in_position") in (False, None)
    assert "UNCONFIRMED" in reasoning_prefix


def test_r11b_runner_flag_off_channels_none_is_byte_identical():
    """All-None channels (flag OFF) → empty kwargs + empty prefix — the
    DecisionContext keeps today's defaults, byte-identical dark ship."""
    from core.round_table.runner import _position_fields_from_state

    fields, reasoning_prefix = _position_fields_from_state(_eval_state())

    assert fields == {}
    assert reasoning_prefix == ""


# ---------------------------------------------------------------------------
# 5. Config parity — BORA (R13/R14)
# ---------------------------------------------------------------------------


# ARMED by default since 2026-08-05 (owner decision). Verified RECORD-ONLY before
# arming: no agent reads `in_position` from the state, and no execution path branches on
# `DecisionContext.in_position` — the single reader is runner._position_fields_from_state,
# which maps it into the audit record. Arming therefore fixes WHAT IS WRITTEN (4,202 of
# 4,209 decisions were position-blind 31.07.–04.08.) and changes no trading decision.
# The flag stays a flag: `=false` restores the dark behaviour exactly.


def test_r13_enterprise_config_defines_flag_armed():
    import config

    cfg = config.get_config()
    assert hasattr(
        cfg, "ROUND_TABLE_POSITION_CONTEXT_ENABLED"
    ), "config.py must define ROUND_TABLE_POSITION_CONTEXT_ENABLED (BORA parity)"
    assert (
        cfg.ROUND_TABLE_POSITION_CONTEXT_ENABLED is True
    ), "default must be ARMED (record-only; position-blind decision rows are the defect)"


def test_r14_oss_config_defines_flag_armed():
    spec = importlib.util.spec_from_file_location(
        "config_oss_position_context_parity", _AI_BOT / "config.oss.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert hasattr(
        module, "ROUND_TABLE_POSITION_CONTEXT_ENABLED"
    ), "config.oss.py must define ROUND_TABLE_POSITION_CONTEXT_ENABLED (BORA parity)"
    assert module.ROUND_TABLE_POSITION_CONTEXT_ENABLED is True
