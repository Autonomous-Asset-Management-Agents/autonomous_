# tests/unit/test_momentum_fallback_abstain.py
# #1968 (RT-BUG-2) — MomentumAgent: one-bar fallback → ABSTAIN (TDD Red→Green).
#
# The one-bar fallback (close-open)/open is direction-identical to RegimeDetection's
# close/open ratio: with <40 daily closes (12-1M path unavailable) the Momentum vote
# is a silent ECHO of the regime signal that also conditions its weight (consensus.py
# bearish-regime halving) — a self-referential coupling, not an independent voice.
# Fix (plan Option B): behind MOMENTUM_FALLBACK_ABSTAIN_ENABLED (default OFF =
# byte-identical) the fallback ABSTAINS (score 0.5, weight 0.0, excluded from
# consensus — the VIXAware/SpecialistAlpha abstention pattern) instead of echoing.
#
# Plan §8: R1 flag-off regression · R2 flag-on abstain + WARNING · R3 12-1M path
# untouched · R4 echo-correlation removed structurally · config parity.

from __future__ import annotations

import importlib.util
import logging
import statistics
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_AI_BOT = Path(__file__).resolve().parents[2]  # ai_trading_bot/


def _state(symbol: str = "AAPL", open_: float = 100.0, close: float = 104.0) -> dict:
    return {
        "symbol": symbol,
        "ohlc": {
            "open": open_,
            "high": max(open_, close),
            "low": min(open_, close),
            "close": close,
            "volume": 1_000_000.0,
        },
        "vix": None,
        "market_data_keys": [],
        "current_time": "2026-03-10T06:00:00+00:00",
        "signal": None,
        "error": None,
        "round_table_scores": None,
        "consensus_ranking": None,
    }


@pytest.fixture(autouse=True)
def _fresh_warn_cache():
    """The once-per-symbol ABSTAIN-WARNING throttle (audit follow-up) is session-scoped
    module state — clear it around every test so each test sees a fresh session
    (order independence; R2 must always observe the FIRST warning per symbol)."""
    from core.round_table import agents

    agents._MOMENTUM_ABSTAIN_WARNED.clear()
    yield
    agents._MOMENTUM_ABSTAIN_WARNED.clear()


def _arm(monkeypatch, enabled: bool) -> None:
    """Pin the flag via config.get_config (the finance-core's only config seam)."""
    import config

    monkeypatch.setattr(
        config,
        "get_config",
        lambda: SimpleNamespace(MOMENTUM_FALLBACK_ABSTAIN_ENABLED=enabled),
    )


def _no_history(monkeypatch) -> None:
    """No active strategy → data_provider None → the 12-1M primary path is skipped
    (structurally equivalent to <40 daily closes: the agent lands in the fallback)."""
    import core.agent_registry as agent_registry

    monkeypatch.setattr(agent_registry, "get_global_registry", lambda: None)


def _short_history(monkeypatch, n_closes: int) -> MagicMock:
    """Registry WITH a data_provider that returns n_closes daily closes."""
    pd = pytest.importorskip("pandas")
    import core.agent_registry as agent_registry

    provider = MagicMock()
    provider.get_data.return_value = pd.DataFrame(
        {"Close": [100.0 + 0.1 * i for i in range(n_closes)]}
    )
    active = SimpleNamespace(data_provider=provider)
    registry = SimpleNamespace(get_active=lambda: active)
    monkeypatch.setattr(agent_registry, "get_global_registry", lambda: registry)
    return provider


# ---------------------------------------------------------------------------
# R1 — flag OFF (default): the one-bar fallback is byte-identical (BORA)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_r1_flag_off_keeps_one_bar_fallback(monkeypatch):
    from core.round_table.agents import MomentumAgent

    _arm(monkeypatch, enabled=False)
    _no_history(monkeypatch)
    r = await MomentumAgent().vote(_state(open_=100.0, close=104.0))
    # (104-100)/100 = 0.04 → score = 0.5 + 0.04*5 = 0.70, full default weight 0.45
    assert r.weight == pytest.approx(0.45)
    assert r.score == pytest.approx(0.70, abs=1e-9)
    # #2408: reasoning is plain English now — the [src: …] anchor marks the
    # one-bar-fallback path (was "1-Bar Fallback").
    assert "[src: today's price bar]" in r.reasoning
    assert "EXCLUDED" not in r.reasoning


# ---------------------------------------------------------------------------
# R2 — flag ON, <40 closes: ABSTAIN (weight 0, EXCLUDED reasoning, WARNING log)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_r2_flag_on_insufficient_history_abstains(monkeypatch, caplog):
    from core.round_table.agents import MomentumAgent

    _arm(monkeypatch, enabled=True)
    _no_history(monkeypatch)
    with caplog.at_level(logging.WARNING):
        r = await MomentumAgent().vote(_state(open_=100.0, close=104.0))
    assert r.weight == 0.0
    assert r.score == 0.5
    assert r.vetoed is False
    assert "EXCLUDED" in r.reasoning
    # §5.6: fallback substitution is logged at WARNING, never DEBUG
    assert any(
        rec.levelno == logging.WARNING and "ABSTAIN" in rec.getMessage()
        for rec in caplog.records
    ), "expected a WARNING log announcing the ABSTAIN substitution"


