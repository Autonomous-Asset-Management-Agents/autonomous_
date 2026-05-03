# tests/unit/test_reconciliation.py
# Epic 2.3-Pre / PR-B — TDD Red-Phase
# ReconciliationService: Watch-Compare-Act
#
# Alle Tests sind ROT — core/reconciliation.py existiert noch nicht.
# Policy: docs/CODING_POLICY.md §11.5 TDD, §1 Compliance-First

import pytest  # noqa: F401 — used via @pytest.mark.anyio
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_alpaca_order(symbol, side="buy", status="new", order_id="ord-001"):
    o = MagicMock()
    o.id = order_id
    o.symbol = symbol
    o.side = side
    o.status = status
    return o


def _make_position(symbol, qty="10", avg_entry_price="150.0"):
    p = MagicMock()
    p.symbol = symbol
    p.qty = qty
    p.avg_entry_price = avg_entry_price
    return p


def _make_service(open_orders=None, positions=None, internal_orders=None):
    """Baut einen ReconciliationService mit gemocktem API und Redis."""
    from core.reconciliation import ReconciliationService

    api = MagicMock()
    api.get_orders = MagicMock(return_value=open_orders or [])
    api.get_all_positions = MagicMock(return_value=positions or [])
    api.cancel_order_by_id = MagicMock()

    redis = MagicMock()
    redis.get = MagicMock(return_value=None)

    service = ReconciliationService(api=api, redis_client=redis)
    service._internal_order_ids = set(internal_orders or [])
    return service, api, redis


# ---------------------------------------------------------------------------
# 1. Watch
# ---------------------------------------------------------------------------


class TestReconciliationWatch:
    @pytest.mark.anyio
    async def test_watch_fetches_broker_orders(self):
        """Watch ruft api.get_orders() auf und gibt die Order-Liste zurück."""
        orders = [_make_alpaca_order("AAPL")]
        service, api, _ = _make_service(open_orders=orders)

        result = await service._watch()

        api.get_orders.assert_called_once()
        assert len(result["orders"]) == 1
        assert result["orders"][0].symbol == "AAPL"

    @pytest.mark.anyio
    async def test_watch_fetches_broker_positions(self):
        """Watch ruft api.get_all_positions() auf."""
        positions = [_make_position("MSFT")]
        service, api, _ = _make_service(positions=positions)

        result = await service._watch()

        api.get_all_positions.assert_called_once()
        assert len(result["positions"]) == 1


# ---------------------------------------------------------------------------
# 2. Compare
# ---------------------------------------------------------------------------


class TestReconciliationCompare:
    def test_compare_detects_orphaned_order(self):
        """Compare erkennt Order die nicht in _internal_order_ids bekannt ist."""
        service, _, _ = _make_service(internal_orders={"ord-known"})
        orphan_order = _make_alpaca_order("AAPL", order_id="ord-orphan")

        broker_state = {"orders": [orphan_order], "positions": []}
        breaks = service._compare(broker_state)

        assert len(breaks) == 1
        assert breaks[0].order_id == "ord-orphan"
        assert breaks[0].break_type == "orphaned_order"

    def test_compare_noop_when_synced(self):
        """Compare gibt leere Liste zurück wenn Broker-State und intern übereinstimmen."""
        service, _, _ = _make_service(internal_orders={"ord-001"})
        known_order = _make_alpaca_order("AAPL", order_id="ord-001")

        broker_state = {"orders": [known_order], "positions": []}
        breaks = service._compare(broker_state)

        assert breaks == []


# ---------------------------------------------------------------------------
# 3. Act
# ---------------------------------------------------------------------------


class TestReconciliationAct:
    @pytest.mark.anyio
    async def test_act_cancels_orphaned_order(self):
        """Act ruft api.cancel_order_by_id() für jede orphaned_order Break auf."""
        from core.reconciliation import ReconciliationBreak

        service, api, _ = _make_service()
        breaks = [
            ReconciliationBreak(
                order_id="ord-orphan", symbol="AAPL", break_type="orphaned_order"
            )
        ]

        await service._act(breaks)

        api.cancel_order_by_id.assert_called_once_with("ord-orphan")

    @pytest.mark.anyio
    async def test_act_noop_when_no_breaks(self):
        """Act macht nichts wenn keine Breaks vorliegen."""
        service, api, _ = _make_service()

        await service._act([])

        api.cancel_order_by_id.assert_not_called()


# ---------------------------------------------------------------------------
# 4. Performance
# ---------------------------------------------------------------------------


class TestReconciliationPerformance:
    @pytest.mark.anyio
    async def test_single_reconciliation_cycle_under_30s(self):
        """Ein vollständiger Watch-Compare-Act-Cycle muss unter 30s bleiben."""
        import time

        service, _, _ = _make_service()

        start = time.perf_counter()
        broker_state = await service._watch()
        breaks = service._compare(broker_state)
        await service._act(breaks)
        elapsed = time.perf_counter() - start

        assert (
            elapsed < 30.0
        ), f"Reconciliation cycle took {elapsed:.1f}s — must be < 30s"
