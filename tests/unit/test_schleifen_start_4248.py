"""Start, Lease und Übergabe liegen in ``core/engine/schleifen_start.py``, der Kern erbt sie.

#4248 (H-2g), Teil von ARC-E6 (#3738). Reiner Umzug der neun Start-Methoden aus
``core/engine/trading_loop.py`` nach ``StartMixin`` (Schnitt-Entscheidung #4184 §2). Die
Patch-Ziele der Tests liegen weiter am Kern; das Modul liest sie zur Laufzeit als
``_tl.<name>`` (§3). Plan: ``docs/4248-*/implementation_plan.md`` §6.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc3]

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
MODUL = PAKET / "core" / "engine" / "schleifen_start.py"

METHODEN = {
    "_sichere_schreibberechtigung",
    "_starte_lease_erneuerung",
    "_lease_erneuerung",
    "_lease_fortschritt",
    "_start_fill_stream",
    "_start_reconciliation",
    "_start_outbox_abgleich",
    "_startup_health_check",
    "_perform_graceful_handover",
}
PATCH_ZIELE = {"get_config", "CompositionRoot"}


def test_mixin_definiert_genau_die_neun_methoden():
    from core.engine.schleifen_start import StartMixin

    funktionen = {
        name for name, wert in vars(StartMixin).items() if inspect.isfunction(wert)
    }
    assert funktionen == METHODEN


def test_kern_erbt_und_definiert_sie_nicht_selbst():
    from core.engine.schleifen_start import StartMixin
    from core.engine.trading_loop import TradingLoopMixin

    assert issubclass(TradingLoopMixin, StartMixin)
    assert not METHODEN & set(vars(TradingLoopMixin))


def test_botengine_ueberschreibt_health_check_weiter():
    from core.engine.base import BotEngine
    from core.engine.schleifen_start import StartMixin

    assert BotEngine._startup_health_check is not StartMixin._startup_health_check


def test_kein_modulimport_von_patch_zielen():
    baum = ast.parse(MODUL.read_text(encoding="utf-8"))
    gebunden = set()
    for knoten in baum.body:
        if isinstance(knoten, (ast.Import, ast.ImportFrom)):
            gebunden |= {(a.asname or a.name).split(".")[0] for a in knoten.names}
    assert not gebunden & PATCH_ZIELE
    letzte = baum.body[-1]
    assert isinstance(letzte, ast.ImportFrom)
    assert letzte.module == "core.engine"
    assert [(a.name, a.asname) for a in letzte.names] == [("trading_loop", "_tl")]


@pytest.mark.parametrize("alter, erwartet", [(149.0, True), (151.0, False)])
def test_patch_am_kern_wirkt_im_start(alter, erwartet):
    from core.engine.trading_loop import TradingLoopMixin
    from core.lease import FORTSCHRITT_MAX_ALTER_SEKUNDEN

    assert FORTSCHRITT_MAX_ALTER_SEKUNDEN == 150.0
    jetzt = 10_000.0
    wurzel = SimpleNamespace(clock_port=SimpleNamespace(time=lambda: jetzt))
    with patch("core.engine.trading_loop.CompositionRoot") as cr:
        cr.get_instance.return_value = wurzel
        eng = TradingLoopMixin.__new__(TradingLoopMixin)
        eng._last_cycle_details = {"timestamp": jetzt - alter}
        assert eng._lease_fortschritt() is erwartet
