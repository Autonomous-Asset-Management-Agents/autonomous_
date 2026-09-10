# tests/unit/test_regime_conditioner.py
# #1949 (RTR-2) — RegimeDetectionAgent: O/C proxy → real MarketRegimeModel conditioner.
#
# Today RegimeDetectionAgent.vote scores clamp(0.5 + (close/open - 1)*5) — a one-bar
# intraday-return formula that is direction-identical to MomentumAgent's fallback
# (double counting, plan §2.1-2.2). The REAL regime model (core/market_regime.py,
# VIX with SPY fallback) already runs every cycle (monitor_loop.py:106-112) but its
# output never reaches the agent: LangGraph drops the undeclared "vix"/"regime" input
# keys (verified against langgraph==1.0.10 — undeclared keys never reach any node).
#
# Fix (plan Option C, Inc 1) behind REGIME_CONDITIONER_ENABLED (default OFF =
# byte-identical): the agent scores the continuous VIX sigmoid
# clamp(1/(1+exp((vix - REGIME_VIX_MIDPOINT)/10))) (same form as VIXAwareRiskAgent),
# with a regime-label fallback and a neutral ABSTAIN (+WARNING, §5.6) when both are
# missing. State seam: "vix"/"regime" become DECLARED SymbolEvalState channels whose
# producer values are flag-gated (OFF → None ⇒ agents see exactly today's
# dropped-key behaviour).
#
# Plan §8: R1 flag-off regression · R2 flag-on VIX-driven + O/C-independent ·
# R3 low VIX → risk-on · R4 missing vix+regime → abstain + WARNING · R4b regime-label
# fallback · R5 midpoint configurable · R10 state seam · config parity (BORA).

from __future__ import annotations

import importlib.util
import logging
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

_AI_BOT = Path(__file__).resolve().parents[2]  # ai_trading_bot/


def _state(
    symbol: str = "AAPL",
    open_: float = 100.0,
    close: float = 105.0,
    vix=None,
    regime=None,
) -> dict:
    state = {
        "symbol": symbol,
        "ohlc": {
            "open": open_,
            "high": max(open_, close),
            "low": min(open_, close),
            "close": close,
            "volume": 1_000_000.0,
        },
        "market_data_keys": [],
        "current_time": "2026-03-10T06:00:00+00:00",
        "signal": None,
        "error": None,
        "round_table_scores": None,
        "consensus_ranking": None,
        "vix": vix,
        "regime": regime,
    }
    return state


def _arm(monkeypatch, enabled: bool, midpoint: float = 25.0) -> None:
    """Pin the flag via config.get_config (the finance-core's only config seam)."""
    import config

    monkeypatch.setattr(
        config,
        "get_config",
        lambda: SimpleNamespace(
            REGIME_CONDITIONER_ENABLED=enabled,
            REGIME_VIX_MIDPOINT=midpoint,
            REGIME_RISKOFF_DAMP_MAX=0.5,
        ),
    )


def _vix_sigmoid(vix: float, midpoint: float = 25.0) -> float:
    return max(0.0, min(1.0, 1.0 / (1.0 + math.exp((vix - midpoint) / 10.0))))


# ---------------------------------------------------------------------------
# R1 — flag OFF (default): the O/C proxy path is byte-identical (BORA)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_r1_flag_off_keeps_oc_proxy(monkeypatch):
    from core.round_table.agents import RegimeDetectionAgent

    _arm(monkeypatch, enabled=False)
    r = await RegimeDetectionAgent().vote(_state(open_=100.0, close=105.0, vix=40.0))
    # ratio 1.05 → score = 0.5 + 0.05*5 = 0.75 — the exact 0.1.x O/C path,
    # even though a crisis VIX (40) sits in the state (flag off ⇒ ignored).
    assert r.score == pytest.approx(0.75, abs=1e-9)
    assert r.weight == pytest.approx(0.50)
    assert "[src: today's open/close]" in r.reasoning


