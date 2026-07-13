"""TDD (ADR-OBS-01 / PR C): the ``decision`` subsystem — Round-Table decision HEALTH.

Closes the VC-2 gap: PR A/B only exposed cycle_watchdog *liveness* ("did the round
table complete"), never the actual decision ACTIVITY — the consensus verdict
distribution (BUY / SELL / NO-TRADE), how many round tables ran, and which agents'
``vote(state)`` raised.

The load-bearing invariant is the SAFETY test: every counter bump is PURE OBSERVATION
on the VC-2 decision path (BEFORE order execution). If a bump raises, the consensus
verdict, the agent votes, and the ``run_round_table`` flow MUST all be byte-identical —
a broken counter can never alter a decision.

MACHINE-ONLY: the counters hold aggregate counts, agent NAMES (code class identifiers),
and ages/timestamps — never symbols, order content, scores, or per-symbol verdicts.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

import core.engine.api_routes as api_routes_mod
from core.auth import require_engine_key
from core.engine.api_routes import app

# --------------------------------------------------------------------------- #
# 1. consensus outcome counter — {buy, sell, no_trade} at the verdict
# --------------------------------------------------------------------------- #


def test_consensus_outcome_counter_bumps_buy_sell_no_trade():
    """Each verdict classification bumps exactly its bucket (thresholds L27-28)."""
    from core.round_table import consensus as cons

    cons.reset_decision_counters()

    # score > 0.65 & approved → BUY territory
    cons.record_consensus_outcome(0.80, approved=True)
    # score < 0.35 & approved → SELL territory
    cons.record_consensus_outcome(0.20, approved=True)
    # HOLD zone (0.35..0.65) & approved → NO-TRADE
    cons.record_consensus_outcome(0.50, approved=True)
    # any score but NOT approved (gatekeeper veto) → NO-TRADE
    cons.record_consensus_outcome(0.90, approved=False)

    snap = cons.get_decision_counters()["consensus_outcomes"]
    assert snap["buy"] == 1
    assert snap["sell"] == 1
    assert snap["no_trade"] == 2


def test_consensus_outcome_counter_failure_never_changes_verdict(monkeypatch):
    """SAFETY: a poisoned outcome bump must not alter the ConsensusEngine score.

    The aggregate() verdict math is byte-identical whether or not the observation
    counter explodes.
    """
    from core.round_table import consensus as cons
    from core.round_table.base_agent import VoteResult

    cons.reset_decision_counters()

    def _boom(*_a, **_k):
        raise RuntimeError("counter exploded")

    monkeypatch.setattr(cons, "_bump_outcome", _boom)

    engine = cons.ConsensusEngine()
    votes = [
        VoteResult(agent_name="A", symbol="AAPL", score=0.8, weight=0.6, reasoning="x"),
        VoteResult(agent_name="B", symbol="AAPL", score=0.4, weight=0.4, reasoning="y"),
    ]
    # (0.8*0.6 + 0.4*0.4)/1.0 = 0.64 — must be unchanged by the broken counter.
    assert abs(engine.aggregate(votes) - 0.64) < 1e-6
    # And record_consensus_outcome itself must swallow the poisoned bump.
    cons.record_consensus_outcome(0.80, approved=True)  # must not raise


# --------------------------------------------------------------------------- #
# 2. runner — round_tables_run + last_consensus_ts + agent_vote_failures,
#    and the SAFETY invariant on the full run_round_table flow
# --------------------------------------------------------------------------- #


def _state():
    return {
        "symbol": "AAPL",
        "ohlc": {
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 1_000_000,
        },
        "current_time": "2026-04-01T10:00:00Z",
    }


def test_run_round_table_bumps_run_counter_and_stamps_ts(monkeypatch):
    from core.round_table import runner

    runner.reset_decision_counters()
    runner.boot_engine()  # OSS engine, 9 agents

    before = runner.get_decision_counters()["round_tables_run"]
    asyncio.run(runner.run_round_table(_state()))
    snap = runner.get_decision_counters()

    assert snap["round_tables_run"] == before + 1
    assert snap["last_consensus_ts"] is not None


def test_agent_vote_failure_counter_records_agent_name(monkeypatch):
    """When an agent's vote() raises, the failure counter bumps under its CLASS name."""
    from core.round_table import runner

    runner.reset_decision_counters()
    runner.boot_engine()

    # Force ONE agent to raise inside vote() — its class name must be recorded.
    target = runner._active_agents[0]
    target_name = target.__class__.__name__

    async def _raise(_state):
        raise RuntimeError("vote blew up")

    monkeypatch.setattr(target, "vote", _raise)

    asyncio.run(runner.run_round_table(_state()))
    failures = runner.get_decision_counters()["agent_vote_failures"]
    assert failures.get(target_name, 0) >= 1


def test_run_counter_failure_never_breaks_round_table(monkeypatch):
    """SAFETY: a poisoned run/ts/failure counter must not break run_round_table.

    The flow completes and returns its normal state dict even though every decision
    counter bump explodes.
    """
    from core.round_table import runner

    runner.reset_decision_counters()
    runner.boot_engine()

    def _boom(*_a, **_k):
        raise RuntimeError("counter exploded")

    monkeypatch.setattr(runner, "_bump_run", _boom)
    monkeypatch.setattr(runner, "_bump_agent_failure", _boom)

    # Must complete and return a state dict (no error injected by the counter).
    out = asyncio.run(runner.run_round_table(_state()))
    assert isinstance(out, dict)
    assert "error" not in out or out.get("error") is None


# --------------------------------------------------------------------------- #
# 3. /engine-diagnostics wiring — the new fail-soft ``decision`` subsystem
# --------------------------------------------------------------------------- #


@pytest.fixture
def client_authed():
    app.dependency_overrides[require_engine_key] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_decision_subsystem_present_and_shaped(client_authed):
    body = client_authed.get("/engine-diagnostics").json()

    assert "decision" in body
    dec = body["decision"]
    assert "consensus_outcomes" in dec
    for k in ("buy", "sell", "no_trade"):
        assert k in dec["consensus_outcomes"], f"consensus_outcomes missing {k}"
    assert "round_tables_run" in dec
    assert "last_consensus_age_seconds" in dec
    assert "agent_vote_failures" in dec


def test_decision_subsystem_is_fail_soft(client_authed, monkeypatch):
    def _boom():
        raise RuntimeError("kaboom")

    monkeypatch.setattr(api_routes_mod, "_collect_decision", _boom)

    r = client_authed.get("/engine-diagnostics")
    assert r.status_code == 200
    body = r.json()
    assert body["decision"] == {"_error": "RuntimeError"}
    # Siblings unaffected.
    assert "_error" not in body["compliance_decisions"]


def test_decision_subsystem_has_no_symbol_content(client_authed):
    """MACHINE-only: the decision surface never leaks a symbol or per-symbol verdict."""
    import json as _json

    from core.round_table import runner

    runner.reset_decision_counters()
    runner.boot_engine()
    asyncio.run(runner.run_round_table(_state()))

    dec = client_authed.get("/engine-diagnostics").json()["decision"]
    serialized = _json.dumps(dec)
    assert "AAPL" not in serialized
