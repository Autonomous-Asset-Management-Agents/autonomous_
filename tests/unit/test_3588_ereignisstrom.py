"""#3588 (ARC-E2.16) — der Ereignisstrom des Abgleichs muss ueberhaupt laufen.

Gemessen am 23.09.2026 an ``origin/main``: Der Strom ist wirkungslos, aus zwei
unabhaengigen Gruenden.

1. ``core/reconciliation.py`` erzeugt das Stream-Objekt und ruft
   ``subscribe_trade_updates(...)`` — mehr nicht. In ``alpaca-py`` legt dieser Aufruf nur
   den Handler ab (``alpaca/trading/stream.py:113-125``); empfangen wird erst in
   ``run()`` bzw. ``_run_forever()``. Diesen Aufruf gibt es im Repo nirgends.
2. ``alpaca-py`` verlangt eine **Koroutine** als Handler und wirft sonst
   ``ValueError: handler must be a coroutine function`` (``stream.py:215-225``). Der
   Handler war eine gewoehnliche Funktion — das Abonnement scheiterte also schon vorher,
   fail-soft protokolliert.

Folge: Einzige Stufe des Abgleichs war der periodische Lauf (30 s). Diese Tests fahren
einen **Fake-Strom ohne Netz** (die Naht liegt aussen, genau dafuer).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_AI_BOT = Path(__file__).resolve().parents[2]
if str(_AI_BOT) not in sys.path:
    sys.path.insert(0, str(_AI_BOT))

pytestmark = pytest.mark.vc5


class FakeStream:
    """Bildet die Zusagen von ``alpaca.trading.stream.TradingStream`` nach."""

    def __init__(self) -> None:
        self.handler = None
        self.laeuft = False
        self.gestoppt = False
        self.schleife_gelaufen = False

    def subscribe_trade_updates(self, handler):
        # Dieselbe Bedingung wie die Bibliothek (stream.py:215-225).
        if not asyncio.iscoroutinefunction(handler):
            raise ValueError("handler must be a coroutine function")
        self.handler = handler

    async def _run_forever(self):
        self.schleife_gelaufen = True
        self.laeuft = True
        try:
            while not self.gestoppt:
                await asyncio.sleep(0.01)
        finally:
            self.laeuft = False

    async def stop_ws(self):
        self.gestoppt = True


class FakeOrder:
    def __init__(self, oid="o-1", qty=2.0, preis=100.0, status="filled"):
        self.id = oid
        self.client_order_id = "entry-0-entscheidung-1"
        self.symbol = "GATE"
        self.side = "buy"
        self.status = status
        self.filled_qty = qty
        self.filled_avg_price = preis


def _dienst():
    from core.reconciliation import ReconciliationService

    return ReconciliationService(object(), None)


def _laufen_lassen(coro):
    return asyncio.run(coro)


def test_der_handler_ist_eine_koroutine():
    """Heute rot: der Handler ist eine gewoehnliche Funktion, die Bibliothek weist ihn ab."""
    strom = FakeStream()
    dienst = _dienst()

    async def _fahre():
        dienst.start_fill_stream(lambda: strom)
        await asyncio.sleep(0.05)
        await dienst.stop_fill_stream()

    _laufen_lassen(_fahre())
    assert (
        strom.handler is not None
    ), "kein Handler abonniert — das Abonnement scheiterte"
    assert asyncio.iscoroutinefunction(strom.handler)


def test_die_empfangsschleife_laeuft_als_eigene_aufgabe():
    """Heute rot: niemand startet ``_run_forever``; der Strom liegt nur herum."""
    strom = FakeStream()
    dienst = _dienst()

    async def _fahre():
        dienst.start_fill_stream(lambda: strom)
        await asyncio.sleep(0.05)
        laeuft = strom.laeuft
        await dienst.stop_fill_stream()
        return laeuft

    assert _laufen_lassen(_fahre()) is True
    assert strom.schleife_gelaufen is True


def test_ein_ereignis_wird_als_fill_erfasst():
    strom = FakeStream()
    dienst = _dienst()

    class Ereignis:
        order = FakeOrder()

    async def _fahre():
        dienst.start_fill_stream(lambda: strom)
        await asyncio.sleep(0.05)
        await strom.handler(Ereignis())
        await dienst.stop_fill_stream()

    _laufen_lassen(_fahre())
    assert len(dienst.fills) == 1
    assert dienst.fills[0].filled_qty == pytest.approx(2.0)


def test_derselbe_fill_zaehlt_nur_einmal():
    """Strom und periodischer Lauf tragen in denselben Schluesselraum ein."""
    strom = FakeStream()
    dienst = _dienst()
    order = FakeOrder()

    class Ereignis:
        pass

    Ereignis.order = order

    async def _fahre():
        dienst.start_fill_stream(lambda: strom)
        await asyncio.sleep(0.05)
        await strom.handler(Ereignis())
        await strom.handler(Ereignis())
        await dienst.stop_fill_stream()

    _laufen_lassen(_fahre())
    assert len(dienst.fills) == 1


def test_ein_nicht_gefuelltes_ereignis_wird_ignoriert():
    strom = FakeStream()
    dienst = _dienst()

    class Ereignis:
        order = FakeOrder(status="new")

    async def _fahre():
        dienst.start_fill_stream(lambda: strom)
        await asyncio.sleep(0.05)
        await strom.handler(Ereignis())
        await dienst.stop_fill_stream()

    _laufen_lassen(_fahre())
    assert dienst.fills == []


def test_das_herunterfahren_laesst_keine_aufgabe_zurueck():
    strom = FakeStream()
    dienst = _dienst()

    async def _fahre():
        dienst.start_fill_stream(lambda: strom)
        await asyncio.sleep(0.05)
        await dienst.stop_fill_stream()
        await asyncio.sleep(0.05)
        return strom.laeuft, dienst._stream_task

    laeuft, aufgabe = _laufen_lassen(_fahre())
    assert laeuft is False
    assert aufgabe is None or aufgabe.done()


def test_ein_kaputter_strom_bricht_den_abgleich_nicht():
    """Fail-soft bleibt: ohne Strom traegt der periodische Lauf."""
    dienst = _dienst()

    def _fabrik():
        raise RuntimeError("kein Netz")

    async def _fahre():
        dienst.start_fill_stream(_fabrik)
        await asyncio.sleep(0.02)
        await dienst.stop_fill_stream()

    _laufen_lassen(_fahre())
    assert dienst._stream is None


# ---------------------------------------------------------------------------
# Engine-Seite: der Strom kommt dunkel herein
# ---------------------------------------------------------------------------


class _EngineStub:
    """Nur die Naht, die ``_start_fill_stream`` braucht."""

    def __init__(self, dienst):
        self.reconciler = dienst


def _start_fill_stream(engine):
    from core.engine.trading_loop import TradingLoopMixin

    return TradingLoopMixin._start_fill_stream(engine)


def test_der_strom_bleibt_ohne_schalter_aus(monkeypatch):
    """Dunkel per Default: ohne Schalter wird nichts abonniert."""
    import config

    dienst = _dienst()
    aufrufe = []
    dienst.start_fill_stream = lambda fabrik: aufrufe.append(fabrik)
    monkeypatch.setattr(
        config.get_config(), "RECONCILIATION_STREAM_ENABLED", False, raising=False
    )
    _start_fill_stream(_EngineStub(dienst))
    assert aufrufe == []


def test_der_schalter_ohne_zugangsdaten_abonniert_nichts(monkeypatch):
    import config

    dienst = _dienst()
    aufrufe = []
    dienst.start_fill_stream = lambda fabrik: aufrufe.append(fabrik)
    cfg = config.get_config()
    monkeypatch.setattr(cfg, "RECONCILIATION_STREAM_ENABLED", True, raising=False)
    monkeypatch.setattr(cfg, "ALPACA_API_KEY", None, raising=False)
    monkeypatch.setattr(cfg, "ALPACA_SECRET_KEY", None, raising=False)
    _start_fill_stream(_EngineStub(dienst))
    assert aufrufe == []


def test_mit_schalter_und_zugangsdaten_wird_abonniert(monkeypatch):
    import config

    dienst = _dienst()
    aufrufe = []
    dienst.start_fill_stream = lambda fabrik: aufrufe.append(fabrik)
    cfg = config.get_config()
    monkeypatch.setattr(cfg, "RECONCILIATION_STREAM_ENABLED", True, raising=False)
    monkeypatch.setattr(cfg, "ALPACA_API_KEY", "k", raising=False)
    monkeypatch.setattr(cfg, "ALPACA_SECRET_KEY", "s", raising=False)
    _start_fill_stream(_EngineStub(dienst))
    assert len(aufrufe) == 1 and callable(aufrufe[0])


def test_der_strom_folgt_dem_handelsmodus(monkeypatch):
    """#3627: Hier stand ``ALPACA_PAPER`` — ein Name, den die Konfiguration nie kannte.

    Der Rueckfallwert ``True`` war damit die einzige Quelle: Der Strom ging IMMER zum
    Paper-Endpunkt, auch im Live-Betrieb, wo die Anmeldung mit Live-Zugangsdaten dort
    fehlschlaegt. Seit #3627 entscheidet ``PAPER_TRADING`` — der Name, den die
    Konfiguration wirklich kennt.
    """
    from alpaca.trading import stream as alpaca_stream

    import config

    gebaut: dict = {}

    class _Stream:
        def __init__(self, key, secret, paper=True):
            gebaut.update(key=key, secret=secret, paper=paper)

    monkeypatch.setattr(alpaca_stream, "TradingStream", _Stream)
    dienst = _dienst()
    fabriken: list = []
    dienst.start_fill_stream = lambda fabrik: fabriken.append(fabrik)
    cfg = config.get_config()
    monkeypatch.setattr(cfg, "RECONCILIATION_STREAM_ENABLED", True, raising=False)
    monkeypatch.setattr(cfg, "ALPACA_API_KEY", "k", raising=False)
    monkeypatch.setattr(cfg, "ALPACA_SECRET_KEY", "s", raising=False)
    monkeypatch.setattr(cfg, "PAPER_TRADING", False, raising=False)

    _start_fill_stream(_EngineStub(dienst))
    fabriken[0]()

    assert gebaut["paper"] is False


def test_der_strom_ist_per_default_an():
    """Owner-Entscheid 23.09.2026 nach der Messung am Paper-Konto.

    Gemessen: ein Fill ist nach 0,7-2,5 s bekannt statt erst mit dem naechsten
    periodischen Lauf (bis 30 s). Der periodische Lauf bleibt die tragende Stufe — was
    waehrend einer Trennung gefuellt wird, liefert der Strom NICHT nach (ebenfalls
    gemessen), das findet der Lauf als ``missing_fill``.
    """
    import config

    assert config.get_config().RECONCILIATION_STREAM_ENABLED is True


# ---------------------------------------------------------------------------
# Gemessen am Paper-Konto (23.09.2026) — zwei Defekte, die den Strom stumm hielten
# ---------------------------------------------------------------------------


class EnumStatus(str):
    """Bildet ``alpaca.trading.enums.OrderStatus`` nach: ``str()`` liefert den NAMEN.

    Seit Python 3.11 gibt ``str(OrderStatus.FILLED)`` ``"OrderStatus.FILLED"`` zurueck,
    nicht ``"filled"``. Genau daran scheiterte jeder Statusvergleich.
    """

    value = "filled"

    def __str__(self):  # noqa: D105
        return "OrderStatus.FILLED"


def test_ein_enum_status_wird_als_gefuellt_erkannt():
    """Der Messlauf vom 23.09. meldete 0 Fills, obwohl zwei Orders gefuellt waren."""
    strom = FakeStream()
    dienst = _dienst()
    order = FakeOrder()
    order.status = EnumStatus()

    class Ereignis:
        pass

    Ereignis.order = order

    async def _fahre():
        dienst.start_fill_stream(lambda: strom)
        await asyncio.sleep(0.05)
        await strom.handler(Ereignis())
        await dienst.stop_fill_stream()

    _laufen_lassen(_fahre())
    assert len(dienst.fills) == 1


def test_der_periodische_lauf_holt_auch_abgeschlossene_orders():
    """``get_orders()`` ohne Filter liefert bei Alpaca nur OFFENE Orders — eine gefuellte
    Order stand damit in keiner Liste, und der Nachtrag konnte nie greifen.

    Der erste Lauf uebernimmt den Bestand still (auch die abgeschlossenen Orders, sonst
    meldete der zweite Lauf die ganze Historie). Erst ein DANACH gefuellter Auftrag ist
    ein entgangener Fill.
    """
    from core.reconciliation import ReconciliationService

    class FakeApi:
        def __init__(self):
            self.abfragen = []
            self.abgeschlossen = []

        def get_orders(self, filter=None):  # noqa: A002 — Signatur von alpaca-py
            self.abfragen.append(filter)
            return [] if filter is None else list(self.abgeschlossen)

        def get_all_positions(self):
            return []

    api = FakeApi()
    dienst = ReconciliationService(api, None)

    asyncio.run(dienst.run_once())  # Bestandsuebernahme
    assert len(api.abfragen) == 2, "die abgeschlossenen Orders werden nicht abgerufen"
    assert dienst.fills == []

    order = FakeOrder(oid="geschlossen-1")
    order.status = EnumStatus()
    api.abgeschlossen.append(order)

    satz = asyncio.run(dienst.run_once())
    assert [f.broker_order_id for f in dienst.fills] == ["geschlossen-1"]
    assert [b.kind for b in satz.breaks] == ["missing_fill"]


def test_der_bestand_vor_dem_start_ist_kein_entgangener_fill():
    from core.reconciliation import ReconciliationService

    class FakeApi:
        def __init__(self):
            order = FakeOrder(oid="alt-1")
            order.status = EnumStatus()
            self.abgeschlossen = [order]

        def get_orders(self, filter=None):  # noqa: A002
            return [] if filter is None else list(self.abgeschlossen)

        def get_all_positions(self):
            return []

    dienst = ReconciliationService(FakeApi(), None)
    asyncio.run(dienst.run_once())
    satz = asyncio.run(dienst.run_once())
    assert satz.breaks == ()
    assert dienst.fills == []
