# tests/unit/test_drawdown_guard_conditioner.py
# #1951 / plan #2205 — DrawdownGuard: hard-veto → conditioner (+severe backstop).
# TDD Red→Green. The DrawdownGuardAgent hard-vetoed every name >7% below its 30d high,
# overriding max-conviction ML BUYs (MSFT: RL conv=1.0 + LSTM 0.83, 20% dip → blocked).
# Hybrid B+C: in the 7–25% band it DAMPENS the BUY conviction/size instead of blocking;
# > DRAWDOWN_SEVERE_VETO_THRESHOLD still hard-blocks. Flag off ⇒ byte-identical.
#
# #1951 (conviction gate + default flip): live forensics (13.–22.07.2026, 8017
# decisions) showed the damping was COSMETIC — action="BUY" was fixed from the RAW
# score before damping, and risk_manager's MIN_POSITION_PERCENT floor executed 708
# BUYs at ~0.1x damped conviction anyway. Fix: the DAMPED conviction gates the
# action (below SIGNAL_BUY_THRESHOLD ⇒ HOLD, audited), and the shipped default is
# back to OFF (audited hard-veto posture, BORA byte-identical-at-off restored).
from __future__ import annotations

import importlib
import importlib.util
import logging
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[2]


def _state(symbol="X", high=100.0, low=100.0, close=100.0):
    # No agent_registry → DrawdownGuardAgent uses the 1-bar fallback (H-L)/H, which the
    # three-regime veto also governs (agents.py:191-192). Same code path both editions.
    return {
        "symbol": symbol,
        "ohlc": {
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1e6,
        },
        "vix": None,
        "market_data_keys": [],
        "current_time": "2026-03-10T06:00:00+00:00",
        "signal": None,
        "error": None,
    }


def _arm_agent(monkeypatch, enabled=True, damp=0.8, severe=0.25):
    import config

    monkeypatch.setattr(
        config,
        "get_config",
        lambda: SimpleNamespace(
            DRAWDOWN_GUARD_CONDITIONER_ENABLED=enabled,
            DRAWDOWN_CONVICTION_DAMP_MAX=damp,
            DRAWDOWN_SEVERE_VETO_THRESHOLD=severe,
        ),
    )


# ---------------------------------------------------------------------------
# Agent: three-regime veto (1-bar fallback path, agents.py:191-192)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_flag_off_keeps_old_hard_veto(monkeypatch):
    from core.round_table.agents import DrawdownGuardAgent

    # Explicitly force the flag OFF (independent of the shipped default, which is ON
    # since the #2208 activation) → old behaviour: dd 0.20 > 0.05 hard-vetoes.
    _arm_agent(monkeypatch, enabled=False)
    r = await DrawdownGuardAgent().vote(_state(high=100.0, low=80.0, close=80.0))
    assert r.vetoed is True


@pytest.mark.anyio
async def test_flag_on_moderate_drawdown_not_vetoed(monkeypatch):
    _arm_agent(monkeypatch)
    # dd 0.20 < severe 0.25 → conditioner: no veto, but score collapses to 0.0 (signal)
    r = await DrawdownGuardAgent_vote(high=100.0, low=80.0, close=80.0)
    assert r.vetoed is False
    assert r.score == pytest.approx(0.0, abs=1e-6)


@pytest.mark.anyio
async def test_flag_on_severe_drawdown_still_vetoed(monkeypatch):
    _arm_agent(monkeypatch)
    # dd (100-70)/100 = 0.30 > severe 0.25 → hard-block retained
    r = await DrawdownGuardAgent_vote(high=100.0, low=70.0, close=70.0)
    assert r.vetoed is True


@pytest.mark.anyio
async def test_severe_threshold_is_configurable(monkeypatch):
    _arm_agent(monkeypatch, severe=0.15)
    # dd 0.20 > severe 0.15 → vetoed
    r = await DrawdownGuardAgent_vote(high=100.0, low=80.0, close=80.0)
    assert r.vetoed is True


async def DrawdownGuardAgent_vote(**kw):
    from core.round_table.agents import DrawdownGuardAgent

    return await DrawdownGuardAgent().vote(_state(**kw))


# ---------------------------------------------------------------------------
# runner._score_to_signal: conviction damping + audit trail + fail-open
# ---------------------------------------------------------------------------


