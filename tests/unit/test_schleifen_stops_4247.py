"""Die Stops liegen in ``core/engine/schleifen_stops.py``, der Kern erbt sie.

#4247 (H-2f), Teil von ARC-E6 (#3738). Reiner Umzug der drei Stop-Methoden aus
``core/engine/trading_loop.py`` nach ``StopsMixin`` (Schnitt-Entscheidung #4184 §2). Die
Patch-Ziele der Tests liegen weiter am Kern; das Modul liest sie zur Laufzeit als
``_tl.<name>`` (§3). Plan: ``docs/4247-*/implementation_plan.md`` §6.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc3]

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
MODUL = PAKET / "core" / "engine" / "schleifen_stops.py"

METHODEN = {
    "_melde_liegende_stops_einmal",
    "_maintain_broker_stops",
    "_run_position_stop_checks",
}
PATCH_ZIELE = {"get_config", "engine_now", "plan_position_stops", "kill_switch"}


def test_mixin_definiert_genau_die_drei_methoden():
    from core.engine.schleifen_stops import StopsMixin

    funktionen = {
        name for name, wert in vars(StopsMixin).items() if inspect.isfunction(wert)
    }
    assert funktionen == METHODEN


def test_kern_erbt_und_definiert_sie_nicht_selbst():
    from core.engine.schleifen_stops import StopsMixin
    from core.engine.trading_loop import TradingLoopMixin

    assert issubclass(TradingLoopMixin, StopsMixin)
    assert not METHODEN & set(vars(TradingLoopMixin))


def test_kein_modulimport_von_patch_zielen():
    baum = ast.parse(MODUL.read_text(encoding="utf-8"))
    gebunden = set()
    for knoten in ast.walk(baum):
        if isinstance(knoten, (ast.Import, ast.ImportFrom)):
            gebunden |= {(a.asname or a.name).split(".")[0] for a in knoten.names}
    assert not gebunden & PATCH_ZIELE
    letzte = baum.body[-1]
    assert isinstance(letzte, ast.ImportFrom)
    assert letzte.module == "core.engine"
    assert [(a.name, a.asname) for a in letzte.names] == [("trading_loop", "_tl")]


async def test_patch_am_kern_wirkt_in_den_positions_stops():
    from core.engine.trading_loop import TradingLoopMixin

    gesehen = {}

    def planer(positions, trade_history, **kw):
        gesehen["now"] = kw["now"]
        return []

    stempel = object()
    with patch("core.engine.trading_loop.plan_position_stops", planer), patch(
        "core.engine.trading_loop.engine_now", return_value=stempel
    ):
        eng = TradingLoopMixin.__new__(TradingLoopMixin)
        eng.api = SimpleNamespace(get_all_positions=lambda: [])
        eng._ratchet_high_water_marks = AsyncMock(return_value={})
        eng._ratchet_entry_times = lambda positions, history: {}
        gestoppt = await eng._run_position_stop_checks()
    assert gestoppt == set()
    assert gesehen["now"] is stempel
