"""DrawdownGuard HARD-VETO master gate (DRAWDOWN_GUARD_VETO_ENABLED).

Pins: default (True) => the >7%/>5% veto still fires (byte-identical shipped
behaviour); False => the guard votes its soft score but NEVER hard-blocks a BUY.
Uses the 1-bar fallback path (no data provider) with a big intra-bar swing
(>5%) so the veto is deterministic without mocking history.
"""

from __future__ import annotations

import asyncio

import pytest

import config
from core.round_table.agents import DrawdownGuardAgent, _drawdown_guard_veto_enabled

_NOW = "2026-03-10T06:00:00+00:00"


@pytest.fixture(autouse=True)
def _force_1bar_fallback(monkeypatch):
    """No active strategy -> data_provider is None -> the agent takes the 1-bar
    fallback (judges on today's high/low), so the test needs no history mock."""
    monkeypatch.setattr("core.round_table.agents.get_global_registry", lambda: None)


def _state() -> dict:
    # intra-bar range (100-90)/100 = 10% > the 5% fallback veto threshold
    return {
        "symbol": "TSTX",
        "ohlc": {
            "open": 95.0,
            "high": 100.0,
            "low": 90.0,
            "close": 92.0,
            "volume": 1e6,
        },
        "vix": None,
        "market_data_keys": [],
        "current_time": _NOW,
        "signal": None,
        "error": None,
        "round_table_scores": None,
        "consensus_ranking": None,
    }


def _vote(state):
    return asyncio.run(DrawdownGuardAgent().vote(state))


# --- helper contract ---
def test_helper_default_true(monkeypatch):
    cfg = config.get_config()
    monkeypatch.setattr(cfg, "DRAWDOWN_GUARD_VETO_ENABLED", True, raising=False)
    assert _drawdown_guard_veto_enabled() is True


def test_helper_false(monkeypatch):
    cfg = config.get_config()
    monkeypatch.setattr(cfg, "DRAWDOWN_GUARD_VETO_ENABLED", False, raising=False)
    assert _drawdown_guard_veto_enabled() is False


# --- shipped behaviour: default True => veto fires on a >5% intra-bar drawdown ---
def test_veto_fires_when_enabled(monkeypatch):
    cfg = config.get_config()
    monkeypatch.setattr(cfg, "DRAWDOWN_GUARD_VETO_ENABLED", True, raising=False)
    monkeypatch.setattr(cfg, "DRAWDOWN_GUARD_CONDITIONER_ENABLED", False, raising=False)
    vote = _vote(_state())
    assert vote.vetoed is True


# --- removal: flag False => NO veto, but the soft score is still cast ---
def test_no_veto_when_disabled(monkeypatch):
    cfg = config.get_config()
    monkeypatch.setattr(cfg, "DRAWDOWN_GUARD_VETO_ENABLED", False, raising=False)
    monkeypatch.setattr(cfg, "DRAWDOWN_GUARD_CONDITIONER_ENABLED", False, raising=False)
    vote = _vote(_state())
    assert vote.vetoed is False
    # the guard still participates: a real drawdown score is emitted (1 - dd*5),
    # here dd=0.10 -> score 0.5; the agent is not silenced, only un-vetoed.
    assert vote.score == pytest.approx(0.5, abs=1e-6)
    assert vote.agent_name == "DrawdownGuardAgent"
