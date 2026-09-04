"""#3095 (b) — UpsideSkewAgent vote/abstention via fake SymbolEvalState.

Flag gated through agents._upside_skew_enabled (monkeypatched — no config/env churn).
Vote is async → driven with asyncio.run (no async-plugin dependency).
"""

import asyncio

import pytest

from core.round_table import agents
from core.round_table.agents import UpsideSkewAgent


def _run(state):
    return asyncio.run(UpsideSkewAgent().vote(state))


def _state(**kw):
    base = {
        "symbol": "AAPL",
        "risk_reversal": 0.05,
        "risk_reversal_reference": [-0.03, 0.0, 0.02, 0.05],
        "risk_reversal_reference_date": "2026-08-27",
    }
    base.update(kw)
    return base


def test_flag_off_abstains(monkeypatch):
    monkeypatch.setattr(agents, "_upside_skew_enabled", lambda: False)
    v = _run(_state())
    assert v.weight == 0.0
    assert v.agent_name == "UpsideSkewAgent"


def test_flag_on_votes_with_weight(monkeypatch):
    monkeypatch.setattr(agents, "_upside_skew_enabled", lambda: True)
    v = _run(_state(risk_reversal=0.05))
    assert v.weight > 0.0
    # percentile of 0.05 in [-0.03,0,0.02,0.05] -> 3/4 = 0.75
    assert v.score == pytest.approx(0.75)


def test_higher_skew_scores_higher(monkeypatch):
    monkeypatch.setattr(agents, "_upside_skew_enabled", lambda: True)
    hi = _run(_state(risk_reversal=0.05)).score
    lo = _run(_state(risk_reversal=-0.03)).score
    assert hi > lo


def test_missing_rr_abstains(monkeypatch):
    monkeypatch.setattr(agents, "_upside_skew_enabled", lambda: True)
    assert _run(_state(risk_reversal=None)).weight == 0.0


def test_short_reference_abstains(monkeypatch):
    monkeypatch.setattr(agents, "_upside_skew_enabled", lambda: True)
    assert _run(_state(risk_reversal_reference=[0.01])).weight == 0.0


def test_non_finite_rr_abstains(monkeypatch):
    monkeypatch.setattr(agents, "_upside_skew_enabled", lambda: True)
    assert _run(_state(risk_reversal=float("nan"))).weight == 0.0
