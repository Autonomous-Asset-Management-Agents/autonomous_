"""TDD (ADR-OBS-01 / PR A.2): Tier-1 CRITICAL-path instrumentation.

Covers the three hot-path modules — order_executor (execution), compliance
(GO/NO-GO decision), senate_log (audit-write) — plus their wiring into the
always-200 ``/engine-diagnostics`` surface.

The load-bearing invariant is the SAFETY test in each block: the counter
increment is *pure observation*. If the counter itself raises, the underlying
submit / check_order / audit-write MUST still return its normal result and its
control flow MUST be unchanged — a broken counter can never block or alter a
trade, a compliance decision, or an audit write.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

import core.engine.api_routes as api_routes_mod
from core.auth import require_engine_key
from core.engine.api_routes import app

# --------------------------------------------------------------------------- #
# 1. order_executor — execution counters
# --------------------------------------------------------------------------- #


def _make_executor(client_mock):
    from core.engine.order_executor import OrderExecutorMixin

    ex = OrderExecutorMixin.__new__(OrderExecutorMixin)
    ex.api = client_mock
    ex.compliance_guardian = None
    ex.live_universe = []
    ex.cloud_logger = MagicMock()
    return ex


def _reset_exec_counters():
    from core.engine import order_executor as oe

    oe.reset_exec_counters()


def test_exec_counters_increment_on_submit_ok(monkeypatch):
    """A successful live submit bumps submit_ok and stamps last_fill_ts."""
    from core.cloud_logger import DecisionContext
    from core.engine import order_executor as oe
    from core.events import SignalEvent

    _reset_exec_counters()
    monkeypatch.setattr(oe.config, "SHADOW_MODE", False, raising=False)

    filled = MagicMock()
    filled.status = __import__(
        "alpaca.trading.enums", fromlist=["OrderStatus"]
    ).OrderStatus.FILLED
    client = MagicMock()
    submitted = MagicMock()
    submitted.id = "oid-1"
    client.submit_order.return_value = submitted
    client.get_order_by_id.return_value = filled
    client.get_account.return_value = MagicMock(cash="100000")

    ex = _make_executor(client)
    ctx = DecisionContext(symbol="AAPL", action="SELL", current_price=100.0)
    # SELL path so a fixed position qty is used (no risk sizing surprises).
    ex._get_tenant_portfolio_manager = MagicMock(
        return_value=MagicMock(can_sell_position=MagicMock(return_value=(True, "")))
    )
    ex._get_tenant_risk_manager = MagicMock()
    client.get_open_position.return_value = MagicMock(qty="5")
    event = SignalEvent(symbol="AAPL", action="SELL", decision_context=ctx)
    tenant = {"user_id": "u1", "client": client, "equity": 100000.0}

    asyncio.run(ex._execute_tenant_order(tenant, event))

    snap = oe.get_exec_counters()
    assert snap["submit_ok"] >= 1
    assert snap["last_fill_ts"] is not None
    assert snap["shadow_mode"] is False


def test_exec_counters_increment_on_submit_fail(monkeypatch):
    """A live submit that raises bumps submit_fail (and never submit_ok)."""
    from core.cloud_logger import DecisionContext
    from core.engine import order_executor as oe
    from core.events import SignalEvent

    _reset_exec_counters()
    monkeypatch.setattr(oe.config, "SHADOW_MODE", False, raising=False)

    client = MagicMock()
    client.submit_order.side_effect = RuntimeError("broker down")
    client.get_open_position.return_value = MagicMock(qty="5")

    ex = _make_executor(client)
    ex._get_tenant_portfolio_manager = MagicMock(
        return_value=MagicMock(can_sell_position=MagicMock(return_value=(True, "")))
    )
    ex._get_tenant_risk_manager = MagicMock()
    ctx = DecisionContext(symbol="AAPL", action="SELL", current_price=100.0)
    event = SignalEvent(symbol="AAPL", action="SELL", decision_context=ctx)
    tenant = {"user_id": "u1", "client": client, "equity": 100000.0}

    asyncio.run(ex._execute_tenant_order(tenant, event))

    snap = oe.get_exec_counters()
    assert snap["submit_fail"] >= 1
    assert snap["submit_ok"] == 0


def test_exec_counter_failure_never_breaks_submit(monkeypatch):
    """SAFETY: if the counter increment raises, the submit STILL succeeds and the
    method still returns its normal ``order_submitted`` result."""
    from core.cloud_logger import DecisionContext
    from core.engine import order_executor as oe
    from core.events import SignalEvent

    _reset_exec_counters()
    monkeypatch.setattr(oe.config, "SHADOW_MODE", False, raising=False)

    # Poison the raw counter bump — every increment now raises.
    def _boom(*_a, **_k):
        raise RuntimeError("counter exploded")

    monkeypatch.setattr(oe, "_bump_exec", _boom)

    filled = MagicMock()
    filled.status = __import__(
        "alpaca.trading.enums", fromlist=["OrderStatus"]
    ).OrderStatus.FILLED
    client = MagicMock()
    submitted = MagicMock()
    submitted.id = "oid-1"
    client.submit_order.return_value = submitted
    client.get_order_by_id.return_value = filled
    client.get_open_position.return_value = MagicMock(qty="5")

    ex = _make_executor(client)
    ex._get_tenant_portfolio_manager = MagicMock(
        return_value=MagicMock(
            can_sell_position=MagicMock(return_value=(True, "")),
            record_trade=MagicMock(),
            clear_sell_signals_after_sale=MagicMock(),
        )
    )
    ex._get_tenant_risk_manager = MagicMock()
    ctx = DecisionContext(symbol="AAPL", action="SELL", current_price=100.0)
    event = SignalEvent(symbol="AAPL", action="SELL", decision_context=ctx)
    tenant = {"user_id": "u1", "client": client, "equity": 100000.0}

    result = asyncio.run(ex._execute_tenant_order(tenant, event))

    # Control flow unchanged: broker was called and the normal result returned.
    client.submit_order.assert_called_once()
    assert result is True


# --------------------------------------------------------------------------- #
# 2. compliance — GO/NO-GO decision counters
# --------------------------------------------------------------------------- #


def _valid_order(**over):
    o = {
        "symbol": "AAPL",
        "side": "buy",
        "quantity": 1,
        "price": 100.0,
        "strategy_id": "s1",
        "timestamp": 1.0,
        "user_id": "u1",
    }
    o.update(over)
    return o


def test_compliance_counters_go_and_nogo():
    from core import compliance as comp

    g = comp.ComplianceGuardian()
    comp.reset_compliance_counters()

    assert g.check_order(_valid_order()) is True  # GO
    # NO-GO: restricted symbol → machine reason "restricted_symbol".
    assert g.check_order(_valid_order(symbol="SCAM_TOKEN")) is False

    snap = comp.get_compliance_counters()
    assert snap["go_count"] >= 1
    assert snap["nogo_count"] >= 1
    # reject reasons are MACHINE codes, never symbol-specific content.
    assert "restricted_symbol" in snap["reject_reasons"]
    assert "SCAM_TOKEN" not in str(snap["reject_reasons"])


def test_compliance_counter_failure_never_breaks_decision(monkeypatch):
    """SAFETY: a raising counter must not change the GO/NO-GO verdict."""
    from core import compliance as comp

    comp.reset_compliance_counters()

    def _boom(*_a, **_k):
        raise RuntimeError("counter exploded")

    monkeypatch.setattr(comp, "_bump_compliance", _boom)

    g = comp.ComplianceGuardian()
    # The decision itself is byte-identical despite the poisoned counter.
    assert g.check_order(_valid_order()) is True
    assert g.check_order(_valid_order(symbol="SCAM_TOKEN")) is False


# --------------------------------------------------------------------------- #
# 3. senate_log — audit-write counters
# --------------------------------------------------------------------------- #


def test_senate_write_counters_ok(tmp_path, monkeypatch):
    from core.round_table import senate_log as sl

    monkeypatch.setenv("SENATE_LOG_DIR", str(tmp_path))
    sl.reset_audit_counters()

    logger = sl.LocalJSONAuditLogger()
    asyncio.run(logger._write_to_hash_chain({"event_type": "unit", "x": 1}))

    snap = sl.get_audit_counters()
    assert snap["write_ok"] >= 1
    assert snap["write_fail"] == 0


def test_senate_write_counters_fail(tmp_path, monkeypatch):
    from core.round_table import senate_log as sl

    monkeypatch.setenv("SENATE_LOG_DIR", str(tmp_path))
    sl.reset_audit_counters()

    logger = sl.LocalJSONAuditLogger()

    # Force the write to fail (disk_usage raises) → write_fail.
    async def _raise(*_a, **_k):
        raise OSError("disk gone")

    monkeypatch.setattr(sl.asyncio, "to_thread", _raise)
    asyncio.run(logger._write_to_hash_chain({"event_type": "unit", "x": 1}))

    snap = sl.get_audit_counters()
    assert snap["write_fail"] >= 1


def test_senate_counter_failure_never_breaks_write(tmp_path, monkeypatch):
    """SAFETY: a raising counter must not propagate out of the audit write."""
    from core.round_table import senate_log as sl

    monkeypatch.setenv("SENATE_LOG_DIR", str(tmp_path))
    sl.reset_audit_counters()

    def _boom(*_a, **_k):
        raise RuntimeError("counter exploded")

    monkeypatch.setattr(sl, "_bump_audit", _boom)

    logger = sl.LocalJSONAuditLogger()
    # Must complete without raising even though every counter bump explodes.
    asyncio.run(logger._write_to_hash_chain({"event_type": "unit", "x": 1}))


# --------------------------------------------------------------------------- #
# 4. /engine-diagnostics wiring — the 3 new fail-soft subsystems
# --------------------------------------------------------------------------- #


@pytest.fixture
def client_authed():
    app.dependency_overrides[require_engine_key] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_new_subsystems_present_and_shaped(client_authed):
    body = client_authed.get("/engine-diagnostics").json()

    assert "execution" in body
    for k in (
        "submit_ok",
        "submit_fail",
        "retry_count",
        "last_fill_age_seconds",
        "shadow_mode",
    ):
        assert k in body["execution"], f"execution missing {k}"

    assert "compliance_decisions" in body
    for k in ("go_count", "nogo_count", "top_reject_reasons"):
        assert k in body["compliance_decisions"], f"compliance_decisions missing {k}"

    assert "audit_write" in body
    for k in ("senate_write_ok", "senate_write_fail"):
        assert k in body["audit_write"], f"audit_write missing {k}"


def test_new_subsystems_are_fail_soft(client_authed, monkeypatch):
    def _boom():
        raise RuntimeError("kaboom")

    monkeypatch.setattr(api_routes_mod, "_collect_execution", _boom)

    r = client_authed.get("/engine-diagnostics")
    assert r.status_code == 200
    body = r.json()
    assert body["execution"] == {"_error": "RuntimeError"}
    # Siblings unaffected.
    assert "_error" not in body["compliance_decisions"]
    assert "_error" not in body["audit_write"]


def test_reject_reasons_are_machine_codes_only(client_authed):
    """The diagnostics surface must expose machine reason codes, never order content."""
    from core import compliance as comp

    comp.reset_compliance_counters()
    g = comp.ComplianceGuardian()
    g.check_order(_valid_order(symbol="EVIL_CORP"))  # NO-GO restricted

    body = client_authed.get("/engine-diagnostics").json()
    top = body["compliance_decisions"]["top_reject_reasons"]
    import json as _json

    assert "EVIL_CORP" not in _json.dumps(top)
