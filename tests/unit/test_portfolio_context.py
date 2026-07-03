# tests/unit/test_portfolio_context.py
# GAP9 — feed the ComplianceGatekeeper ("Iron Dome") a real per-cycle portfolio snapshot.
#
# Two seams under test:
#   1. core/engine/portfolio_context.build_portfolio_context — builds the 7-key snapshot,
#      fail-OPEN to None on any error (so default behaviour never regresses).
#   2. core/round_table/runner._resolve_gatekeeper_decision — consumes the snapshot, warns
#      (rate-limited) when it is missing, and (strict mode) fails CLOSED on a BUY.
#   3. SymbolEvalState carries `_portfolio_context` THROUGH the compiled LangGraph — the
#      field must be in the TypedDict or LangGraph drops it before the runner reads it.
#
# Policy: CODING_POLICY.md §5.1 TDD, §1 Compliance-First, §5.2 AsyncMock.

from __future__ import annotations

import logging
import math
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.engine.portfolio_context import build_portfolio_context

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _account(equity=100000.0, daytrade_count=1):
    return SimpleNamespace(equity=equity, daytrade_count=daytrade_count)


def _position(symbol, market_value):
    return SimpleNamespace(symbol=symbol, market_value=market_value)


def _api(account=None, positions=None, account_exc=None):
    api = MagicMock()
    if account_exc is not None:
        api.get_account.side_effect = account_exc
    else:
        api.get_account.return_value = account if account is not None else _account()
    api.get_all_positions.return_value = positions or []
    return api


def _guardian(daily_trades=5, max_daily_trades=50):
    return SimpleNamespace(daily_trades=daily_trades, max_daily_trades=max_daily_trades)


_SEVEN_KEYS = {
    "day_trades_last_5d",
    "max_daily_trades",
    "current_daily_trades",
    "symbol_weights",
    "sector_weights",
    "symbol_sector_map",
    "position_locked",
}


# ---------------------------------------------------------------------------
# 1. build_portfolio_context
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_happy_path_has_all_seven_keys():
    api = _api(
        account=_account(equity=100000.0, daytrade_count=1),
        positions=[_position("AAPL", 25000.0), _position("MSFT", 10000.0)],
    )
    ctx = await build_portfolio_context(api, _guardian())
    assert ctx is not None
    assert set(ctx) == _SEVEN_KEYS
    assert ctx["symbol_weights"]["AAPL"] == pytest.approx(0.25)
    assert ctx["symbol_weights"]["MSFT"] == pytest.approx(0.10)
    assert ctx["position_locked"] is False


@pytest.mark.anyio
async def test_mapping_pdt_and_daily_counters():
    api = _api(account=_account(equity=50000.0, daytrade_count=2), positions=[])
    ctx = await build_portfolio_context(
        api, _guardian(daily_trades=7, max_daily_trades=40)
    )
    assert ctx["day_trades_last_5d"] == 2
    assert ctx["current_daily_trades"] == 7
    assert ctx["max_daily_trades"] == 40


@pytest.mark.anyio
async def test_fail_open_on_api_exception(caplog):
    api = _api(account_exc=RuntimeError("alpaca down"))
    with caplog.at_level(logging.WARNING):
        ctx = await build_portfolio_context(api, _guardian())
    assert ctx is None  # fail OPEN — never raise, never regress today's behaviour
    assert any("without" in r.message.lower() for r in caplog.records)


@pytest.mark.anyio
async def test_fail_open_on_nonpositive_equity(caplog):
    api = _api(account=_account(equity=0.0), positions=[])
    with caplog.at_level(logging.WARNING):
        ctx = await build_portfolio_context(api, _guardian())
    assert ctx is None


@pytest.mark.anyio
async def test_none_api_returns_none():
    ctx = await build_portfolio_context(None, _guardian())
    assert ctx is None


@pytest.mark.anyio
async def test_sector_gap_is_empty_and_visible(caplog):
    api = _api(account=_account(), positions=[_position("AAPL", 1000.0)])
    with caplog.at_level(logging.WARNING):
        ctx = await build_portfolio_context(api, _guardian())
    assert ctx["sector_weights"] == {}
    assert ctx["symbol_sector_map"] == {}
    assert any("sector" in r.message.lower() for r in caplog.records)


@pytest.mark.anyio
async def test_short_position_weight_reflects_exposure():
    # P0-fix (#1159): abs(market_value)/equity — a $5k short on $100k equity is 5%
    # exposure, not 0% (which would make the Iron Dome blind to short-side risk).
    api = _api(
        account=_account(equity=100000.0), positions=[_position("TSLA", -5000.0)]
    )
    ctx = await build_portfolio_context(api, _guardian())
    assert ctx["symbol_weights"]["TSLA"] == pytest.approx(0.05)


