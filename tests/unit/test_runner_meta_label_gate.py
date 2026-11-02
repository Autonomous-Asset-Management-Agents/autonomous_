# tests/unit/test_runner_meta_label_gate.py
# #1955 TRD-4 — runner gate node `_apply_meta_label` (plan §8 R6–R11).
# TDD Red → Green. Mirrors the `_apply_agent_veto` test idiom
# (test_runner_agent_veto_direction_aware.py).
#
# Gherkin (plan §7):
#   - Weak approved BUY (score > BUY_THRESHOLD) + model p < threshold →
#     approved=False with reason "MetaLabel: ..." (audited no_trade).
#   - Strong BUY (p >= threshold) → the ORIGINAL decision passes through.
#   - SELL/HOLD → no-op (carve-out: only real BUYs are ever filtered).
#   - Flag off (default) → no-op, the filter is never consulted (byte-identical).
#   - Full round table: the no_trade path yields signal=None and vetoed votes
#     (the EXISTING approved=False plumbing audits the block reason).

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.round_table.gatekeeper import GatekeeperDecision
from core.round_table.runner import _apply_meta_label


def _vote(name="LSTMSignalAgent", score=0.9, weight=1.0, vetoed=False):
    return SimpleNamespace(
        agent_name=name, score=score, weight=weight, vetoed=vetoed, reasoning=""
    )


def _votes():
    return [
        _vote("LSTMSignalAgent", 0.9),
        _vote("RLConfidenceAgent", 0.9),
        _vote("DrawdownGuardAgent", 0.8),
        _vote("RegimeDetectionAgent", 0.6),
    ]


def _approved(symbol="AAPL"):
    return GatekeeperDecision(approved=True, reason="AllChecksPassed", symbol=symbol)


def _state(symbol="AAPL"):
    return {"symbol": symbol, "ohlc": {"close": 100.0}}


class _MockConfig(SimpleNamespace):
    def __getattr__(self, name: str):
        return False


def _patch_all_configs(monkeypatch, cfg_obj):
    import sys

    if not isinstance(cfg_obj, _MockConfig):
        wrapped = _MockConfig(**vars(cfg_obj))
        cfg_obj = wrapped

    for mod_name, mod in list(sys.modules.items()):
        if mod_name == "config" or mod_name.endswith(".config"):
            if mod is not None and not mod_name.startswith("pydantic"):
                try:
                    if hasattr(mod, "get_config"):
                        monkeypatch.setattr(mod, "get_config", lambda: cfg_obj)
                except Exception:
                    pass


def _arm(
    monkeypatch,
    enabled=True,
    min_proba=0.55,
    path="data/meta_label_model.pkl",
    require_model=False,
):
    _patch_all_configs(
        monkeypatch,
        SimpleNamespace(
            META_LABEL_FILTER_ENABLED=enabled,
            META_LABEL_MIN_PROBA=min_proba,
            META_LABEL_MODEL_PATH=path,
            META_LABEL_REQUIRE_MODEL=require_model,
            GATEKEEPER_REQUIRE_CONTEXT=False,
            DRAWDOWN_GUARD_CONDITIONER_ENABLED=False,
            SHADOW_TFT_VOTE_ENABLED=False,
            GATEKEEPER_STRICT_ML_REQUIRES_RL=False,
        ),
    )


def _fake_filter(monkeypatch, trade, proba, reason):
    import core.round_table.meta_label as ml_mod

    fake = MagicMock()
    fake.should_trade.return_value = (trade, proba, reason)
    monkeypatch.setattr(ml_mod, "get_meta_label_filter", lambda: fake)
    return fake


# ---------------------------------------------------------------------------
# R6 — weak approved BUY → audited no_trade
# ---------------------------------------------------------------------------
def test_weak_buy_downgraded_to_no_trade(monkeypatch):
    _arm(monkeypatch)
    _fake_filter(monkeypatch, False, 0.31, "p=0.31 < 0.55 (no edge net-of-cost)")

    out = _apply_meta_label(_approved(), _state(), _votes(), 0.70, "AAPL")

    assert out.approved is False
    assert out.reason.startswith("MetaLabel: ")
    assert "0.31" in out.reason and "0.55" in out.reason
    assert out.symbol == "AAPL"


