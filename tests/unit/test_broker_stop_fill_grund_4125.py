"""#4125 Teil 4 (Plan §2 Punkt 4, §13d L1/L2): Verkaufs-Fills ohne Engine-Entscheidung bekommen einen Grund.

- Fill mit eigenem Stop-Schluessel (``stop-<versuch>-stop-…``) → ``sell_reason`` ``broker_stop`` mit
  Stop-Kurs und Fill-Kurs.
- Fill mit einem Schluessel, der nicht dem Engine-Muster folgt → ``manual`` mit Fill-Kurs und Order-ID.
- Fills von Engine-Orders legen keinen zweiten Grund ab; den hat das Tor geschrieben (Teil 3a).
- Strom und periodischer Lauf gehen denselben Weg (``on_broker_fill``).
- L1: Der erste Lauf nach dem Start legt eigene Stop-Fills und fremde Fills aus dem Fenster ab;
  der Schluessel ``sell_reason:<order_id>`` in der synchronen Ablage verhindert ein Doppel ueber
  einen Neustart. Ohne Ablage: ablegen mit WARNING.
"""

import asyncio
import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import core.reconciliation as reconciliation
from core.ports import audit_ablage_port
from core.ports.audit_ablage_port import AuditAblagePort
from core.reconciliation import ReconciliationService

pytestmark = pytest.mark.vc3

_FILL_ZEIT = datetime(2026, 9, 28, 14, 2, tzinfo=timezone.utc)


class _Port(AuditAblagePort):
    def __init__(self, wirft=False):
        self.abgelegt = []
        self._wirft = wirft

    def lege_ab(self, art, daten):
        if self._wirft:
            raise RuntimeError("Ablage kaputt")
        self.abgelegt.append((art, dict(daten)))

    def gruende(self):
        return [d for a, d in self.abgelegt if a == "sell_reason"]


class _SyncAblage:
    """Die Form des synchronen Redis-Clients (``core/state/synchron.py``)."""

    def __init__(self):
        self.werte = {}
        self.fristen = {}

    def get(self, key):
        return self.werte.get(key)

    def set(self, key, value, ex=None, **_):
        self.werte[key] = str(value)
        self.fristen[key] = ex
        return True


@pytest.fixture
def port():
    p = _Port()
    audit_ablage_port.setze_ablage(p)
    yield p
    audit_ablage_port.setze_ablage(None)


@pytest.fixture
def sync_ablage(monkeypatch):
    ablage = _SyncAblage()
    monkeypatch.setattr(reconciliation, "synchrone_ablage", lambda: ablage)
    return ablage


def _order(coid, *, oid="o-1", side="sell", status="filled", **mehr):
    werte = {
        "id": oid,
        "client_order_id": coid,
        "symbol": "FFIV",
        "side": side,
        "status": status,
        "filled_qty": "3",
        "filled_avg_price": "432.31",
        "filled_at": _FILL_ZEIT,
        "stop_price": "432.88",
    }
    werte.update(mehr)
    return SimpleNamespace(**werte)


def _dienst():
    return ReconciliationService(MagicMock(), None)


def test_eigener_stop_ergibt_broker_stop_mit_stop_und_fill_kurs(port, sync_ablage):
    _dienst().on_broker_fill(_order("stop-1-stop-FFIV"))

    (grund,) = port.gruende()
    assert grund["klasse"] == "broker_stop"
    assert grund["stop_kurs"] == pytest.approx(432.88)
    assert grund["fill_kurs"] == pytest.approx(432.31)
    assert grund["symbol"] == "FFIV"
    assert grund["qty"] == pytest.approx(3.0)
    assert grund["broker_order_id"] == "o-1"
    assert grund["zeitpunkt"] == _FILL_ZEIT.isoformat()


def test_fremder_schluessel_ergibt_manual_mit_fill_kurs_und_order_id(port, sync_ablage):
    _dienst().on_broker_fill(
        _order("3f2a9c1e-1234-4b6f-9a51-7d6e2c0b8a11", oid="o-ui", stop_price=None)
    )

    (grund,) = port.gruende()
    assert grund["klasse"] == "manual"
    assert grund["fill_kurs"] == pytest.approx(432.31)
    assert grund["order_id"] == "o-ui"
    assert "stop_kurs" not in grund


@pytest.mark.parametrize(
    "coid",
    [
        "trim-0-dec-42",  # Executor, woertliche Form
        "stop-1-_" + "a" * 32,  # Digest-Form (lange decision_id)
        "retry-1-_" + "b" * 32,  # Folgeversuch einer Alt-Form
        "hitl-7c1f0e",  # HITL-Freigabe
        "displacement-0-ersatz-displacement-global-ABC",  # Ersatz-Entscheidung
    ],
)
def test_engine_order_legt_keinen_zweiten_grund_ab(port, sync_ablage, coid):
    assert _dienst().on_broker_fill(_order(coid)) is not None
    assert port.gruende() == []


