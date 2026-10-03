"""#3383 (Epic #3366) — die drei Ausnahmepfade schreiben eine Entscheidung.

Notverkauf, Breaker-Liquidation und Strategiewechsel bewegen **auf einen Schlag den
groessten Teil des Bestands** — und hinterlassen heute keinen Datensatz:

* ``core/engine/api_routes.py`` (``panic_sell``) ruft ``engine.api.submit_order`` direkt,
* ``core/risk_manager.py`` ruft ``close_all_positions(cancel_orders=True)`` direkt,
* ``core/engine/monitor_loop.py`` ruft ``close_all_positions(...)`` direkt.

Damit fehlen genau die Faelle im Audit, in denen am meisten Kapital auf einmal bewegt
wird. Im Nachhinein ist nicht belegbar, wer den Verkauf auf welcher Grundlage ausgeloest
hat.

Ein Sammelaufruf kann das auch nicht heilen: ``close_all_positions`` kennt weder Symbol
noch Menge — der Datensatz waere eine Absichtserklaerung („alles"), keine Aufzeichnung.
Deshalb wird er hier in eine Schleife ueber die tatsaechlich gehaltenen Positionen
aufgeloest, und jede Bewegung bekommt ihren eigenen Intent.
"""

import re
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parents[2] / "core"


def _quelle(rel: str) -> str:
    return (CORE / rel).read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# 1 — Vokabular: Grund-Codes und Freistellungs-Matrix
# ---------------------------------------------------------------------------


def test_die_drei_grund_codes_existieren():
    from core.contracts import ReasonCode

    assert ReasonCode.EMERGENCY.value == "emergency"
    assert ReasonCode.BREAKER.value == "breaker"
    assert ReasonCode.STRATEGY_SWITCH.value == "strategy_switch"


def test_die_drei_pfade_stehen_in_der_freistellungs_matrix():
    """Bevorrechtigt heisst: nie blockiert, aber immer protokolliert.

    Ein Notverkauf, den eine Einstiegsregel abweist, waere ein zugesperrter Notausgang.
    """
    from core.gateway import NEVER_BLOCKED_KINDS

    for art in ("panic", "breaker", "strategy_switch"):
        assert art in NEVER_BLOCKED_KINDS, f"{art} fehlt in der Freistellungs-Matrix"


# ---------------------------------------------------------------------------
# 2 — Die Sammelaufrufe sind aufgeloest
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rel,pfad",
    [
        ("risk_manager.py", "Breaker-Liquidation"),
        ("engine/monitor_loop.py", "Strategiewechsel"),
    ],
)
def test_kein_sammelaufruf_mehr(rel, pfad):
    """``close_all_positions`` kennt weder Symbol noch Menge — darueber laesst sich
    hinterher keine Rechenschaft ablegen."""
    treffer = [
        f"{rel}:{no}"
        for no, zeile in enumerate(_quelle(rel).splitlines(), 1)
        if "close_all_positions(" in zeile and not zeile.strip().startswith("#")
    ]
    assert not treffer, (
        f"{pfad} ruft weiterhin close_all_positions: {treffer}. Der Sammelaufruf muss in "
        "eine Schleife ueber die gehaltenen Positionen aufgeloest werden, damit je "
        "Bewegung ein Datensatz entsteht (#3383)."
    )


def test_notverkauf_geht_nicht_mehr_direkt_an_den_broker():
    quelle = _quelle("engine/api_routes.py")
    beginn = quelle.index("async def panic_sell")
    ende = quelle.index('@app.post("/reset-kill-switch")', beginn)
    koerper = quelle[beginn:ende]

    direkt = [
        z.strip()
        for z in koerper.splitlines()
        if re.search(r"\.submit_order\s*\(", z) and not z.strip().startswith("#")
    ]
    assert not direkt, (
        f"panic_sell setzt weiterhin direkt am Broker ab: {direkt}. Der Notverkauf ist "
        "der Pfad, der den GESAMTEN Bestand liquidiert — er gehoert durch das Tor."
    )


# ---------------------------------------------------------------------------
# 3 — Das Verhalten der Aufloesung
# ---------------------------------------------------------------------------


class _Position:
    def __init__(self, symbol, qty, qty_available=None):
        self.symbol = symbol
        self.qty = qty
        if qty_available is not None:
            self.qty_available = qty_available


class _Broker:
    def __init__(self, scheitert_bei=()):
        self.gesendet = []
        self._scheitert_bei = set(scheitert_bei)

    def submit_order(self, request):
        symbol = request["symbol"]
        if symbol in self._scheitert_bei:
            raise RuntimeError(f"Broker lehnt {symbol} ab")
        self.gesendet.append(symbol)
        return {"id": f"b-{symbol}"}


def _tor(broker, *, halted=False):
    from core.gateway import OrderGateway

    return OrderGateway(
        broker=broker,
        is_halted=lambda user_id=None: halted,
        record=lambda decision: None,
    )


def _auftrag(symbol, qty):
    return {"symbol": symbol, "qty": qty}


