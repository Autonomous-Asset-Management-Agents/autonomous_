# tests/unit/test_drawdown_guard_window.py
# #2584 (P0) — DrawdownGuardAgent: "30-day high" was computed over the UNCUT
# provider frame. data_provider.get_data(days=30) treats `days` as a MINIMUM
# depth (the fetch stores +200 calendar days; the disk cache serves ANY deeper
# frame — data_provider.py:430 "Deeper history than requested is fine"), so
# `closes.max()` was a multi-year-peak check. Field audit (Audit Chain,
# 2026-07-30): SNDK "30-day high 2335.46, now 1250" (-46.5%), TYL 613.11/321.99,
# INTU 807.10/304.75, CRWD -76.8% — all long-term peaks → veto wall, no BUY
# passes although the engine logs "[DHR] LSTM+RL AGREE: BUY!".
#
# TDD Red→Green: these tests pin the ADR-R14 window — the drawdown peak MUST
# come from the LAST 30 TRADING DAYS (bars) only, and the audit reasoning must
# quote the peak of that real window.
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

_NOW = "2026-03-10T06:00:00+00:00"


@pytest.fixture(autouse=True)
def _pin_conditioner_off(monkeypatch):
    """Pin the #1951 conditioner OFF on the real config singleton so the veto
    threshold on the history path is the deterministic 0.07 (shipped default)."""
    import config

    cfg = config.get_config()
    monkeypatch.setattr(cfg, "DRAWDOWN_GUARD_CONDITIONER_ENABLED", False, raising=False)


def _state(symbol: str = "SNDK", close: float = 1250.0) -> dict:
    return {
        "symbol": symbol,
        "ohlc": {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 1_000_000.0,
        },
        "vix": None,
        "market_data_keys": [],
        "current_time": _NOW,
        "signal": None,
        "error": None,
        "round_table_scores": None,
        "consensus_ranking": None,
    }


def _frame(closes: list) -> pd.DataFrame:
    """Daily-bar frame like the provider returns: ascending DatetimeIndex of
    trading days, already filtered to index <= end_date (data_provider.py)."""
    idx = pd.bdate_range(end="2026-03-10", periods=len(closes))
    return pd.DataFrame({"Close": [float(c) for c in closes]}, index=idx)


def _registry(df: pd.DataFrame):
    """Mock agent registry: get_active().data_provider.get_data → df.
    get_data is SYNC in production (called via asyncio.to_thread) → MagicMock."""
    registry = MagicMock()
    active = MagicMock()
    active.data_provider.get_data = MagicMock(return_value=df)
    registry.get_active.return_value = active
    return registry, active


async def _vote(df: pd.DataFrame, state: dict):
    from core.round_table.agents import DrawdownGuardAgent

    registry, active = _registry(df)
    # vote() imports get_global_registry from core.agent_registry at call time
    # → patch the source module, not the round_table.agents alias.
    with patch("core.agent_registry.get_global_registry", return_value=registry):
        result = await DrawdownGuardAgent().vote(state)
    return result, active


@pytest.mark.anyio
async def test_peak_outside_30_trading_days_does_not_veto():
    """(a) SNDK field case: long-ago peak 2335.46 (60 trading days back), last
    30 trading days flat around 1250 → the guard must NOT veto and must NOT
    quote the ancient peak."""
    old_leg = [2335.46 - i * 18.0 for i in range(60)]  # 2335.46 → ~1273, all >30d old
    recent = [1245.0, 1255.0] * 15  # last 30 bars: window peak 1255.0
    result, _ = await _vote(_frame(old_leg + recent), _state("SNDK", close=1250.0))

    assert result.vetoed is False, (
        "peak lies OUTSIDE the last 30 trading days — a -46.5% 'drawdown' from "
        f"the uncut series must not veto: {result.reasoning}"
    )
    assert "within limits" in result.reasoning
    assert "2335.46" not in result.reasoning, (
        "audit text must quote the real 30-trading-day window peak, not the "
        f"multi-year high: {result.reasoning}"
    )
    assert "peak 1255.00" in result.reasoning
    assert result.score > 0.9  # dd (1255-1250)/1255 ≈ 0.4% → score ≈ 0.98


@pytest.mark.anyio
async def test_window_boundary_bar_31_ago_is_excluded():
    """(a') Sharp boundary: a spike exactly 31 trading days ago is OUTSIDE the
    window; the last 30 bars are flat → no veto."""
    closes = [100.0] * 59 + [999.0] + [100.0] * 30  # spike = 31st-from-last bar
    result, _ = await _vote(_frame(closes), _state("X", close=100.0))

    assert result.vetoed is False, result.reasoning
    assert "999.00" not in result.reasoning
    assert "peak 100.00" in result.reasoning


@pytest.mark.anyio
async def test_peak_inside_30_trading_days_still_vetoes():
    """(b) TYL field case: peak 613.11 INSIDE the last 30 trading days, price
    321.99 far below → the veto must stay."""
    old_leg = [330.0] * 60
    recent = [613.11 - i * 10.0 for i in range(30)]  # window peak 613.11 → 323.11
    result, _ = await _vote(_frame(old_leg + recent), _state("TYL", close=321.99))

    assert result.vetoed is True, result.reasoning
    assert "blocks new buying" in result.reasoning
    assert "peak 613.11" in result.reasoning


@pytest.mark.anyio
async def test_near_high_stays_within_limits():
    """(c) AAPL field counter-case: close near the window high → 'within
    limits', no veto (this behaviour was already correct and must not regress)."""
    closes = [250.0 + i * 1.0 for i in range(90)]  # rising into peak 339.0
    result, _ = await _vote(_frame(closes), _state("AAPL", close=333.2))

    assert result.vetoed is False, result.reasoning
    assert "within limits" in result.reasoning
    assert result.score > 0.85  # dd (339-333.2)/339 ≈ 1.7%


@pytest.mark.anyio
async def test_request_window_stays_30_days_cache_share():
    """#2389 regression guard: the provider request stays get_data(sym, t, 30)
    so the compute_features node keeps hitting the same provider-cache entry
    (graph.py:_FEATURE_WINDOW_DAYS = 30). The FIX is the local tail-slice, not
    a bigger fetch."""
    closes = [100.0] * 90
    _, active = await _vote(_frame(closes), _state("X", close=100.0))

    active.data_provider.get_data.assert_called_once()
    args = active.data_provider.get_data.call_args.args
    assert args[0] == "X"
    assert args[2] == 30


@pytest.mark.anyio
async def test_short_frame_uses_all_bars_and_honest_window_label():
    """A frame shorter than 30 bars (minimal-depth cache) uses ALL its bars —
    a strict SUBSET of the last 30 trading days (never more) — and the audit
    text must state the REAL window size instead of claiming 30 days."""
    closes = [110.0] * 5 + [120.0] + [100.0] * 15  # 21 bars, peak inside
    result, _ = await _vote(_frame(closes), _state("X", close=100.0))

    assert result.vetoed is True  # dd (120-100)/120 ≈ 16.7% > 0.07
    assert "peak 120.00" in result.reasoning
    assert "21-trading-day" in result.reasoning, (
        "window label must reflect the real bar count: " + result.reasoning
    )
