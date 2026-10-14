# tests/unit/test_reconciliation.py
# Epic 2.3-Pre / PR-B — TDD Red-Phase
# ReconciliationService: Watch-Compare-Act
#
# Alle Tests sind ROT — core/reconciliation.py existiert noch nicht.
# Policy: docs/CODING_POLICY.md §11.5 TDD, §1 Compliance-First

from unittest.mock import MagicMock

import allure
import pytest  # noqa: F401 — used via @pytest.mark.anyio

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


@pytest.mark.vc2
@allure.story("Portfolio & Strategy")
class TestReconciliationWatch:
    @pytest.mark.anyio
    async def test_watch_fetches_broker_orders(self):
        """Watch ruft api.get_orders() auf und gibt die Order-Liste zurück.

        #3588: Zweimal — ohne Filter die OFFENEN Orders (der Vergleich), mit Filter die
        zuletzt ABGESCHLOSSENEN (die Fill-Seite). Ohne den zweiten Abruf sieht der Lauf
        eine gefuellte Order nirgends, denn Alpacas Default ist "open".
        """
        orders = [_make_alpaca_order("AAPL")]
        service, api, _ = _make_service(open_orders=orders)

        result = await service._watch()

        assert api.get_orders.call_count == 2
        assert api.get_orders.call_args_list[0].args in ((), (None,))
        assert api.get_orders.call_args_list[1].args, "zweiter Abruf ohne Filter"
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


@pytest.mark.vc2
@allure.story("Portfolio & Strategy")
class TestReconciliationCompare:
    def test_compare_detects_orphaned_order(self):
        """Compare erkennt Order die nicht in _internal_order_ids bekannt ist.

        #3389: Der ERSTE Lauf uebernimmt den vorgefundenen Bestand, statt ihn zu melden
        (sonst waere nach jedem Neustart jede offene Order eine Abweichung). Der Befund
        entsteht deshalb erst danach. Das Feld heisst jetzt `kind` — der Datensatz ist
        der Vertrag aus #3386.
        """
        service, _, _ = _make_service(internal_orders={"ord-known"})
        service._adopted = True  # Uebernahme-Lauf uebersprungen
        orphan_order = _make_alpaca_order("AAPL", order_id="ord-orphan")

        broker_state = {"orders": [orphan_order], "positions": []}
        breaks = service._compare(broker_state)

        assert len(breaks) == 1
        assert breaks[0].order_id == "ord-orphan"
        assert breaks[0].kind == "orphaned_order"

    def test_compare_noop_when_synced(self):
        """Compare gibt leere Liste zurück wenn Broker-State und intern übereinstimmen."""
        service, _, _ = _make_service(internal_orders={"ord-001"})
        service._adopted = True
        known_order = _make_alpaca_order("AAPL", order_id="ord-001")

        broker_state = {"orders": [known_order], "positions": []}
        breaks = service._compare(broker_state)

        assert breaks == []


# ---------------------------------------------------------------------------
# 3. Act
# ---------------------------------------------------------------------------


@pytest.mark.vc2
@allure.story("Portfolio & Strategy")
class TestReconciliationAct:
    @pytest.mark.anyio
    async def test_act_never_cancels_anything(self):
        """#3389: Der Abgleich storniert NICHTS mehr.

        Frueher hielt dieser Test fest, dass `_act` jede verwaiste Order storniert. Das
        war der Rueckschritt, den #3389 aufloest: eine Abweichung heisst gerade, dass
        nicht feststeht, welche Seite recht hat. Ein automatischer Storno auf Basis eines
        fehlerhaften Vergleichs ist irreversibel, ein Alarm nicht — und nach EU AI Act
        Art. 14 ist eine Bestandsaenderung eine Kapitalentscheidung fuer den Menschen.

        Verschaerfend: `_internal_order_ids` ist nach jedem Neustart leer. Haette der
        Dienst je in Produktion gelaufen, haette der erste Lauf nach einem Neustart den
        gesamten offenen Orderbestand storniert.
        """
        service, api, _ = _make_service()
        service._adopted = True
        orphan_order = _make_alpaca_order("AAPL", order_id="ord-orphan")

        breaks = service._compare({"orders": [orphan_order], "positions": []})

        assert breaks and breaks[0].kind == "orphaned_order"
        api.cancel_order_by_id.assert_not_called()
        assert not hasattr(service, "_act"), "_act ist mit #3389 entfallen"


# ---------------------------------------------------------------------------
# 4. Performance
# ---------------------------------------------------------------------------


@pytest.mark.vc2
@allure.story("Portfolio & Strategy")
class TestReconciliationPerformance:
    @pytest.mark.anyio
    async def test_single_reconciliation_cycle_under_30s(self):
        """Ein vollständiger Abgleichlauf muss unter 30s bleiben.

        #3389: aus Watch-Compare-Act wurde Watch-Compare-Record — `_act` ist entfallen.
        """
        import time

        service, _, _ = _make_service()

        start = time.perf_counter()
        await service.run_once()
        elapsed = time.perf_counter() - start

        assert (
            elapsed < 30.0
        ), f"Reconciliation cycle took {elapsed:.1f}s — must be < 30s"
