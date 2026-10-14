"""#4237 (H-1h) — Outbox und Tor wohnen in ``absendung_tor.py``.

Plan: ``docs/4237-*/implementation_plan.md`` §4/§5. Entscheidung:
``docs/3738-arc-e6-gestalt/H1_SCHNITT_order_executor.md``, Abschnitt H-1h.

Fünf freie Funktionen und ``_submit_with_market_failsafe`` ziehen wörtlich um,
``_sende_durchs_tor`` wird in zwei statische Schritte geschnitten (``_tor_absenden``,
``_tor_gesperrt``). ``TorMixin`` ist Basisklasse von ``OrderExecutorMixin``, nicht komponiert:
Die Aufrufer rufen das Tor ohne Instanz über ``OrderExecutorMixin`` (Entscheidung §3).
"""

from __future__ import annotations

import ast
import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.architecture import regeln

pytestmark = [pytest.mark.unit, pytest.mark.h1]

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
ENGINE = PAKET / "core" / "engine"
DATEI_GRENZE = 800
FUNKTION_GRENZE = 150
TOR = ENGINE / "absendung_tor.py"
METHODEN = (
    "_sende_durchs_tor",
    "_submit_with_market_failsafe",
    "_tor_absenden",
    "_tor_gesperrt",
)
FREIE = (
    "_outbox_sitzung",
    "_outbox_schliessen",
    "ist_duplikat",
    "_bestehende_order",
    "_outbox_schritt",
)
RE_EXPORT = ("_outbox_sitzung", "_outbox_schritt", "ist_duplikat")


def test_tor_wohnt_in_tormixin():
    from core.engine.absendung_tor import TorMixin
    from core.engine.order_executor import OrderExecutorMixin

    for name in METHODEN:
        assert name in TorMixin.__dict__, name
        assert name not in OrderExecutorMixin.__dict__, name
    assert TorMixin in OrderExecutorMixin.__mro__
    assert TorMixin in OrderExecutorMixin.__bases__


def test_sende_durchs_tor_bleibt_statisch():
    from core.engine.order_executor import OrderExecutorMixin

    for name in ("_sende_durchs_tor", "_tor_absenden", "_tor_gesperrt"):
        assert isinstance(
            inspect.getattr_static(OrderExecutorMixin, name), staticmethod
        ), name


def test_outbox_funktionen_wohnen_im_tor():
    from core.engine import absendung_tor
    from core.engine import order_executor as oe

    for name in FREIE:
        assert getattr(absendung_tor, name).__module__ == absendung_tor.__name__, name
    assert hasattr(absendung_tor, "_OUTBOX_OHNE_ABLAGE_GEMELDET")
    for name in RE_EXPORT:
        assert getattr(oe, name) is getattr(absendung_tor, name), name
    # Weg a: Eine Kopie im Kern wäre toter Zustand, ein Patch dort liefe ins Leere.
    assert not hasattr(oe, "_OUTBOX_OHNE_ABLAGE_GEMELDET")


def test_tor_bleibt_unter_den_schwellen():
    """Epic §8.2: höchstens 800 je Datei, 150 je Funktion — strenger als der Vertrag."""
    vertrag = regeln.lade_vertrag()["groessen"]
    datei = min(DATEI_GRENZE, vertrag["datei_schwelle"])
    funktion = min(FUNKTION_GRENZE, vertrag["funktion_schwelle"])
    zeilen = len(TOR.read_text(encoding="utf-8").splitlines())
    assert zeilen <= datei, zeilen
    gemessen = [
        b
        for b in regeln.funktions_groessen(PAKET, "core/engine")
        if b.datei == "core/engine/absendung_tor.py"
    ]
    assert {b.was for b in gemessen} >= {f"TorMixin.{m}" for m in METHODEN}
    zu_lang = {b.was: int(b.zusatz) for b in gemessen if int(b.zusatz) > funktion}
    assert not zu_lang, zu_lang


def test_tor_importiert_den_kern_nicht_auf_modulebene():
    """Ein Modulimport überschattete den Namen am Kern; ein Patch dort liefe vorbei."""
    verboten = {"order_executor", "config", "kill_switch", "logging"}
    baum = ast.parse(TOR.read_text(encoding="utf-8"))
    for knoten in baum.body:
        if isinstance(knoten, ast.Import):
            namen = {a.asname or a.name.split(".")[-1] for a in knoten.names}
        elif isinstance(knoten, ast.ImportFrom):
            namen = {a.asname or a.name for a in knoten.names}
            namen.add((knoten.module or "").split(".")[-1])
        else:
            continue
        assert not namen & verboten, (knoten.lineno, namen & verboten)


class _Tor:
    """Ein Tor, das annimmt und festhält, was es bekam."""

    def __init__(self):
        self.gesehen = []

    def submit_with_result(self, intent, *, request):
        self.gesehen.append((intent, request))
        return SimpleNamespace(approved=True), SimpleNamespace(id="brk-1")


@pytest.fixture
def tor(monkeypatch):
    from core.engine import order_executor as oe

    monkeypatch.delenv("AAA_USER_DATA_DIR", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    gefangen = _Tor()
    monkeypatch.setattr(oe, "gateway_for", lambda client: gefangen)
    monkeypatch.setattr(
        type(oe.kill_switch), "is_halted", lambda self, user_id=None: False
    )
    return gefangen


def _sende(**abweichend):
    from alpaca.trading.enums import OrderSide

    from core.engine.order_executor import OrderExecutorMixin

    argumente = {
        "client": MagicMock(),
        "request": MagicMock(client_order_id="trim-0-entscheidung-4237"),
        "symbol": "FCX",
        "side_enum": OrderSide.SELL,
        "qty": 3.0,
        "user_id": "u1",
        "decision_id": "entscheidung-4237",
    }
    argumente.update(abweichend)
    return asyncio.run(OrderExecutorMixin._sende_durchs_tor(**argumente))


def test_verkaufsfreigabe_erreicht_das_tor(tor, monkeypatch):
    """Späte Bindung: Das Tor bekommt Intent und Anfrage NACH ``gib_verkauf_frei`` (#4033)."""
    import core.engine.broker_stop_pflege as pflege

    ersetzt = {}

    def _frei(client, intent, request):
        ersetzt["intent"] = intent.model_copy(update={"qty": 1.0})
        ersetzt["request"] = MagicMock(client_order_id=request.client_order_id)
        return ersetzt["intent"], ersetzt["request"]

    monkeypatch.setattr(pflege, "gib_verkauf_frei", AsyncMock(side_effect=_frei))

    order = _sende()

    assert order.id == "brk-1"
    assert len(tor.gesehen) == 1
    intent, request = tor.gesehen[0]
    assert intent is ersetzt["intent"]
    assert request is ersetzt["request"]


def test_tor_liest_kill_switch_am_kern(tor, monkeypatch):
    """``patch("core.engine.order_executor.kill_switch")`` muss im Intent ankommen."""
    import core.engine.broker_stop_pflege as pflege

    monkeypatch.setattr(
        pflege,
        "gib_verkauf_frei",
        AsyncMock(side_effect=lambda client, intent, request: (intent, request)),
    )
    ks = MagicMock()
    ks.is_halted = MagicMock(return_value=True)
    with patch("core.engine.order_executor.kill_switch", ks):
        _sende()

    intent, _ = tor.gesehen[0]
    assert intent.halted is True
    ks.is_halted.assert_called_once_with("u1")
