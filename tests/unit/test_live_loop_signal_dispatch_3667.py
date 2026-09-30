"""#3667 — live_trading_loop darf Modulnamen nicht als Funktions-Lokale binden.

Seit #3546 stand im Enthaltungs-Zweig „Graph konnte nicht geladen werden" ein funktionslokaler
Import von ``SignalEvent`` und ``DecisionContext``. Python bindet den Namen damit für die GESAMTE
Funktion als lokale Variable; im Normalpfad (Graph geladen) wird der Zweig nie ausgeführt und die
``isinstance(res.get("signal"), SignalEvent)``-Prüfung liest eine ungebundene Lokale →
``UnboundLocalError`` in jedem Zyklus, kein Signal erreicht den Order-Executor.

Die Tests prüfen die Ursache direkt am Code-Objekt und am Syntaxbaum, damit die Regression nicht
wieder still durch einen „lokal importieren, um Zyklen zu vermeiden"-Reflex zurückkommt.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

pytestmark = [pytest.mark.vc0]

from core.engine import trading_loop as tl  # noqa: E402

_MODULNAMEN = {"SignalEvent", "DecisionContext"}


def _live_loop_function():
    fn = tl.TradingLoopMixin.live_trading_loop
    return inspect.unwrap(fn)


class TestKeineLokaleBindungDerModulnamen:
    def test_code_objekt_bindet_signalevent_und_decisioncontext_nicht_lokal(self):
        co = _live_loop_function().__code__
        lokal = _MODULNAMEN & set(co.co_varnames)
        assert not lokal, (
            f"live_trading_loop bindet {sorted(lokal)} als Funktions-Lokale — "
            "jeder Zyklus endet mit UnboundLocalError, bevor ein Signal dispatcht wird (#3667)."
        )

    def test_kein_funktionslokaler_import_der_modulnamen(self):
        src = inspect.getsource(_live_loop_function())
        tree = ast.parse(textwrap.dedent(src))
        lokale_importe = [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
            if (alias.asname or alias.name) in _MODULNAMEN
        ]
        assert not lokale_importe, (
            f"Funktionslokaler Import von {lokale_importe} in live_trading_loop; "
            "die Namen sind auf Modulebene importiert (core.events / core.cloud_logger)."
        )

    def test_modulnamen_sind_auf_modulebene_gebunden(self):
        assert tl.SignalEvent.__name__ == "SignalEvent"
        assert tl.DecisionContext.__name__ == "DecisionContext"
