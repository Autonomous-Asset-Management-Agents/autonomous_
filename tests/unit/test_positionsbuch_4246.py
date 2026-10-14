"""Das Positionsbuch liegt in ``core/engine/positionsbuch.py``, der Kern erbt es.

#4246 (H-2e), Teil von ARC-E6 (#3738). Reiner Umzug der fünf Positionsbuch-Methoden und der
Konstante ``ENTRY_TIME_RECONCILE_COOLDOWN_S`` aus ``core/engine/trading_loop.py`` nach
``PositionsbuchMixin`` (Schnitt-Entscheidung #4184 §2). Die Patch-Ziele der Tests liegen
weiter am Kern; das Modul liest sie zur Laufzeit als ``_tl.<name>`` (§3).
Plan: ``docs/4246-*/implementation_plan.md`` §6.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from core.engine import positionsbuch, trading_loop
from core.engine.positionsbuch import PositionsbuchMixin
from core.engine.trading_loop import TradingLoopMixin

pytestmark = [pytest.mark.unit, pytest.mark.vc3]

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
MODUL = PAKET / "core" / "engine" / "positionsbuch.py"

METHODEN = {
    "_reconcile_active_strategy_entry_time",
    "_ratchet_high_water_marks",
    "_note_position_snapshot",
    "_stopout_reentry_locked",
    "_ratchet_entry_times",
}
PATCH_ZIELE = {
    "get_config",
    "engine_now",
    "CompositionRoot",
    "load_position_hwm",
    "save_position_hwm",
}


def test_mixin_definiert_genau_die_fuenf_methoden():
    funktionen = {
        name
        for name, wert in vars(PositionsbuchMixin).items()
        if inspect.isfunction(wert)
    }
    assert funktionen == METHODEN


def test_kern_erbt_und_definiert_sie_nicht_selbst():
    assert issubclass(TradingLoopMixin, PositionsbuchMixin)
    assert not METHODEN & set(vars(TradingLoopMixin))


def test_konstante_wandert_mit():
    assert positionsbuch.ENTRY_TIME_RECONCILE_COOLDOWN_S == 300.0
    assert not hasattr(trading_loop, "ENTRY_TIME_RECONCILE_COOLDOWN_S")


def test_kein_modulimport_von_patch_zielen():
    baum = ast.parse(MODUL.read_text(encoding="utf-8"))
    gebunden = set()
    for knoten in baum.body:
        if isinstance(knoten, (ast.Import, ast.ImportFrom)):
            gebunden |= {(a.asname or a.name).split(".")[0] for a in knoten.names}
        elif isinstance(knoten, ast.Assign):
            gebunden |= {z.id for z in knoten.targets if isinstance(z, ast.Name)}
    assert not gebunden & PATCH_ZIELE
    letzte = baum.body[-1]
    assert isinstance(letzte, ast.ImportFrom)
    assert letzte.module == "core.engine"
    assert [(a.name, a.asname) for a in letzte.names] == [("trading_loop", "_tl")]


async def test_patch_am_kern_wirkt_im_positionsbuch():
    cfg = SimpleNamespace(
        POSITION_EXIT_HWM_TRAILING_ENABLED=True,
        POSITION_EXIT_HWM_PERSIST_ENABLED=True,
    )
    laden = AsyncMock(return_value={"DDOG": 250.0})
    sichern = AsyncMock()
    with patch("core.engine.trading_loop.get_config", return_value=cfg), patch(
        "core.engine.trading_loop.load_position_hwm", laden
    ), patch("core.engine.trading_loop.save_position_hwm", sichern):
        eng = TradingLoopMixin.__new__(TradingLoopMixin)
        karte = await eng._ratchet_high_water_marks(
            [SimpleNamespace(symbol="DDOG", current_price=200.0)]
        )
    assert karte == {"DDOG": 250.0}
    laden.assert_awaited_once()
    sichern.assert_awaited_once_with({"DDOG": 250.0})
