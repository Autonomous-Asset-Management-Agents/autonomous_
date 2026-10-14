# tests/unit/test_hitl_drain_wiring.py
# ii-5b (PR-0a-ii, GAP2): the trading-loop HITL wiring (EU AI Act Art. 14).
#
# Three small helpers on TradingLoopMixin, each self-gated on HITL_ENABLED (dormant by default)
# and called from live_trading_loop:
#   _drain_hitl_approvals()  — every cycle, execute each human-approved order (C3 / N1).
#   _hitl_day_rollover(prev) — on an NY-date change, clear YESTERDAY's day-notional key (N3).
#   _hitl_symbol_pending(s)  — skip a symbol already awaiting approval, before its analysis (C4).
from __future__ import annotations

import asyncio
import datetime as dt
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

_AI_BOT = Path(__file__).resolve().parents[2]  # ai_trading_bot/
if str(_AI_BOT) not in sys.path:
    sys.path.insert(0, str(_AI_BOT))


def _run(coro):
    return asyncio.run(coro)


def _engine():
    from core.engine.trading_loop import TradingLoopMixin

    return TradingLoopMixin.__new__(TradingLoopMixin)


def _cfg(enabled=True):
    return SimpleNamespace(HITL_ENABLED=enabled)


def _gc(enabled):
    return patch("core.engine.trading_loop.get_config", return_value=_cfg(enabled))


# ── _drain_hitl_approvals ────────────────────────────────────────────────────────


def _patch_queue(*, claimed):
    """Patch the three HITL-queue drain entry points; return the mocks."""
    recover = AsyncMock(return_value=[])
    claim = AsyncMock(return_value=claimed)
    ack = AsyncMock(return_value=True)
    return (
        patch("core.hitl_queue.HitlQueue.recover_orphaned_inflight", recover),
        patch("core.hitl_queue.HitlQueue.claim_approved", claim),
        patch("core.hitl_queue.HitlQueue.ack_inflight", ack),
        recover,
        claim,
        ack,
    )


def test_drain_noop_when_hitl_disabled():
    eng = _engine()
    eng.execute_approved_order = AsyncMock()
    p_rec, p_claim, p_ack, recover, claim, ack = _patch_queue(claimed=[])
    with _gc(False), p_rec, p_claim, p_ack:
        assert _run(eng._drain_hitl_approvals()) == 0
    claim.assert_not_awaited()  # never even touches the queue when dormant
    recover.assert_not_awaited()
    eng.execute_approved_order.assert_not_awaited()


def test_drain_noop_when_queue_empty():
    eng = _engine()
    eng.execute_approved_order = AsyncMock()
    p_rec, p_claim, p_ack, recover, claim, ack = _patch_queue(claimed=[])
    with _gc(True), p_rec, p_claim, p_ack:
        assert _run(eng._drain_hitl_approvals()) == 0
    recover.assert_awaited_once()  # crash-recovery sweep runs every cycle
    eng.execute_approved_order.assert_not_awaited()


def test_drain_executes_and_acks_each_approved_payload():
    eng = _engine()
    eng.execute_approved_order = AsyncMock(return_value=True)
    payloads = [
        {"symbol": "AAPL", "approval_id": "a1"},
        {"symbol": "MSFT", "approval_id": "a2"},
    ]
    p_rec, p_claim, p_ack, recover, claim, ack = _patch_queue(claimed=payloads)
    with _gc(True), p_rec, p_claim, p_ack:
        assert _run(eng._drain_hitl_approvals()) == 2
    assert eng.execute_approved_order.await_count == 2
    # each claimed order is acked (inflight marker cleared) after a definitive outcome
    assert ack.await_count == 2
    ack.assert_any_await("a1")