@pytest.mark.anyio
async def test_r1b_flag_off_zero_open_neutral(monkeypatch):
    from core.round_table.agents import RegimeDetectionAgent

    _arm(monkeypatch, enabled=False)
    r = await RegimeDetectionAgent().vote(_state(open_=0.0, close=105.0))
    assert r.score == pytest.approx(0.5)
    assert r.weight == pytest.approx(0.50)


# ---------------------------------------------------------------------------
# R2 — flag ON: score comes from the real regime signal, NOT from O/C
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_r2_flag_on_vix_driven_not_oc(monkeypatch):
    from core.round_table.agents import RegimeDetectionAgent

    _arm(monkeypatch, enabled=True)
    # Crisis VIX 40 on a BULLISH bar (close/open = 1.05): the O/C proxy would say
    # 0.75 — the real conditioner must say risk-off (~0.18).
    r = await RegimeDetectionAgent().vote(_state(open_=100.0, close=105.0, vix=40.0))
    assert r.score == pytest.approx(_vix_sigmoid(40.0), abs=1e-6)  # ≈ 0.1824
    assert r.score < 0.25
    assert r.weight == pytest.approx(0.50)
    assert "CONDITIONER" in r.reasoning
    assert "[src: VIX" in r.reasoning


@pytest.mark.anyio
async def test_r2b_flag_on_score_independent_of_oc(monkeypatch):
    from core.round_table.agents import RegimeDetectionAgent

    _arm(monkeypatch, enabled=True)
    bull = await RegimeDetectionAgent().vote(_state(open_=100.0, close=105.0, vix=30.0))
    bear = await RegimeDetectionAgent().vote(_state(open_=100.0, close=95.0, vix=30.0))
    # No momentum double counting: identical VIX ⇒ identical score, whatever the bar.
    assert bull.score == pytest.approx(bear.score, abs=1e-12)


@pytest.mark.anyio
async def test_r3_flag_on_low_vix_risk_on(monkeypatch):
    from core.round_table.agents import RegimeDetectionAgent

    _arm(monkeypatch, enabled=True)
    r = await RegimeDetectionAgent().vote(_state(vix=12.0))
    assert r.score == pytest.approx(_vix_sigmoid(12.0), abs=1e-6)  # ≈ 0.7858
    assert r.score > 0.7


# ---------------------------------------------------------------------------
# R4 — flag ON, no vix AND no regime → neutral ABSTAIN + WARNING (fail-open)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_r4_flag_on_missing_all_abstains_with_warning(monkeypatch, caplog):
    from core.round_table.agents import RegimeDetectionAgent

    _arm(monkeypatch, enabled=True)
    with caplog.at_level(logging.WARNING, logger="core.round_table.agents"):
        r = await RegimeDetectionAgent().vote(_state(vix=None, regime=None))
    assert r.score == 0.5
    assert r.weight == 0.0  # abstain → excluded, NO conditioning happens
    assert r.vetoed is False
    # §5.6: fallback substitution logged at WARNING, never DEBUG
    assert any(
        rec.levelno == logging.WARNING and "RegimeDetectionAgent" in rec.getMessage()
        for rec in caplog.records
    ), "expected a WARNING announcing the abstain"


@pytest.mark.anyio
async def test_r4b_flag_on_regime_label_fallback(monkeypatch):
    """vix missing/invalid → the regime LABEL (monitor_loop seam) still conditions,
    mapped through the same sigmoid via the band-representative VIX levels."""
    from core.round_table.agents import RegimeDetectionAgent

    _arm(monkeypatch, enabled=True)
    high = await RegimeDetectionAgent().vote(_state(vix=None, regime="High Volatility"))
    low = await RegimeDetectionAgent().vote(_state(vix=None, regime="Low Volatility"))
    mid = await RegimeDetectionAgent().vote(_state(vix=None, regime="Ranging"))
    trend = await RegimeDetectionAgent().vote(_state(vix=None, regime="Trending"))
    assert high.score < 0.35  # risk-off
    assert low.score > 0.65  # risk-on
    assert low.score > mid.score > trend.score > high.score  # monotone in the bands
    assert all(v.weight == pytest.approx(0.50) for v in (high, low, mid, trend))
    assert "[src: regime label" in high.reasoning


