"""#4126 Teil 1b (Plan §2 Punkt 1, §9c, 9g Punkt 6, 10h Punkt 8): die Senke ``AuditAblage``.

Sie schreibt jedes Ereignis
- in die Hash-Kette (``LocalJSONAuditLogger``, Ereignis ``AuditAblageEvent``) und
- ueber ``log_compliance_event`` mit eigenem ``event_type`` in ``mifid_decision_log``.

Sie ist fail-soft: Ein Fehler wird als WARNING geloggt und gezaehlt, nie geworfen. Die Zeit
kommt aus dem ``ClockPort``.
"""

import asyncio
import json
import logging
import threading
from datetime import datetime, timezone

import pytest

from core.audit_ablage import AuditAblage
from core.ports.clock_port import ClockPort
from core.round_table import senate_log
from core.round_table.senate_log import AuditAblageEvent, _hitl_event_to_dict

pytestmark = pytest.mark.vc0

_ZEIT = datetime(2026, 10, 11, 14, 0, 0, tzinfo=timezone.utc)


class _Uhr(ClockPort):
    def now(self):
        return _ZEIT

    def time(self):
        return _ZEIT.timestamp()


class _Kette:
    def __init__(self, wirft=False):
        self.ereignisse = []
        self.fertig = threading.Event()
        self._wirft = wirft

    async def log_hitl_event(self, event):
        if self._wirft:
            raise RuntimeError("Kette kaputt")
        self.ereignisse.append(event)
        self.fertig.set()


class _Wolke:
    def __init__(self, wirft=False):
        self.aufrufe = []
        self._wirft = wirft

    def log_compliance_event(self, **kw):
        if self._wirft:
            raise RuntimeError("Queue kaputt")
        self.aufrufe.append(kw)


_DATEN = {
    "decision_id": "dec-1",
    "approved": False,
    "reason_code": "trading_halted",
    "halted": True,
    "detail": "Handel gehalten",
    "symbol": "AAPL",
    "side": "buy",
    "qty": 2.0,
    "intent_kind": "entry",
}


@pytest.fixture(autouse=True)
def _zaehler_null():
    senate_log.reset_audit_counters()
    yield
    senate_log.reset_audit_counters()


def _ablage(kette=None, wolke=None):
    return AuditAblage(
        clock=_Uhr(),
        kette=lambda: kette or _Kette(),
        wolke=lambda: wolke or _Wolke(),
    )


def test_schreibt_in_die_kette_mit_der_zeit_des_clockport():
    kette = _Kette()
    _ablage(kette=kette).lege_ab("gateway_decision", _DATEN)

    assert kette.fertig.wait(5)
    ereignis = kette.ereignisse[-1]
    assert isinstance(ereignis, AuditAblageEvent)
    assert ereignis.art == "gateway_decision"
    assert ereignis.timestamp == _ZEIT.isoformat()
    assert json.loads(ereignis.daten)["symbol"] == "AAPL"


def test_kettenzeile_ist_frei_von_kommazahlen():
    kette = _Kette()
    _ablage(kette=kette).lege_ab("gateway_decision", _DATEN)
    assert kette.fertig.wait(5)

    daten = json.loads(kette.ereignisse[-1].daten)
    assert all(isinstance(v, str) for v in daten.values())
    assert daten["qty"] == "2.0"
    assert daten["approved"] == "false"


def test_schreibt_in_mifid_decision_log_mit_eigenem_event_type():
    wolke = _Wolke()
    _ablage(wolke=wolke).lege_ab("gateway_decision", _DATEN)

    aufruf = wolke.aufrufe[-1]
    assert aufruf["event_type"] == "gateway_decision"
    assert aufruf["approved"] is False
    assert aufruf["reason"] == "trading_halted"
    assert aufruf["order"]["symbol"] == "AAPL"
    assert aufruf["order"]["side"] == "buy"
    assert aufruf["order"]["quantity"] == 2.0
    assert aufruf["order"]["intent_kind"] == "entry"


def test_kaputte_kette_wirft_nicht_und_wird_gezaehlt(caplog):
    with caplog.at_level(logging.WARNING):
        _ablage(kette=_Kette(wirft=True)).lege_ab("gateway_decision", _DATEN)
        for _ in range(50):
            if senate_log.get_audit_counters()["write_fail"]:
                break
            threading.Event().wait(0.1)

    assert senate_log.get_audit_counters()["write_fail"] >= 1


def test_kaputte_wolke_wirft_nicht_und_wird_gezaehlt(caplog):
    kette = _Kette()
    with caplog.at_level(logging.WARNING):
        _ablage(kette=kette, wolke=_Wolke(wirft=True)).lege_ab(
            "gateway_decision", _DATEN
        )

    assert senate_log.get_audit_counters()["write_fail"] == 1
    assert any("Audit-Ablage" in r.getMessage() for r in caplog.records)
    assert kette.fertig.wait(5), "die Kette wird trotzdem geschrieben"


def test_im_laufenden_loop_blockiert_die_ablage_nicht():
    kette = _Kette()
    ablage = _ablage(kette=kette)

    async def _lauf():
        ablage.lege_ab("gateway_decision", _DATEN)
        assert kette.ereignisse == []  # noch nicht geschrieben: Hintergrund
        for _ in range(50):
            if kette.ereignisse:
                return
            await asyncio.sleep(0.01)

    asyncio.run(_lauf())
    assert kette.ereignisse


def test_ereignis_wird_mit_seiner_art_serialisiert():
    zeile = _hitl_event_to_dict(
        AuditAblageEvent(timestamp="t", art="gateway_decision", daten="{}")
    )
    assert zeile == {"event_type": "gateway_decision", "timestamp": "t", "daten": "{}"}


def test_unbekannte_art_wird_abgewiesen():
    with pytest.raises(TypeError):
        _hitl_event_to_dict(
            AuditAblageEvent(timestamp="t", art="irgendwas", daten="{}")
        )


def test_echte_kette_bekommt_die_zeile(tmp_path, monkeypatch):
    monkeypatch.setenv("SENATE_LOG_DIR", str(tmp_path))
    ablage = AuditAblage(clock=_Uhr(), wolke=lambda: _Wolke())

    ablage.lege_ab("gateway_decision", _DATEN)

    for _ in range(50):
        zeilen = [
            json.loads(z)
            for d in tmp_path.glob("audit_log_*.jsonl")
            for z in d.read_text(encoding="utf-8").splitlines()
            if z
        ]
        if zeilen:
            break
        threading.Event().wait(0.1)
    assert zeilen[-1]["event_type"] == "gateway_decision"
    assert json.loads(zeilen[-1]["daten"])["decision_id"] == "dec-1"
