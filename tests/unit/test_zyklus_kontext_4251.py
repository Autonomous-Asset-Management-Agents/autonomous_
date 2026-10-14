"""Der Zyklus-Kontext liegt in ``core/engine/zyklus_kontext.py``, der Kern erbt ihn.

#4251 (H-2j), Teil von ARC-E6 (#3738). Reiner Umzug der fünf Kontext-Schritte aus
``core/engine/trading_loop.py`` nach ``ZyklusKontextMixin`` (Schnitt-Entscheidung #4184 §2). Die
Patch-Ziele der Tests liegen weiter am Kern; das Modul liest sie zur Laufzeit als
``_tl.<name>`` (§3). Plan: ``docs/4251-*/implementation_plan.md`` §6.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc3]

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
MODUL = PAKET / "core" / "engine" / "zyklus_kontext.py"

METHODEN = {
    "_zyklus_kontext_aufbauen",
    "_kontext_rangfolge",
    "_kontext_signale_auffrischen",
    "_kontext_snapshots",
    "_kontext_rang_trichter",
}
PATCH_ZIELE = {
    "get_config",
    "engine_now",
    "build_portfolio_context",
    "_fetch_position_snapshot",
    "logger",  # Logger-Name core.engine.trading_loop bleibt
}


# ── Ort ──────────────────────────────────────────────────────────────────────


def test_kontext_liegt_in_zyklus_kontext():
    from core.engine.trading_loop import TradingLoopMixin
    from core.engine.zyklus_kontext import ZyklusKontextMixin

    funktionen = {
        name
        for name, wert in vars(ZyklusKontextMixin).items()
        if inspect.isfunction(wert)
    }
    assert funktionen == METHODEN
    assert issubclass(TradingLoopMixin, ZyklusKontextMixin)
    assert not METHODEN & set(vars(TradingLoopMixin))
    for name in METHODEN:
        assert getattr(TradingLoopMixin, name) is vars(ZyklusKontextMixin)[name]


def test_kein_modulimport_von_patch_zielen():
    baum = ast.parse(MODUL.read_text(encoding="utf-8"))
    gebunden = set()
    for knoten in baum.body:
        if isinstance(knoten, (ast.Import, ast.ImportFrom)):
            gebunden |= {(a.asname or a.name).split(".")[0] for a in knoten.names}
        elif isinstance(knoten, ast.Assign):
            gebunden |= {t.id for t in knoten.targets if isinstance(t, ast.Name)}
    assert not gebunden & PATCH_ZIELE
    letzte = baum.body[-1]
    assert isinstance(letzte, ast.ImportFrom)
    assert letzte.module == "core.engine"
    assert [(a.name, a.asname) for a in letzte.names] == [("trading_loop", "_tl")]


# ── Charakterisierung: vor dem Umzug grün, danach unverändert ────────────────


def _mixin():
    from core.engine.trading_loop import TradingLoopMixin

    m = TradingLoopMixin.__new__(TradingLoopMixin)
    m._shutdown_event = threading.Event()
    m.api = MagicMock()
    m.compliance_guardian = None
    m.current_market_data = {}
    m.data_provider = None
    m._rank_panel_producer = None
    m._fetch_snapshots_chunked = AsyncMock(return_value={"AAPL": object()})
    m._note_position_snapshot = MagicMock()
    return m


def _cfg(**werte):
    basis = {
        "FULL_UNIVERSE_TRADING_ENABLED": False,
        "LSTM_RANK_PANEL_STRATEGY_INDEPENDENT": False,
        "ROUND_TABLE_RANK_REFRESH_MINUTES": 0,
        "EARNINGS_GUARD_ENABLED": False,
        "REGIME_THROTTLE_ENABLED": False,
        "SIM_MODE": False,
        "GATEKEEPER_PORTFOLIO_CONTEXT_ENABLED": True,
        "ROUND_TABLE_TOP_K_EVAL": 0,
        "ROUND_TABLE_FUNNEL_MAX_PANEL_AGE_DAYS": 3,
    }
    basis.update(werte)
    return SimpleNamespace(**basis)


def test_kontext_folgt_kern_patches():
    from core.engine.zyklus import ZyklusZustand

    m = _mixin()
    kontext = object()
    positionen = {"AAPL": {"qty": 1.0}}
    z = ZyklusZustand()
    z.local_active_strategy = None
    z.symbols_to_process = ["AAPL"]
    with patch("core.engine.trading_loop.get_config", return_value=_cfg()), patch(
        "core.engine.trading_loop.build_portfolio_context",
        new=AsyncMock(return_value=kontext),
    ), patch(
        "core.engine.trading_loop._fetch_position_snapshot",
        new=AsyncMock(return_value=(positionen, True)),
    ):
        asyncio.run(m._zyklus_kontext_aufbauen(z))
    assert z.portfolio_context is kontext
    assert z.positions_by_symbol is positionen
    assert z.position_confirmed is True


def test_earnings_warnung_am_kern_logger(caplog):
    from core.engine.zyklus import ZyklusZustand

    m = _mixin()
    vorige = MagicMock()
    vorige.done.return_value = True
    vorige.cancelled.return_value = False
    vorige.exception.return_value = RuntimeError("edgar weg")
    m._earnings_cache_task = vorige
    z = ZyklusZustand()
    z.symbols_to_process = ["AAPL"]
    z.current_time_utc = datetime(2026, 10, 8, 15, 0, tzinfo=timezone.utc)

    async def _lauf():
        await m._kontext_signale_auffrischen(z)
        await m._earnings_cache_task

    with patch(
        "core.engine.trading_loop.get_config",
        return_value=_cfg(EARNINGS_GUARD_ENABLED=True),
    ), patch(
        "core.engine.earnings_guard.ensure_fresh_earnings_cache"
    ) as auffrischen, caplog.at_level(
        logging.WARNING
    ):
        asyncio.run(_lauf())
    warnungen = [
        r for r in caplog.records if "EarningsGuard cache refresh failed" in r.message
    ]
    assert [r.name for r in warnungen] == ["core.engine.trading_loop"]
    auffrischen.assert_called_once_with(["AAPL"], z.current_time_utc)


def test_trichter_behaelt_bestand():
    from core.engine.zyklus import ZyklusZustand

    m = _mixin()
    jetzt = datetime(2026, 10, 8, 15, 0, tzinfo=timezone.utc)
    store = MagicMock()
    store.latest_snapshot_date.return_value = jetzt.date()
    xsec = {"MSFT": 0.9, "NVDA": 0.8, "AAPL": 0.1, "TSLA": 0.05}
    z = ZyklusZustand()
    z.current_time_utc = jetzt
    z.symbols_order = ["AAPL", "TSLA", "MSFT", "NVDA"]
    z.positions_by_symbol = {"TSLA": {"qty": 3.0}}
    z.position_confirmed = True
    with patch(
        "core.engine.trading_loop.get_config",
        return_value=_cfg(ROUND_TABLE_TOP_K_EVAL=2),
    ), patch("core.report.lstm_panel_store.get_store", return_value=store), patch(
        "core.report.lstm_panel_store.active_cross_section", return_value=xsec
    ):
        m._kontext_rang_trichter(z)
    assert z.symbols_order == ["TSLA", "MSFT", "NVDA"]
