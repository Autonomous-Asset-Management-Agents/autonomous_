"""Der Zyklus-Vorlauf liegt in ``core/engine/zyklus_vorlauf.py``, der Kern erbt ihn.

#4250 (H-2i), Teil von ARC-E6 (#3738). Reiner Umzug der vier Vorlauf-Schritte aus
``core/engine/trading_loop.py`` nach ``ZyklusVorlaufMixin`` (Schnitt-Entscheidung #4184 §2). Die
Patch-Ziele der Tests liegen weiter am Kern; das Modul liest sie zur Laufzeit als
``_tl.<name>`` (§3). Plan: ``docs/4250-*/implementation_plan.md`` §6.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc3]

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
MODUL = PAKET / "core" / "engine" / "zyklus_vorlauf.py"

METHODEN = {
    "_zyklus_vorbereiten",
    "_marktzeit_pruefen",
    "_markt_geschlossen_berichten",
    "_schutz_vor_konsens",
}
PATCH_ZIELE = {
    "kill_switch",
    "BYPASS_MARKET_HOURS",
    "engine_now",
    "CompositionRoot",
    "get_config",
    "asyncio",  # schlaf_nur_im_modul tauscht nur die Referenz im Kern
}


def test_mixin_definiert_genau_die_vier_methoden():
    from core.engine.zyklus_vorlauf import ZyklusVorlaufMixin

    funktionen = {
        name
        for name, wert in vars(ZyklusVorlaufMixin).items()
        if inspect.iscoroutinefunction(wert)
    }
    assert funktionen == METHODEN


def test_kern_erbt_und_definiert_sie_nicht_selbst():
    from core.engine.trading_loop import TradingLoopMixin
    from core.engine.zyklus_vorlauf import ZyklusVorlaufMixin

    assert issubclass(TradingLoopMixin, ZyklusVorlaufMixin)
    assert not METHODEN & set(vars(TradingLoopMixin))


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
    typen = next(
        k
        for k in baum.body
        if isinstance(k, ast.ImportFrom) and k.module == "core.engine.zyklus"
    )
    assert {a.name for a in typen.names} == {"Zyklus", "ZyklusZustand"}


def _mixin():
    from core.engine.trading_loop import TradingLoopMixin

    m = TradingLoopMixin.__new__(TradingLoopMixin)
    m._shutdown_event = threading.Event()
    m.strategy_running = threading.Event()
    m.strategy_running.set()
    m.strategy_lock = threading.Lock()
    m.active_strategy = None
    m.compliance_guardian = None
    m._sichere_schreibberechtigung = AsyncMock()
    m._hitl_day_rollover = AsyncMock()
    m._warm_lstm_bar_cache = AsyncMock()
    m._drain_hitl_approvals = AsyncMock()
    m._reconcile_active_strategy_entry_time = AsyncMock()
    m._log_strategy_thought = MagicMock()
    return m


def _kommt_aus_dem_vorlauf(name: str) -> None:
    from core.engine.trading_loop import TradingLoopMixin
    from core.engine.zyklus_vorlauf import ZyklusVorlaufMixin

    assert getattr(TradingLoopMixin, name) is vars(ZyklusVorlaufMixin)[name]


def test_kill_switch_patch_am_kern_wirkt_im_vorlauf():
    from core.engine.zyklus import Zyklus, ZyklusZustand

    _kommt_aus_dem_vorlauf("_zyklus_vorbereiten")
    m = _mixin()
    gehalten = MagicMock()
    gehalten.is_halted.return_value = True
    with patch("core.engine.trading_loop.kill_switch", gehalten):
        ergebnis = asyncio.run(m._zyklus_vorbereiten(ZyklusZustand()))
    assert ergebnis is Zyklus.STOPP
    assert m._shutdown_event.is_set()
    assert not m.strategy_running.is_set()


def test_schlaf_patch_am_kern_wirkt_im_vorlauf():
    """``schlaf_nur_im_modul("core.engine.trading_loop")`` tauscht ``asyncio`` nur im Kern.

    Der Halt-Schlaf (#3380) liegt jetzt im Vorlauf; ohne ``_tl.asyncio`` schliefe er echt
    60 s (``test_halt_protective_exit.py``, Fixture ``schlaf`` in ``test_zyklus_schritte_4007.py``).
    """
    from core.engine.zyklus import Zyklus, ZyklusZustand
    from tests.helpers.schlaf import schlaf_nur_im_modul

    _kommt_aus_dem_vorlauf("_schutz_vor_konsens")
    m = _mixin()
    m._update_live_account_equity = AsyncMock()
    m._maintain_broker_stops = AsyncMock()
    m._run_position_stop_checks = AsyncMock(return_value=set())
    strategie = SimpleNamespace(
        symbols=["AAPL"], risk_manager=SimpleNamespace(trading_halted=True)
    )
    z = ZyklusZustand()
    z.local_active_strategy = strategie

    async def _lauf():
        return await asyncio.wait_for(m._schutz_vor_konsens(z), timeout=5)

    with schlaf_nur_im_modul("core.engine.trading_loop") as schlaf:
        ergebnis = asyncio.run(_lauf())
    assert ergebnis is Zyklus.NAECHSTER
    schlaf.assert_awaited_once_with(60)


def test_bypass_patch_am_kern_wirkt_im_vorlauf():
    from core.engine.zyklus import ZyklusZustand

    _kommt_aus_dem_vorlauf("_marktzeit_pruefen")
    m = _mixin()
    m.api = MagicMock()
    m.api.get_clock.return_value = SimpleNamespace(is_open=False, next_open=None)
    m.active_strategy = SimpleNamespace(symbols=["AAPL"])  # kein Leerlauf-Schlaf
    for bypass, geschlossen in ((True, False), (False, True)):
        z = ZyklusZustand()
        with patch("core.engine.trading_loop.BYPASS_MARKET_HOURS", bypass):
            asyncio.run(m._marktzeit_pruefen(z))
        assert z.cycle_market_closed is geschlossen, bypass
