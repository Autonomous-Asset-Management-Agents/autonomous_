# tests/unit/test_runner_agent_veto_direction_aware.py
# TDD (#2031 Change 2): the agent-veto (e.g. DrawdownGuardAgent) must be
# DIRECTION-AWARE — it must NOT block a risk-reducing SELL. Blocking the exit of a
# drawdown name is the exact harm the guard is meant to prevent. `_apply_agent_veto`
# overrides the gatekeeper decision only when the consensus is NOT a SELL.

from types import SimpleNamespace

from core.round_table.consensus import SIGNAL_SELL_THRESHOLD
from core.round_table.gatekeeper import GatekeeperDecision
from core.round_table.runner import _apply_agent_veto


def _vote(vetoed=False, name="DrawdownGuardAgent", reasoning="drawdown"):
    return SimpleNamespace(vetoed=vetoed, agent_name=name, reasoning=reasoning)


def _approved():
    return GatekeeperDecision(approved=True, reason="AllChecksPassed", symbol="X")


def test_sell_not_blocked_by_agent_veto():
    """SELL-band consensus + a vetoing agent → the veto does NOT block it."""
    out = _apply_agent_veto(_approved(), [_vote(vetoed=True), _vote()], 0.28, "HOOD")
    assert out.approved is True


def test_buy_blocked_by_agent_veto():
    """BUY-band consensus + a vetoing agent → blocked (unchanged behaviour)."""
    out = _apply_agent_veto(_approved(), [_vote(vetoed=True)], 0.85, "AMZN")
    assert out.approved is False
    assert "Agent VETO" in out.reason and "DrawdownGuardAgent" in out.reason


def test_hold_blocked_by_agent_veto():
    """HOLD-band consensus + a vetoing agent → blocked (no order results anyway)."""
    out = _apply_agent_veto(_approved(), [_vote(vetoed=True)], 0.5, "MSFT")
    assert out.approved is False


def test_boundary_at_sell_threshold_is_blocked():
    """Exactly at the SELL threshold is NOT a SELL (score < threshold) → blocked."""
    out = _apply_agent_veto(
        _approved(), [_vote(vetoed=True)], SIGNAL_SELL_THRESHOLD, "X"
    )
    assert out.approved is False


def test_no_veto_passes_decision_through_unchanged():
    """No vetoing agent → the original gatekeeper decision is returned untouched."""
    base = _approved()
    out = _apply_agent_veto(base, [_vote(), _vote()], 0.28, "HOOD")
    assert out is base
