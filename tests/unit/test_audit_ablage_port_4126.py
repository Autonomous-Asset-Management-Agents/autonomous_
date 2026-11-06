"""#4126 Teil 1b (Plan §9c, 9g Punkt 4): Das Tor kennt die Audit-Ablage nur ueber einen Port.

Die Composition Root meldet die Ablage im Port-Modul an (``audit_ablage_port.setze_ablage``,
Plan §11); das Tor holt sie dort mit ``hole_ablage``. Ohne Anmeldung
bleibt die Logzeile, dazu kommt genau eine WARNING und der Zaehler ``nicht_verdrahtet``
steigt. Ein Port, der wirft, darf die Tor-Entscheidung nie stoeren.
"""

import configparser
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.contracts import ComplianceDecision, OrderIntent, ReasonCode
from core.gateway import fabrik
from core.ports import audit_ablage_port
from core.ports.audit_ablage_port import AuditAblagePort

pytestmark = pytest.mark.vc3


class _Port(AuditAblagePort):
    def __init__(self):
        self.abgelegt = []

    def lege_ab(self, art, daten):
        self.abgelegt.append((art, dict(daten)))


class _KaputterPort(AuditAblagePort):
    def lege_ab(self, art, daten):
        raise RuntimeError("Ablage kaputt")


@pytest.fixture(autouse=True)
def _ohne_anmeldung():
    audit_ablage_port.setze_ablage(None)
    fabrik.reset_ablage_zaehler()
    yield
    audit_ablage_port.setze_ablage(None)
    fabrik.reset_ablage_zaehler()


def _entscheidung(**ueber):
    basis = {
        "decision_id": "dec-1",
        "approved": False,
        "reason_code": ReasonCode.TRADING_HALTED,
        "detail": "Handel gehalten",
        "halted": True,
    }
    basis.update(ueber)
    return ComplianceDecision(**basis)


def _intent(**ueber):
    basis = {
        "decision_id": "dec-1",
        "symbol": "AAPL",
        "side": "buy",
        "qty": 2.0,
        "intent_kind": "entry",
        "halted": True,
    }
    basis.update(ueber)
    return OrderIntent(**basis)


def test_angemeldeter_port_bekommt_die_entscheidung_mit_intent():
    port = _Port()
    audit_ablage_port.setze_ablage(port)

    fabrik.record_gateway_decision(_entscheidung(), intent=_intent())

    art, daten = port.abgelegt[-1]
    assert art == "gateway_decision"
    assert daten["decision_id"] == "dec-1"
    assert daten["approved"] is False
    assert daten["reason_code"] == "trading_halted"
    assert daten["halted"] is True
    assert daten["symbol"] == "AAPL"
    assert daten["side"] == "buy"
    assert daten["qty"] == 2.0
    assert daten["intent_kind"] == "entry"
    assert fabrik.get_ablage_zaehler()["nicht_verdrahtet"] == 0


def test_storno_legt_symbol_und_order_id_ab():
    port = _Port()
    audit_ablage_port.setze_ablage(port)

    fabrik.record_gateway_decision(
        _entscheidung(approved=True, reason_code=ReasonCode.EXIT_EXEMPT, halted=False),
        storno={"symbol": "AAPL", "order_id": "stop-1"},
    )

    _art, daten = port.abgelegt[-1]
    assert daten["symbol"] == "AAPL"
    assert daten["storno_order_id"] == "stop-1"


def test_ohne_anmeldung_logzeile_eine_warning_und_zaehler(caplog):
    with caplog.at_level(logging.INFO):
        fabrik.record_gateway_decision(_entscheidung(), intent=_intent())
        fabrik.record_gateway_decision(_entscheidung(), intent=_intent())

    zeilen = [
        r for r in caplog.records if "[Gateway] decision_id=dec-1" in r.getMessage()
    ]
    assert len(zeilen) == 2
    warnungen = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING
        and "Audit-Ablage nicht verdrahtet" in r.getMessage()
    ]
    assert len(warnungen) == 1
    assert fabrik.get_ablage_zaehler()["nicht_verdrahtet"] == 2


def test_kaputter_port_stoert_die_entscheidung_nicht(caplog):
    audit_ablage_port.setze_ablage(_KaputterPort())

    with caplog.at_level(logging.WARNING):
        fabrik.record_gateway_decision(_entscheidung(), intent=_intent())

    assert any("Audit-Ablage" in r.getMessage() for r in caplog.records)


def test_schutz_exit_geht_hinaus_auch_wenn_die_ablage_wirft():
    audit_ablage_port.setze_ablage(_KaputterPort())
    broker = MagicMock()
    tor = fabrik.gateway_for(broker, is_halted=lambda user_id=None: True)

    entscheidung = tor.submit(
        _intent(side="sell", intent_kind="stop", is_protective_exit=True),
        request={"symbol": "AAPL"},
    )

    assert entscheidung.approved is True
    broker.submit_order.assert_called_once()


def test_der_tor_vertrag_verbietet_die_ablage_direkt():
    ini = configparser.ConfigParser()
    ini.read(
        Path(__file__).parents[1] / "architecture" / "importlinter.ini",
        encoding="utf-8",
    )
    vertrag = ini["importlinter:contract:gateway-kennt-engine-nicht"]
    verboten = vertrag["forbidden_modules"].split()
    assert "core.audit_ablage" in verboten


def test_anmeldung_liegt_im_port_modul():
    port = _Port()
    audit_ablage_port.setze_ablage(port)

    assert audit_ablage_port.hole_ablage() is port


def test_die_root_importiert_das_tor_nicht():
    """Plan §11: Die Recherche erreicht die Root; importierte die Root das Tor, erreichte die
    Recherche VC-3 (Vertrag ``recherche-sendet-nicht``)."""
    import ast
    import inspect

    from core.composition import root

    baum = ast.parse(inspect.getsource(root))
    module = [
        k.module for k in ast.walk(baum) if isinstance(k, ast.ImportFrom) and k.module
    ] + [a.name for k in ast.walk(baum) if isinstance(k, ast.Import) for a in k.names]
    assert not [m for m in module if m.startswith("core.gateway")], module