def _votes(dg_score=0.0):
    return [
        SimpleNamespace(
            agent_name="DrawdownGuardAgent",
            score=dg_score,
            weight=0.60,
            reasoning="DrawdownGuard: VETO=False",
            vetoed=False,
        ),
        SimpleNamespace(
            agent_name="RLConfidenceAgent",
            score=1.0,
            weight=0.9,
            reasoning="RL BUY conv=1.0",
            vetoed=False,
        ),
        SimpleNamespace(
            agent_name="LSTMSignalAgent",
            score=0.83,
            weight=0.7,
            reasoning="LSTM BUY 0.83",
            vetoed=False,
        ),
    ]


def _arm_runner(monkeypatch, enabled=True, damp=0.8, severe=0.25):
    import config

    monkeypatch.setattr(
        config,
        "get_config",
        lambda: SimpleNamespace(
            DRAWDOWN_GUARD_CONDITIONER_ENABLED=enabled,
            DRAWDOWN_CONVICTION_DAMP_MAX=damp,
            DRAWDOWN_SEVERE_VETO_THRESHOLD=severe,
        ),
    )


def test_damped_conviction_below_buy_gate_flips_action_to_hold(monkeypatch):
    # R1 (#1951 forensics: 708 floor-BUYs) — the DAMPED conviction gates the action:
    # raw BUY consensus 0.70, DG dd 20% (score 0.0) → conviction 0.14 < 0.65 ⇒ HOLD.
    # No more 2%-MIN_POSITION_PERCENT floor trade on a cosmetically damped BUY.
    _arm_runner(monkeypatch)
    from core.round_table.runner import _score_to_signal

    sig = _score_to_signal(
        _state("MSFT", high=100, low=80, close=80), 0.70, _votes(dg_score=0.0)
    )
    assert sig.action == "HOLD"  # damped 0.14 is below the 0.65 buy gate
    assert sig.decision_context.conviction_score == pytest.approx(
        0.70 * 0.20, abs=1e-3
    )  # damped value stays in the audit trail


def test_high_conviction_survives_moderate_drawdown_as_buy(monkeypatch):
    # R2 — strong ML conviction over a moderate drawdown stays a (smaller) BUY:
    # raw 0.98, DG score 0.6 (severity 0.4) → conviction 0.98·(1−0.8·0.4)=0.666 ≥ 0.65.
    _arm_runner(monkeypatch)
    from core.round_table.runner import _score_to_signal

    sig = _score_to_signal(_state("X"), 0.98, _votes(dg_score=0.6))
    assert sig.action == "BUY"
    assert sig.decision_context.conviction_score == pytest.approx(
        0.98 * (1.0 - 0.8 * 0.4), abs=1e-3
    )


def test_gated_hold_audit_format_carries_raw_and_damped(monkeypatch):
    # R3 (§5.4 MiFID pre-trade transparency) — a gate-flipped HOLD must carry the
    # RAW consensus AND the damped conviction AND the HOLD verdict, in this format:
    # "consensus=0.700 (damped to 0.140 → HOLD: MSFT ~20% drawdown below buy gate)"
    import re

    _arm_runner(monkeypatch)
    from core.round_table.runner import _score_to_signal

    sig = _score_to_signal(
        _state("MSFT", high=100, low=80, close=80), 0.70, _votes(dg_score=0.0)
    )
    rs = sig.decision_context.reasoning_summary
    assert re.search(r"consensus=0\.700 \(damped to 0\.140 → HOLD", rs), rs
    assert "below buy gate" in rs


def test_mid_band_hold_untouched_by_gate(monkeypatch):
    # R4 — a raw HOLD (0.35 ≤ score ≤ 0.65) never enters the conditioner block.
    _arm_runner(monkeypatch)
    from core.round_table.runner import _score_to_signal

    sig = _score_to_signal(_state("X"), 0.50, _votes(dg_score=0.0))
    assert sig.action == "HOLD"
    assert sig.decision_context.conviction_score == pytest.approx(0.50, abs=1e-6)
    assert "damped" not in sig.decision_context.reasoning_summary


def test_audit_trail_preserves_raw_consensus(monkeypatch):
    # R6b (Archon R2) — the reasoning_summary must carry BOTH raw + damped.
    _arm_runner(monkeypatch)
    from core.round_table.runner import _score_to_signal

    sig = _score_to_signal(
        _state("MSFT", high=100, low=80, close=80), 1.0, _votes(dg_score=0.0)
    )
    rs = sig.decision_context.reasoning_summary
    assert "consensus=1.000" in rs
    assert "damped to 0.200" in rs