@pytest.mark.anyio
async def test_r4c_flag_on_unknown_label_abstains(monkeypatch, caplog):
    from core.round_table.agents import RegimeDetectionAgent

    _arm(monkeypatch, enabled=True)
    with caplog.at_level(logging.WARNING, logger="core.round_table.agents"):
        r = await RegimeDetectionAgent().vote(_state(vix=0.0, regime="Unknown"))
    assert r.score == 0.5
    assert r.weight == 0.0


# ---------------------------------------------------------------------------
# R5 — REGIME_VIX_MIDPOINT is configurable
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_r5_midpoint_configurable(monkeypatch):
    from core.round_table.agents import RegimeDetectionAgent

    _arm(monkeypatch, enabled=True, midpoint=40.0)
    r = await RegimeDetectionAgent().vote(_state(vix=40.0))
    assert r.score == pytest.approx(0.5, abs=1e-9)  # vix == midpoint ⇒ neutral


# ---------------------------------------------------------------------------
# R10 — state seam: declared channels + flag-gated producer values
# ---------------------------------------------------------------------------


def test_r10a_symbol_eval_state_declares_vix_and_regime():
    """LangGraph 1.0.10 DROPS undeclared input keys before any node runs (verified
    empirically). The seam therefore requires "vix" and "regime" as DECLARED
    SymbolEvalState channels — cf. the _portfolio_context lesson (graph.py)."""
    from core.orchestration.graph import SymbolEvalState

    annotations = SymbolEvalState.__annotations__
    assert "vix" in annotations
    assert "regime" in annotations


def test_r10b_producer_seam_flag_off_emits_none(monkeypatch):
    """OFF (default): the producer emits None for both channels so agents observe
    exactly today's dropped-key behaviour (VIXAware keeps abstaining) —
    byte-identical dark ship."""
    import config
    from core.engine.trading_loop import _regime_conditioner_state_keys

    monkeypatch.setattr(
        config,
        "get_config",
        lambda: SimpleNamespace(REGIME_CONDITIONER_ENABLED=False),
    )
    out = _regime_conditioner_state_keys({"vix": 22.5, "regime": "Ranging"})
    assert out == {"vix": None, "regime": None}


def test_r10c_producer_seam_flag_on_threads_values(monkeypatch):
    import config
    from core.engine.trading_loop import _regime_conditioner_state_keys

    monkeypatch.setattr(
        config,
        "get_config",
        lambda: SimpleNamespace(REGIME_CONDITIONER_ENABLED=True),
    )
    out = _regime_conditioner_state_keys({"vix": 22.5, "regime": "Ranging"})
    assert out == {"vix": 22.5, "regime": "Ranging"}
    # Robust against a missing/None market-data snapshot (engine warmup)
    assert _regime_conditioner_state_keys(None) == {"vix": None, "regime": None}


# ---------------------------------------------------------------------------
# Config parity (BORA): 3 keys in BOTH editions, identical env names + defaults
# ---------------------------------------------------------------------------


def _load_oss_config():
    spec = importlib.util.spec_from_file_location(
        "config_oss_regime_parity", _AI_BOT / "config.oss.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_config_parity_both_editions(monkeypatch):
    for key in (
        "REGIME_CONDITIONER_ENABLED",
        "REGIME_RISKOFF_DAMP_MAX",
        "REGIME_VIX_MIDPOINT",
    ):
        monkeypatch.delenv(key, raising=False)
    import config as enterprise

    ent = enterprise.get_config()
    oss = _load_oss_config().get_config()
    # round-table-v2 constellation activated: REGIME_CONDITIONER_ENABLED default
    # flipped OFF→ON in BOTH editions (config.py / config.oss.py). Parity preserved.
    assert ent.REGIME_CONDITIONER_ENABLED is True
    assert oss.REGIME_CONDITIONER_ENABLED is True
    assert ent.REGIME_RISKOFF_DAMP_MAX == pytest.approx(0.5)
    assert oss.REGIME_RISKOFF_DAMP_MAX == pytest.approx(0.5)
    assert ent.REGIME_VIX_MIDPOINT == pytest.approx(25.0)
    assert oss.REGIME_VIX_MIDPOINT == pytest.approx(25.0)