def test_kauf_legt_keinen_grund_ab(port, sync_ablage):
    _dienst().on_broker_fill(_order("3f2a9c1e-1234-4b6f", side="buy"))
    assert port.gruende() == []


def test_alpaca_enum_seite_wird_erkannt(port, sync_ablage):
    _dienst().on_broker_fill(_order("stop-0-stop-FFIV", side="OrderSide.SELL"))
    assert [g["klasse"] for g in port.gruende()] == ["broker_stop"]


def test_teilfills_derselben_order_ergeben_einen_grund(port, sync_ablage):
    dienst = _dienst()
    dienst.on_broker_fill(_order("stop-1-stop-FFIV", filled_qty="1"))
    dienst.on_broker_fill(_order("stop-1-stop-FFIV", filled_qty="3"))
    assert len(port.gruende()) == 1
    assert sync_ablage.werte.keys() == {"sell_reason:o-1"}
    assert sync_ablage.fristen["sell_reason:o-1"] == 7 * 24 * 3600


def test_periodischer_lauf_legt_den_grund_ueber_denselben_weg_ab(port, sync_ablage):
    dienst = _dienst()
    dienst._compare({"orders": [], "abgeschlossene_orders": [], "positions": []})
    dienst._compare(
        {
            "orders": [],
            "abgeschlossene_orders": [_order("stop-1-stop-FFIV")],
            "positions": [],
        }
    )
    assert [g["klasse"] for g in port.gruende()] == ["broker_stop"]


def test_erster_lauf_legt_stop_und_fremde_fills_aus_dem_fenster_ab(port, sync_ablage):
    """L1: Ein Stop, der waehrend einer Engine-Pause ausgefuehrt wurde, bekommt seinen Grund."""
    dienst = _dienst()
    dienst._compare(
        {
            "orders": [],
            "abgeschlossene_orders": [
                _order("stop-1-stop-FFIV", oid="o-stop"),
                _order("3f2a9c1e-1234-4b6f", oid="o-ui", stop_price=None),
                _order("trim-0-dec-42", oid="o-engine"),
                _order("stop-1-stop-PLTR", oid="o-offen", status="canceled"),
            ],
            "positions": [],
        }
    )
    assert sorted(g["klasse"] for g in port.gruende()) == ["broker_stop", "manual"]
    # Der Bestand bleibt Bestand: kein FillEvent, kein Befund im Folgelauf.
    assert dienst.fills == []
    assert (
        dienst._compare({"orders": [], "abgeschlossene_orders": [], "positions": []})
        == []
    )


def test_kein_doppel_ueber_einen_neustart(port, sync_ablage):
    stand = {
        "orders": [],
        "abgeschlossene_orders": [_order("stop-1-stop-FFIV", oid="o-stop")],
        "positions": [],
    }
    _dienst()._compare(stand)
    _dienst()._compare(stand)  # Neustart: neuer Dienst, dieselbe synchrone Ablage
    assert len(port.gruende()) == 1


def test_ohne_synchrone_ablage_wird_mit_warning_abgelegt(port, monkeypatch, caplog):
    monkeypatch.setattr(reconciliation, "synchrone_ablage", lambda: None)
    caplog.set_level(logging.WARNING, logger="core.reconciliation")
    _dienst().on_broker_fill(_order("stop-1-stop-FFIV"))

    assert [g["klasse"] for g in port.gruende()] == ["broker_stop"]
    assert any("sell_reason" in r.getMessage() for r in caplog.records)


def test_kaputte_ablage_stoert_den_abgleich_nicht(monkeypatch, sync_ablage, caplog):
    audit_ablage_port.setze_ablage(_Port(wirft=True))
    try:
        caplog.set_level(logging.WARNING, logger="core.reconciliation")
        ereignis = _dienst().on_broker_fill(_order("stop-1-stop-FFIV"))
    finally:
        audit_ablage_port.setze_ablage(None)
    assert ereignis is not None
    assert any("Ablage" in r.getMessage() for r in caplog.records)


def test_ohne_angemeldete_ablage_kein_fehler(sync_ablage):
    audit_ablage_port.setze_ablage(None)
    assert _dienst().on_broker_fill(_order("stop-1-stop-FFIV")) is not None


def test_strom_meldet_ueber_denselben_weg(port, sync_ablage):
    dienst = _dienst()
    gesehen = {}

    class _Strom:
        def subscribe_trade_updates(self, handler):
            gesehen["handler"] = handler

        async def _run_forever(self):
            return None

    async def lauf():
        dienst.start_fill_stream(_Strom)
        await gesehen["handler"](SimpleNamespace(order=_order("stop-1-stop-FFIV")))
        await dienst.stop_fill_stream()

    asyncio.run(lauf())
    assert [g["klasse"] for g in port.gruende()] == ["broker_stop"]