@pytest.mark.anyio
async def test_large_short_has_high_concentration_weight():
    # ADR-RISK-01: a large short (25% of equity) produces 0.25 weight so the
    # concentration check can block further BUY exposure on that symbol.
    # Known v1 limitation: a covering BUY is also blocked (conservative, not unsafe).
    api = _api(
        account=_account(equity=100000.0), positions=[_position("TSLA", -25000.0)]
    )
    ctx = await build_portfolio_context(api, _guardian())
    assert ctx["symbol_weights"]["TSLA"] == pytest.approx(0.25)


@pytest.mark.anyio
async def test_missing_guardian_uses_safe_daily_defaults():
    api = _api(account=_account(), positions=[])
    ctx = await build_portfolio_context(api, None)
    assert ctx["current_daily_trades"] == 0
    assert ctx["max_daily_trades"] == 50


# ---------------------------------------------------------------------------
# 2. runner._resolve_gatekeeper_decision  (strict / fail-open seam)
# ---------------------------------------------------------------------------


class _FakeGatekeeper:
    def __init__(self):
        from core.round_table.gatekeeper import GatekeeperDecision

        self.calls = []
        self._approve = GatekeeperDecision(approved=True, reason="ok", symbol="X")

    async def check(self, symbol, score, ctx):
        self.calls.append((symbol, score, dict(ctx)))
        return self._approve


def _reset_warn_throttle():
    from core.round_table import runner as runner_module

    with runner_module._MISSING_CONTEXT_WARN_LOCK:
        runner_module._LAST_MISSING_CONTEXT_WARN_TS = 0.0


@pytest.mark.anyio
async def test_present_context_calls_gatekeeper_no_warning(caplog):
    from core.round_table.runner import _resolve_gatekeeper_decision

    _reset_warn_throttle()
    gk = _FakeGatekeeper()
    ctx = {"symbol_weights": {"AAPL": 0.1}}
    with caplog.at_level(logging.WARNING):
        dec = await _resolve_gatekeeper_decision(
            gk, "AAPL", 0.9, ctx, require_context=False
        )
    assert dec.approved is True
    assert gk.calls == [("AAPL", 0.9, ctx)]
    assert not any(
        "without portfolio context" in r.message.lower() for r in caplog.records
    )


@pytest.mark.anyio
async def test_missing_context_fail_open_warns_and_proceeds(caplog):
    from core.round_table.runner import _resolve_gatekeeper_decision

    _reset_warn_throttle()
    gk = _FakeGatekeeper()
    with caplog.at_level(logging.WARNING):
        dec = await _resolve_gatekeeper_decision(
            gk, "AAPL", 0.9, {}, require_context=False
        )
    assert dec.approved is True  # fail OPEN = today's behaviour
    assert (
        gk.calls and gk.calls[0][2] == {}
    )  # gatekeeper still consulted with empty ctx
    assert any("without portfolio context" in r.message.lower() for r in caplog.records)


@pytest.mark.anyio
async def test_strict_blocks_buy_when_context_missing():
    from core.round_table.gatekeeper import ComplianceGatekeeper
    from core.round_table.runner import _resolve_gatekeeper_decision

    _reset_warn_throttle()
    gk = _FakeGatekeeper()
    buy_score = ComplianceGatekeeper.BUY_THRESHOLD + 0.05
    dec = await _resolve_gatekeeper_decision(
        gk, "AAPL", buy_score, {}, require_context=True
    )
    assert dec.approved is False  # fail CLOSED
    assert "context" in dec.reason.lower()
    assert gk.calls == []  # short-circuited — gatekeeper not even consulted


@pytest.mark.anyio
async def test_strict_lets_sell_through_when_context_missing():
    from core.round_table.gatekeeper import ComplianceGatekeeper
    from core.round_table.runner import _resolve_gatekeeper_decision

    _reset_warn_throttle()
    gk = _FakeGatekeeper()
    sell_score = ComplianceGatekeeper.BUY_THRESHOLD - 0.30  # not a BUY
    dec = await _resolve_gatekeeper_decision(
        gk, "AAPL", sell_score, {}, require_context=True
    )
    assert dec.approved is True  # risk-reducing SELL/HOLD never blocked by strict mode
    assert gk.calls  # gatekeeper consulted normally


# ---------------------------------------------------------------------------
# 3. SymbolEvalState propagates _portfolio_context through the compiled graph
# ---------------------------------------------------------------------------


def test_symbol_eval_state_has_portfolio_context_field():
    from core.orchestration.graph import SymbolEvalState

    assert "_portfolio_context" in SymbolEvalState.__annotations__