def test_je_position_ein_intent():
    from core.gateway import liquidate_positions

    broker = _Broker()
    bericht = liquidate_positions(
        _tor(broker),
        [_Position("AAPL", "2"), _Position("MSFT", "1.5")],
        decision_id="d-notfall",
        intent_kind="panic",
        request_factory=_auftrag,
    )

    assert broker.gesendet == ["AAPL", "MSFT"]
    assert bericht.attempted == 2
    assert bericht.submitted == 2
    assert bericht.complete is True


def test_bruchstuecke_werden_nicht_gerundet():
    """Bruchteilige Positionen bleiben — sie hier auf ganze Stuecke zu kuerzen, liesse
    im Notverkauf genau die Reste stehen, die niemand mehr schliesst."""
    from core.gateway import liquidate_positions

    gesendet = []

    def merken(symbol, qty):
        gesendet.append((symbol, qty))
        return {"symbol": symbol, "qty": qty}

    liquidate_positions(
        _tor(_Broker()),
        [_Position("AAPL", "0.017")],
        decision_id="d-notfall",
        intent_kind="panic",
        request_factory=merken,
    )

    assert gesendet == [("AAPL", 0.017)]


def test_ein_fehlschlag_stoppt_die_liquidation_nicht():
    """Die Aufloesung aendert das Ausfallverhalten: ein Sammelaufruf geht ganz oder gar
    nicht, eine Schleife kann bei Position sieben von zehn scheitern. Der Rest muss
    trotzdem geschlossen werden."""
    from core.gateway import liquidate_positions

    broker = _Broker(scheitert_bei={"MSFT"})
    bericht = liquidate_positions(
        _tor(broker),
        [_Position("AAPL", "1"), _Position("MSFT", "1"), _Position("NVDA", "1")],
        decision_id="d-breaker",
        intent_kind="breaker",
        request_factory=_auftrag,
    )

    assert broker.gesendet == ["AAPL", "NVDA"]
    assert bericht.submitted == 2
    assert bericht.complete is False
    assert [s for s, _ in bericht.failed] == ["MSFT"]


def test_der_teilerfolg_wird_gemeldet_nicht_verschwiegen(caplog):
    from core.gateway import liquidate_positions

    with caplog.at_level("ERROR"):
        liquidate_positions(
            _tor(_Broker(scheitert_bei={"MSFT"})),
            [_Position("AAPL", "1"), _Position("MSFT", "1")],
            decision_id="d-breaker",
            intent_kind="breaker",
            request_factory=_auftrag,
        )

    meldungen = " ".join(r.getMessage() for r in caplog.records)
    assert "MSFT" in meldungen
    assert "1" in meldungen, "Die Differenz muss beziffert sein, nicht nur erwaehnt"


def test_der_halt_blockiert_den_notverkauf_nicht():
    """Der Notverkauf legt den Kill-Switch selbst um (api_routes: kill_switch.trip).

    Wuerde der Halt danach die Liquidation abweisen, haette der Notverkauf sich selbst
    ausgesperrt — der haeufigste Weg, einen Notausgang unbrauchbar zu machen.
    """
    from core.gateway import liquidate_positions

    broker = _Broker()
    bericht = liquidate_positions(
        _tor(broker, halted=True),
        [_Position("AAPL", "1")],
        decision_id="d-notfall",
        intent_kind="panic",
        request_factory=_auftrag,
    )

    assert broker.gesendet == ["AAPL"]
    assert bericht.submitted == 1


def test_leere_positionen_werden_uebersprungen():
    from core.gateway import liquidate_positions

    broker = _Broker()
    bericht = liquidate_positions(
        _tor(broker),
        [_Position("AAPL", "0"), _Position("MSFT", "1")],
        decision_id="d-notfall",
        intent_kind="panic",
        request_factory=_auftrag,
    )

    assert broker.gesendet == ["MSFT"]
    assert bericht.attempted == 1


def test_verfuegbare_menge_hat_vorrang():
    """``qty_available`` beruecksichtigt bereits gebundene Stuecke; ``qty`` nicht."""
    from core.gateway import liquidate_positions

    gesendet = []

    def merken(symbol, qty):
        gesendet.append((symbol, qty))
        return {"symbol": symbol, "qty": qty}

    liquidate_positions(
        _tor(_Broker()),
        [_Position("AAPL", "5", qty_available="3")],
        decision_id="d",
        intent_kind="panic",
        request_factory=merken,
    )

    assert gesendet == [("AAPL", 3.0)]


def test_jede_bewegung_traegt_ihre_entscheidung():
    from core.gateway import liquidate_positions

    entscheidungen = []
    from core.gateway import OrderGateway

    tor = OrderGateway(
        broker=_Broker(),
        is_halted=lambda user_id=None: False,
        record=entscheidungen.append,
    )

    liquidate_positions(
        tor,
        [_Position("AAPL", "1"), _Position("MSFT", "1")],
        decision_id="d-wechsel",
        intent_kind="strategy_switch",
        request_factory=_auftrag,
    )

    assert len(entscheidungen) == 2
    assert all(e.decision_id == "d-wechsel" for e in entscheidungen)
    assert all(e.approved for e in entscheidungen)