def test_drain_acks_even_when_a_payload_fails():
    # one bad payload must not crash the loop, must not block the rest, and must STILL be acked
    # (its inflight marker cleared) — a handled error is a definitive outcome, not a crash.
    eng = _engine()
    eng.execute_approved_order = AsyncMock(side_effect=[Exception("boom"), True])
    payloads = [
        {"symbol": "BAD", "approval_id": "b1"},
        {"symbol": "MSFT", "approval_id": "m1"},
    ]
    p_rec, p_claim, p_ack, recover, claim, ack = _patch_queue(claimed=payloads)
    with _gc(True), p_rec, p_claim, p_ack:
        assert _run(eng._drain_hitl_approvals()) == 1  # one succeeded
    assert eng.execute_approved_order.await_count == 2  # both attempted
    assert ack.await_count == 2  # both acked (finally), incl. the failed one


def test_drain_recovers_orphans_before_claiming():
    # The crash-recovery sweep must run BEFORE claim each cycle, so it only ever sees
    # prior-cycle orphans, never this cycle's freshly-claimed inflight markers.
    eng = _engine()
    eng.execute_approved_order = AsyncMock(return_value=True)
    order = []
    recover = AsyncMock(side_effect=lambda *a, **k: order.append("recover") or [])
    claim = AsyncMock(side_effect=lambda *a, **k: order.append("claim") or [])
    with _gc(True), patch(
        "core.hitl_queue.HitlQueue.recover_orphaned_inflight", recover
    ), patch("core.hitl_queue.HitlQueue.claim_approved", claim), patch(
        "core.hitl_queue.HitlQueue.ack_inflight", AsyncMock(return_value=True)
    ):
        _run(eng._drain_hitl_approvals())
    assert order == ["recover", "claim"]


# ── _hitl_day_rollover ───────────────────────────────────────────────────────────


def test_rollover_noop_on_first_boot_previous_none():
    eng = _engine()
    with _gc(True), patch(
        "core.hitl_day_notional.HitlDayNotional.rollover", AsyncMock()
    ) as roll:
        _run(eng._hitl_day_rollover(None))
    roll.assert_not_awaited()  # N3: never roll on first boot


def test_rollover_noop_when_hitl_disabled():
    eng = _engine()
    with _gc(False), patch(
        "core.hitl_day_notional.HitlDayNotional.rollover", AsyncMock()
    ) as roll:
        _run(eng._hitl_day_rollover(dt.date(2026, 6, 14)))
    roll.assert_not_awaited()


def test_rollover_clears_previous_day_iso():
    eng = _engine()
    with _gc(True), patch(
        "core.hitl_day_notional.HitlDayNotional.rollover", AsyncMock()
    ) as roll:
        _run(eng._hitl_day_rollover(dt.date(2026, 6, 14)))
    roll.assert_awaited_once_with("2026-06-14")  # deletes YESTERDAY's key


# ── _hitl_symbol_pending ─────────────────────────────────────────────────────────


def test_symbol_pending_noop_when_disabled():
    eng = _engine()
    eng.active_uid = None
    with _gc(False), patch(
        "core.hitl_queue.HitlQueue.has_pending", AsyncMock(return_value=True)
    ) as hp:
        assert _run(eng._hitl_symbol_pending("AAPL")) is False
    hp.assert_not_awaited()


def test_symbol_pending_true_resolves_global_user():
    eng = _engine()
    eng.active_uid = None
    with _gc(True), patch(
        "core.hitl_queue.HitlQueue.has_pending", AsyncMock(return_value=True)
    ) as hp:
        assert _run(eng._hitl_symbol_pending("AAPL")) is True
    hp.assert_awaited_once_with("AAPL", "global")


def test_symbol_pending_false_resolves_active_uid():
    eng = _engine()
    eng.active_uid = "u1"
    with _gc(True), patch(
        "core.hitl_queue.HitlQueue.has_pending", AsyncMock(return_value=False)
    ) as hp:
        assert _run(eng._hitl_symbol_pending("AAPL")) is False
    hp.assert_awaited_once_with("AAPL", "u1")