@pytest.mark.anyio
async def test_r2c_abstain_warning_throttled_once_per_symbol(monkeypatch, caplog):
    """Audit follow-up: the ABSTAIN WARNING is throttled once-per-symbol-per-session.
    On history-poor desktops the fallback fires for most of the ~500-symbol universe
    every cycle — the first abstain per symbol stays WARNING (§5.6), repeats for the
    SAME symbol drop to DEBUG; a NEW symbol gets its own first WARNING."""
    from core.round_table.agents import MomentumAgent

    _arm(monkeypatch, enabled=True)
    _no_history(monkeypatch)
    with caplog.at_level(logging.DEBUG, logger="core.round_table.agents"):
        r1 = await MomentumAgent().vote(_state(symbol="AAPL"))
        r2 = await MomentumAgent().vote(_state(symbol="AAPL"))  # repeat → DEBUG
        r3 = await MomentumAgent().vote(_state(symbol="MSFT"))  # new symbol → WARNING
    assert all(r.weight == 0.0 for r in (r1, r2, r3))  # throttle never skips abstain

    abstains = [r for r in caplog.records if "ABSTAIN" in r.getMessage()]
    warnings = [r for r in abstains if r.levelno == logging.WARNING]
    debugs = [r for r in abstains if r.levelno == logging.DEBUG]
    assert len(warnings) == 2, "exactly ONE warning per symbol per session (2 symbols)"
    assert sorted("AAPL" in r.getMessage() for r in warnings) == [False, True]
    assert len(debugs) == 1 and "AAPL" in debugs[0].getMessage()


@pytest.mark.anyio
async def test_r2b_flag_on_short_series_abstains(monkeypatch):
    """Same abstain when a provider EXISTS but returns <40 closes (the desktop case)."""
    from core.round_table.agents import MomentumAgent

    _arm(monkeypatch, enabled=True)
    _short_history(monkeypatch, n_closes=20)
    r = await MomentumAgent().vote(_state(open_=100.0, close=104.0))
    assert r.weight == 0.0
    assert r.score == 0.5
    assert "EXCLUDED" in r.reasoning


# ---------------------------------------------------------------------------
# R3 — flag ON, >=40 closes: the 12-1M primary path is untouched
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_r3_flag_on_with_history_keeps_12_1m_path(monkeypatch):
    from core.round_table.agents import MomentumAgent

    _arm(monkeypatch, enabled=True)
    _short_history(monkeypatch, n_closes=60)
    r = await MomentumAgent().vote(_state(open_=100.0, close=104.0))
    # closes[i] = 100 + 0.1*i → p_start=closes[0]=100.0, p_end=closes[-20]=104.0
    # momentum_12_1 = 0.04 → score = 0.5 + 0.04/0.60 ≈ 0.5667
    assert r.weight == pytest.approx(0.45)
    assert r.score == pytest.approx(0.5 + 0.04 / 0.60, abs=1e-6)
    # #2408: plain-English reasoning — the [src: …] anchor marks the 12-1M
    # primary path (was "12-1M").
    assert "[src: 12-month price history]" in r.reasoning


@pytest.mark.anyio
async def test_r3b_flag_off_with_history_keeps_12_1m_path(monkeypatch):
    from core.round_table.agents import MomentumAgent

    _arm(monkeypatch, enabled=False)
    _short_history(monkeypatch, n_closes=60)
    r = await MomentumAgent().vote(_state(open_=100.0, close=104.0))
    assert r.weight == pytest.approx(0.45)
    assert r.score == pytest.approx(0.5 + 0.04 / 0.60, abs=1e-6)
    # #2408: plain-English reasoning — the [src: …] anchor marks the 12-1M
    # primary path (was "12-1M").
    assert "[src: 12-month price history]" in r.reasoning


# ---------------------------------------------------------------------------
# R4 — the regime echo is removed STRUCTURALLY (ticket: correlation < 0.9)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_r4_fallback_no_longer_echoes_regime(monkeypatch):
    """Flag OFF documents the bug: over a synthetic O/C series without 12-1M history
    the Momentum fallback is perfectly correlated with RegimeDetection (both are
    monotone in close/open — Pearson ≈ 1.0). Flag ON removes the echo structurally:
    Momentum abstains on every bar (constant 0.5 at weight 0.0), so no direction-
    identical momentum vote reaches the consensus mean at all."""
    from core.round_table.agents import MomentumAgent, RegimeDetectionAgent

    _no_history(monkeypatch)
    # closes chosen so neither agent clamps (scores stay strictly inside (0, 1))
    bars = [92.0, 96.0, 100.5, 103.0, 106.0]

    _arm(monkeypatch, enabled=False)
    mom_off, reg_scores = [], []
    for close in bars:
        st = _state(open_=100.0, close=close)
        mom_off.append((await MomentumAgent().vote(st)).score)
        reg_scores.append((await RegimeDetectionAgent().vote(st)).score)
    assert statistics.correlation(mom_off, reg_scores) > 0.99  # the echo (bug)

    _arm(monkeypatch, enabled=True)
    mom_on = [
        await MomentumAgent().vote(_state(open_=100.0, close=close)) for close in bars
    ]
    assert all(v.weight == 0.0 for v in mom_on)  # excluded from the mean
    assert {v.score for v in mom_on} == {0.5}  # constant → zero co-movement


# ---------------------------------------------------------------------------
# Config parity (BORA): flag exists in BOTH editions, identical default True
# (round-table-v2 activation: the abstain-instead-of-echo path is now the
# default in both editions — env default "True" in config.py and config.oss.py.)
# ---------------------------------------------------------------------------


def _load_oss_config():
    spec = importlib.util.spec_from_file_location(
        "config_oss_momentum_parity", _AI_BOT / "config.oss.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_flag_parity_both_editions_default_true(monkeypatch):
    monkeypatch.delenv("MOMENTUM_FALLBACK_ABSTAIN_ENABLED", raising=False)
    import config as enterprise

    ent = enterprise.get_config()
    oss = _load_oss_config().get_config()
    assert ent.MOMENTUM_FALLBACK_ABSTAIN_ENABLED is True
    assert oss.MOMENTUM_FALLBACK_ABSTAIN_ENABLED is True