@pytest.mark.anyio
async def test_portfolio_context_propagates_through_compiled_graph(monkeypatch):
    from core.orchestration import graph as graph_module

    captured = {}

    async def _capture(state):
        captured["ctx"] = state.get("_portfolio_context")
        return {**state, "signal": None}

    # no checkpointer → pure in-memory compile; capture what the runner node receives.
    monkeypatch.setattr(graph_module, "_build_checkpointer", lambda: None)
    monkeypatch.setattr(graph_module, "_run_round_table", _capture)
    monkeypatch.setattr(graph_module, "_ROUND_TABLE_AVAILABLE", True)

    compiled = graph_module.build_symbol_eval_graph()
    expected = {"day_trades_last_5d": 0, "symbol_weights": {"AAPL": 0.5}}
    state = {
        "symbol": "AAPL",
        "ohlc": {
            "open": 150.0,
            "high": 155.0,
            "low": 148.0,
            "close": 152.0,
            "volume": 1e6,
        },
        "market_data_keys": [],
        "current_time": "2026-03-10T07:00:00+00:00",
        "signal": None,
        "error": None,
        "_portfolio_context": expected,
    }
    await compiled.ainvoke(state)
    # Without the TypedDict field, LangGraph drops the channel and this is None.
    assert captured["ctx"] == expected


# ---------------------------------------------------------------------------
# P0-2 (Antigravity): concurrency test for _warn_gatekeeper_missing_context
# _LAST_MISSING_CONTEXT_WARN_TS is protected by _MISSING_CONTEXT_WARN_LOCK so the
# read-modify-write is atomic — exactly 1 warning must fire regardless of thread count.
# ---------------------------------------------------------------------------
def test_warn_gatekeeper_missing_context_rate_limits_under_threads():
    """20 threads firing simultaneously must produce exactly 1 warning per
    _MISSING_CONTEXT_WARN_INTERVAL_S window — lock makes this deterministic."""
    import threading
    import time

    from core.round_table import runner as runner_module
    from core.round_table.runner import _warn_gatekeeper_missing_context

    runner_module._LAST_MISSING_CONTEXT_WARN_TS = 0.0
    warning_times = []

    original_warning = runner_module.logger.warning

    def _counting_warning(msg, *args, **kwargs):
        warning_times.append(time.monotonic())
        original_warning(msg, *args, **kwargs)

    runner_module.logger.warning = _counting_warning
    try:
        threads = [
            threading.Thread(target=_warn_gatekeeper_missing_context) for _ in range(20)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        runner_module.logger.warning = original_warning
        with runner_module._MISSING_CONTEXT_WARN_LOCK:
            runner_module._LAST_MISSING_CONTEXT_WARN_TS = 0.0

    assert len(warning_times) == 1, (
        f"Rate limiter fired {len(warning_times)} times under 20 concurrent threads "
        "(expected exactly 1 — _MISSING_CONTEXT_WARN_LOCK must be held during RMW)"
    )


# ---------------------------------------------------------------------------
# P1-3 (Antigravity): NaN/Inf guards in build_portfolio_context
# float('nan') <= 0 evaluates to False in Python — the old equity<=0 guard
# would pass NaN through and poison the snapshot JSON / SQLite logging.
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_fail_open_on_nan_equity(caplog):
    api = _api(account=_account(equity=float("nan")), positions=[])
    with caplog.at_level(logging.WARNING):
        ctx = await build_portfolio_context(api, _guardian())
    assert ctx is None


@pytest.mark.anyio
async def test_fail_open_on_inf_equity(caplog):
    api = _api(account=_account(equity=float("inf")), positions=[])
    with caplog.at_level(logging.WARNING):
        ctx = await build_portfolio_context(api, _guardian())
    assert ctx is None


@pytest.mark.anyio
async def test_nan_market_value_position_is_skipped(caplog):
    # NaN market_value must not appear in symbol_weights — skip and warn.
    api = _api(
        account=_account(equity=100000.0),
        positions=[_position("AAPL", float("nan")), _position("MSFT", 5000.0)],
    )
    with caplog.at_level(logging.WARNING):
        ctx = await build_portfolio_context(api, _guardian())
    assert ctx is not None
    assert "AAPL" not in ctx["symbol_weights"]
    assert ctx["symbol_weights"]["MSFT"] == pytest.approx(0.05)


@pytest.mark.anyio
async def test_inf_market_value_position_is_skipped(caplog):
    # Inf market_value is corrupted broker data — skip and warn.
    api = _api(
        account=_account(equity=100000.0),
        positions=[_position("TSLA", float("inf")), _position("MSFT", 5000.0)],
    )
    with caplog.at_level(logging.WARNING):
        ctx = await build_portfolio_context(api, _guardian())
    assert ctx is not None
    assert "TSLA" not in ctx["symbol_weights"]
    assert ctx["symbol_weights"]["MSFT"] == pytest.approx(0.05)