def test_damping_is_graduated(monkeypatch):
    _arm_runner(monkeypatch)
    from core.round_table.runner import _score_to_signal

    def conv(dg):
        return _score_to_signal(
            _state("X"), 0.70, _votes(dg_score=dg)
        ).decision_context.conviction_score

    # dg score for dd 0.07→0.65, 0.14→0.30, 0.20→0.0
    assert conv(0.65) > conv(0.30) > conv(0.0)


def test_sell_conviction_not_damped(monkeypatch):
    # R8 — only BUYs are damped; a risk-reducing SELL is untouched (direction-aware).
    _arm_runner(monkeypatch)
    from core.round_table.runner import _score_to_signal

    sig = _score_to_signal(_state("X"), 0.28, _votes(dg_score=0.0))  # < SELL_THRESHOLD
    assert sig.action == "SELL"
    assert sig.decision_context.conviction_score == pytest.approx(0.28, abs=1e-6)


def test_missing_dg_vote_fails_open_with_warning(monkeypatch, caplog):
    # R9 (Archon R3) — no DrawdownGuardAgent vote → no damping (fail-open) + WARNING.
    _arm_runner(monkeypatch)
    from core.round_table.runner import _score_to_signal

    votes = [v for v in _votes() if v.agent_name != "DrawdownGuardAgent"]
    with caplog.at_level(logging.WARNING):
        sig = _score_to_signal(_state("X"), 0.70, votes)
    assert sig.action == "BUY"  # R5 — fail-open also means: no gate applied
    assert sig.decision_context.conviction_score == pytest.approx(0.70, abs=1e-6)
    assert any(
        r.levelno == logging.WARNING and "DrawdownGuard" in r.message
        for r in caplog.records
    )


def test_flag_off_byte_identical(monkeypatch):
    # R10 — conditioner OFF: conviction == raw, reasoning has no "damped" clause.
    _arm_runner(monkeypatch, enabled=False)
    from core.round_table.runner import _score_to_signal

    sig = _score_to_signal(_state("X"), 0.70, _votes(dg_score=0.0))
    assert sig.decision_context.conviction_score == pytest.approx(0.70, abs=1e-6)
    assert "damped" not in sig.decision_context.reasoning_summary


# ---------------------------------------------------------------------------
# #1951 §5.2 — default posture: OFF in BOTH editions (audited hard-veto default;
# the shipped "True" default broke the plan's byte-identical-at-off guarantee).
# ---------------------------------------------------------------------------


def _hide_env_files():
    """Hide .env/.env.oss so os.getenv defaults are actually exercised."""
    hidden = []
    for base_dir in (".", ".."):
        for name in (".env.oss", ".env"):
            f = os.path.normpath(os.path.join(base_dir, name))
            if os.path.exists(f):
                try:
                    os.rename(f, f + ".tmp")
                    hidden.append(f)
                except Exception:
                    pass
    return hidden


def _unhide_env_files(hidden):
    for f in hidden:
        try:
            os.rename(f + ".tmp", f)
        except Exception:
            pass


def test_default_posture_is_off_enterprise():
    # R6 — env unset ⇒ DRAWDOWN_GUARD_CONDITIONER_ENABLED is False (config.py).
    import config as config_mod

    hidden = _hide_env_files()
    orig = os.environ.pop("DRAWDOWN_GUARD_CONDITIONER_ENABLED", None)
    try:
        importlib.reload(config_mod)
        shipped_default = config_mod.get_config().DRAWDOWN_GUARD_CONDITIONER_ENABLED
    finally:
        if orig is not None:
            os.environ["DRAWDOWN_GUARD_CONDITIONER_ENABLED"] = orig
        _unhide_env_files(hidden)
        importlib.reload(config_mod)  # restore the true runtime posture
    assert shipped_default is False


def test_default_posture_is_off_oss():
    # R6 (BORA parity) — same OFF default in config.oss.py.
    hidden = _hide_env_files()
    orig = os.environ.pop("DRAWDOWN_GUARD_CONDITIONER_ENABLED", None)
    try:
        spec = importlib.util.spec_from_file_location(
            "config_oss_1951", str(_ROOT / "config.oss.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod.DRAWDOWN_GUARD_CONDITIONER_ENABLED is False
        assert mod.get_config().DRAWDOWN_GUARD_CONDITIONER_ENABLED is False
    finally:
        if orig is not None:
            os.environ["DRAWDOWN_GUARD_CONDITIONER_ENABLED"] = orig
        _unhide_env_files(hidden)