# ---------------------------------------------------------------------------
# R7 — strong BUY passes through UNCHANGED (same object)
# ---------------------------------------------------------------------------
def test_strong_buy_passes_original_decision(monkeypatch):
    _arm(monkeypatch)
    _fake_filter(monkeypatch, True, 0.82, "p=0.82 >= 0.55")

    base = _approved()
    out = _apply_meta_label(base, _state(), _votes(), 0.70, "AAPL")
    assert out is base


# ---------------------------------------------------------------------------
# R8 — SELL / HOLD carve-out: never touched, filter never consulted
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("score", [0.28, 0.50, 0.65])
def test_sell_and_hold_are_noop(monkeypatch, score):
    _arm(monkeypatch)
    fake = _fake_filter(monkeypatch, False, 0.0, "must never be read")

    base = _approved()
    out = _apply_meta_label(base, _state(), _votes(), score, "AAPL")
    assert out is base
    fake.should_trade.assert_not_called()


def test_unapproved_decision_is_noop(monkeypatch):
    """Already-blocked decisions (gatekeeper/veto) are passed through untouched."""
    _arm(monkeypatch)
    fake = _fake_filter(monkeypatch, False, 0.0, "must never be read")

    base = GatekeeperDecision(approved=False, reason="Agent VETO (X): y", symbol="A")
    out = _apply_meta_label(base, _state(), _votes(), 0.70, "A")
    assert out is base
    fake.should_trade.assert_not_called()


# ---------------------------------------------------------------------------
# R9 — flag off (default) → no-op, byte-identical
# ---------------------------------------------------------------------------
def test_flag_off_is_noop_and_filter_untouched(monkeypatch):
    _arm(monkeypatch, enabled=False)
    fake = _fake_filter(monkeypatch, False, 0.0, "must never be read")

    base = _approved()
    out = _apply_meta_label(base, _state(), _votes(), 0.70, "AAPL")
    assert out is base
    fake.should_trade.assert_not_called()


def test_default_config_flag_is_off():
    """Shipped default MUST be OFF (BORA byte-identical guarantee)."""
    from config import RuntimeConfigState

    assert RuntimeConfigState().META_LABEL_FILTER_ENABLED is False


# ---------------------------------------------------------------------------
# R11 — filter exception → fail-open (never blocks the order path)
# ---------------------------------------------------------------------------
def test_filter_exception_fails_open(monkeypatch, caplog):
    import logging

    _arm(monkeypatch)
    import core.round_table.meta_label as ml_mod

    def _boom():
        raise RuntimeError("model exploded")

    monkeypatch.setattr(ml_mod, "get_meta_label_filter", _boom)

    base = _approved()
    with caplog.at_level(logging.WARNING, logger="core.round_table.runner"):
        out = _apply_meta_label(base, _state(), _votes(), 0.70, "AAPL")
    assert out is base
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_filter_exception_fails_closed_when_require_model(monkeypatch, caplog):
    import logging

    _arm(monkeypatch, require_model=True)
    import core.round_table.meta_label as ml_mod

    def _boom():
        raise RuntimeError("model exploded")

    monkeypatch.setattr(ml_mod, "get_meta_label_filter", _boom)

    base = _approved()
    out = _apply_meta_label(base, _state(), _votes(), 0.70, "AAPL")
    assert out.approved is False
    assert "fail-closed" in out.reason


