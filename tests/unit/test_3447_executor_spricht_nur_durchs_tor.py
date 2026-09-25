"""#3447, Schritt 2b — der Executor spricht den Broker nur noch durchs Tor an.

Schritt 2a (PR #3457) hat die Hauptabsendung des Mandantenpfads umgehaengt. Uebrig waren
acht Stellen in ``core/engine/order_executor.py``: fuenf echte Broker-Aufrufe
(Rueckabwicklungs-BUY, Verdraengungs-SELL in beiden Pfaden, der Markt-Rueckfall, die
Hauptabsendung des globalen Pfads) und drei im Schattenmodus.

ADR-019 §1: „Kein Pfad spricht den Broker direkt an." Dieser Test haelt das fuer den
Executor **strukturell** fest — nicht ueber eine Zahl, die jemand anpassen kann, sondern
ueber die Frage, *wo* ein ``submit_order`` stehen darf.

**Was dieser Test nicht sagt:** Die acht Stellen in ``core/strategies/`` bleiben. Sie
gehoeren zum Legacy-Rueckfallpfad (``_submit_order_safe``) und sind ein eigener Schritt.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from core.exceptions import TradingHaltedError

pytestmark = [pytest.mark.unit, pytest.mark.h3]

_EXECUTOR = (
    Path(__file__).resolve().parents[2] / "core" / "engine" / "order_executor.py"
)

#: Wo ein ``submit_order`` im Executor stehen darf.
#:
#: ``DryRunOrderProxy.submit_order`` ist die *Definition* des Schatten-Brokers, kein
#: Aufruf. Weitere Eintraege gehoeren begruendet in den PR — und sind fast sicher falsch.
_ERLAUBTE_FUNKTIONEN = frozenset({"submit_order"})


def _broker_aufrufe_ausserhalb_des_tors() -> list[str]:
    baum = ast.parse(_EXECUTOR.read_text(encoding="utf-8-sig"))
    funde: list[str] = []

    class _Besucher(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stapel: list[str] = []

        def _funktion(self, node) -> None:
            self.stapel.append(node.name)
            self.generic_visit(node)
            self.stapel.pop()

        visit_FunctionDef = _funktion
        visit_AsyncFunctionDef = _funktion

        def visit_Attribute(self, node: ast.Attribute) -> None:
            # Aufruf (`x.submit_order(...)`) UND Referenz (`to_thread(x.submit_order, r)`):
            # beides ist ein Broker-Zugriff am Tor vorbei.
            if node.attr == "submit_order":
                ort = self.stapel[-1] if self.stapel else "<modul>"
                if ort not in _ERLAUBTE_FUNKTIONEN:
                    funde.append(f"order_executor.py:{node.lineno} in {ort}()")
            self.generic_visit(node)

    _Besucher().visit(baum)
    return funde


def test_im_executor_ruft_niemand_den_broker_am_tor_vorbei() -> None:
    funde = _broker_aufrufe_ausserhalb_des_tors()

    assert not funde, (
        f"{len(funde)} Stelle(n) im Executor sprechen den Broker direkt an:\n  "
        + "\n  ".join(funde)
        + "\nJede davon setzt eine Order ab, zu der das Tor keinen Datensatz schreibt "
        "(ADR-019 §1, #3447)."
    )


def test_der_pruefer_faengt_aufruf_und_referenz() -> None:
    """Der Pruefer selbst: Er muss beide Formen sehen, sonst ist sein Gruen nichts wert."""
    quelle = (
        "async def f(client, r):\n"
        "    client.submit_order(r)\n"
        "    await asyncio.to_thread(client.submit_order, r)\n"
    )
    treffer = [
        n.lineno
        for n in ast.walk(ast.parse(quelle))
        if isinstance(n, ast.Attribute) and n.attr == "submit_order"
    ]
    assert treffer == [2, 3]


# ---------------------------------------------------------------------------
# Verhalten der umgehaengten Stellen
# ---------------------------------------------------------------------------


def _run(coro):
    import asyncio

    return asyncio.run(coro)


@pytest.fixture
def datensaetze(monkeypatch):
    from core.engine import order_executor as oe

    gesammelt = []
    monkeypatch.setattr(oe, "_record_gateway_decision", gesammelt.append)
    return gesammelt


def _halt(monkeypatch, wert: bool) -> None:
    from core.engine import order_executor as oe

    # Auf der KLASSE — siehe test_3447_hauptabsendung_durchs_tor.py (Instanz-Patch leckt).
    monkeypatch.setattr(
        type(oe.kill_switch), "is_halted", lambda self, user_id=None: wert
    )


def _sende(client, **abweichend):
    from alpaca.trading.enums import OrderSide

    from core.engine.order_executor import OrderExecutorMixin

    argumente = dict(
        client=client,
        request=object(),
        symbol="AAPL",
        side_enum=OrderSide.SELL,
        qty=3.0,
        user_id="u1",
        decision_id="entscheidung-9",
    )
    argumente.update(abweichend)
    return _run(OrderExecutorMixin._sende_durchs_tor(**argumente))


def test_die_verdraengung_traegt_ihre_art_im_datensatz(
    datensaetze, monkeypatch
) -> None:
    from unittest.mock import MagicMock

    _halt(monkeypatch, False)
    client = MagicMock()

    _sende(client, intent_kind="displacement")

    client.submit_order.assert_called_once()
    assert len(datensaetze) == 1
    assert "displacement" in datensaetze[0].detail


def test_die_art_ist_kein_frei_waehlbarer_weg_am_halt_vorbei(
    datensaetze, monkeypatch
) -> None:
    """Die Freistellungs-Matrix nimmt ``panic``/``breaker``/``strategy_switch`` vom Halt aus.

    Koennte ein Aufrufer des Executors die Art frei setzen, waere jede davon ein Schalter,
    der den Halt abstellt. Uebersteuern darf darum **nur** die Verdraengung — und die
    steht im Executor ausnahmslos hinter einem eigenen ``check_halt``.
    """
    from unittest.mock import MagicMock

    _halt(monkeypatch, True)
    for art in ("panic", "breaker", "strategy_switch", "stop", "irgendwas"):
        client = MagicMock()
        with pytest.raises(TradingHaltedError, match="TRADING HALTED"):
            _sende(client, intent_kind=art)
        client.submit_order.assert_not_called()


def test_der_rueckabwicklungs_kauf_respektiert_den_halt(
    datensaetze, monkeypatch
) -> None:
    """#2467: „no orders after emergency stop must be airtight".

    Der Re-BUY geht als ``entry`` durchs Tor, nicht als ``displacement`` — sonst stellte
    ihn die Matrix vom Halt frei.
    """
    from unittest.mock import MagicMock

    from alpaca.trading.enums import OrderSide

    _halt(monkeypatch, True)
    client = MagicMock()

    with pytest.raises(TradingHaltedError, match="TRADING HALTED"):
        _sende(client, side_enum=OrderSide.BUY)

    client.submit_order.assert_not_called()
    assert [d.approved for d in datensaetze] == [
        False
    ], "Auch die abgelehnte Order gehoert in den Datensatz."


def test_auch_die_schatten_order_bekommt_einen_datensatz(
    datensaetze, monkeypatch
) -> None:
    from core.engine.order_executor import DryRunOrderProxy

    _halt(monkeypatch, False)
    from unittest.mock import MagicMock

    from alpaca.trading.enums import OrderSide

    req = MagicMock(symbol="AAPL", qty=3.0, side=OrderSide.BUY)
    order = _sende(DryRunOrderProxy("u1"), request=req, side_enum=OrderSide.BUY)

    assert str(order.id).startswith("shadow_")
    assert [d.approved for d in datensaetze] == [True]