# ---------------------------------------------------------------------------
# R10 — full round table: no_trade path → signal=None + vetoed votes (audit)
# ---------------------------------------------------------------------------
def _stub_agents(monkeypatch):
    """Real LSTM/RL/DG/RD instances (isinstance strict-ML gate) with stubbed votes."""
    import core.round_table.agents as agents_mod
    from core.round_table import runner
    from core.round_table.base_agent import VoteResult

    agents = [
        object.__new__(runner.LSTMSignalAgent),
        object.__new__(runner.RLConfidenceAgent),
        object.__new__(agents_mod.DrawdownGuardAgent),
        object.__new__(agents_mod.RegimeDetectionAgent),
    ]
    scores = {
        "LSTMSignalAgent": 0.9,
        "RLConfidenceAgent": 0.9,
        "DrawdownGuardAgent": 0.8,
        "RegimeDetectionAgent": 0.6,
    }
    for agent in agents:
        name = agent.__class__.__name__
        monkeypatch.setattr(
            agent,
            "vote",
            AsyncMock(
                return_value=VoteResult(
                    agent_name=name,
                    symbol="AAPL",
                    score=scores[name],
                    weight=1.0,
                    reasoning=f"stub {name}",
                )
            ),
        )
    return agents


def _full_state():
    return {
        "symbol": "AAPL",
        "ohlc": {
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 1_000_000.0,
        },
        "market_data_keys": [],
        "current_time": "2026-07-23T07:00:00+00:00",
        "signal": None,
        "error": None,
        "round_table_scores": None,
        "consensus_ranking": None,
    }


def _arm_full(monkeypatch, enabled=True):
    _patch_all_configs(
        monkeypatch,
        SimpleNamespace(
            META_LABEL_FILTER_ENABLED=enabled,
            META_LABEL_MIN_PROBA=0.55,
            META_LABEL_MODEL_PATH="data/meta_label_model.pkl",
            META_LABEL_REQUIRE_MODEL=False,
            GATEKEEPER_REQUIRE_CONTEXT=False,
            DRAWDOWN_GUARD_CONDITIONER_ENABLED=False,
            SHADOW_TFT_VOTE_ENABLED=False,
            GATEKEEPER_STRICT_ML_REQUIRES_RL=False,
        ),
    )


@pytest.mark.anyio
async def test_round_table_no_trade_path_audits_block(monkeypatch):
    from core.round_table import runner

    runner.boot_engine(None)
    monkeypatch.setattr(runner, "_active_agents", _stub_agents(monkeypatch))
    senate = MagicMock()
    senate.log_session = AsyncMock()
    monkeypatch.setattr(runner, "_senate", senate)
    _arm_full(monkeypatch)
    _fake_filter(monkeypatch, False, 0.31, "p=0.31 < 0.55 (no edge net-of-cost)")

    result = await runner.run_round_table(_full_state())

    # _score_to_signal was never reached → no signal object at all
    assert result["signal"] is None
    # Existing approved=False plumbing marked every vote vetoed (Senate audit)
    assert result["round_table_scores"], "votes must be serialized"
    assert all(v["vetoed"] for v in result["round_table_scores"])


@pytest.mark.anyio
async def test_round_table_strong_buy_still_buys(monkeypatch):
    from core.round_table import runner

    runner.boot_engine(None)
    monkeypatch.setattr(runner, "_active_agents", _stub_agents(monkeypatch))
    senate = MagicMock()
    senate.log_session = AsyncMock()
    monkeypatch.setattr(runner, "_senate", senate)
    _arm_full(monkeypatch)
    _fake_filter(monkeypatch, True, 0.82, "p=0.82 >= 0.55")

    result = await runner.run_round_table(_full_state())

    assert result["signal"] is not None
    assert result["signal"].action == "BUY"


@pytest.mark.anyio
async def test_round_table_flag_off_byte_identical_buy(monkeypatch):
    """Flag OFF: a blocking filter (were it consulted) changes NOTHING — BUY stands."""
    from core.round_table import runner

    runner.boot_engine(None)
    monkeypatch.setattr(runner, "_active_agents", _stub_agents(monkeypatch))
    senate = MagicMock()
    senate.log_session = AsyncMock()
    monkeypatch.setattr(runner, "_senate", senate)
    _arm_full(monkeypatch, enabled=False)
    fake = _fake_filter(monkeypatch, False, 0.0, "must never be read")

    result = await runner.run_round_table(_full_state())

    assert result["signal"] is not None
    assert result["signal"].action == "BUY"
    fake.should_trade.assert_not_called()
