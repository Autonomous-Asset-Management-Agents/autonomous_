"""#4008 (ARC-E6 G-2c) — Zyklus-Kontext, Auswertung und Zyklusgrenze als benannte Schritte.

Plan: ``docs/4008-*/implementation_plan.md`` §2/§5/§6.

``live_trading_loop`` ruft für den Kontext-Aufbau, die Auswertung und die Zyklusgrenze je einen
Schritt auf. Die stufenübergreifenden Werte (Zeitmarken, Snapshots, Rangfolge, Portfolio- und
Positions-Snapshot) liegen im frischen ``ZyklusZustand``. Geprüft wird je Schritt: gesetzte
Felder, Watchdog, Schlafdauer, Sim-Treiber — und am ganzen Lauf, dass Broker- und
Gatekeeper-Snapshot je Durchlauf genau einmal entstehen und die Auswertung dieselben Objekte liest.
"""

from __future__ import annotations

import ast
import asyncio
import threading
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc3]

_SCHRITTE = ["_zyklus_kontext_aufbauen", "_zyklus_auswerten", "_zyklusgrenze"]


def _mixin(*, strategie=None):
    from core.engine.trading_loop import TradingLoopMixin

    m = TradingLoopMixin.__new__(TradingLoopMixin)
    m._shutdown_event = threading.Event()
    m.strategy_running = threading.Event()
    m.strategy_running.set()
    m.active_strategy = strategie
    m.api = MagicMock()
    m.compliance_guardian = None
    m.current_market_data = {}
    m._rank_panel_producer = None
    m._fetch_snapshots_chunked = AsyncMock(return_value={"AAPL": object()})
    m._note_position_snapshot = MagicMock()
    m._cycle_latencies = []
    m._last_cycle_details = {}
    m._cycle_watchdog = MagicMock()
    m.cloud_logger = MagicMock()
    m.agent_registry = None
    m._perform_graceful_handover = AsyncMock()
    return m


def _zustand(**felder):
    from core.engine.trading_loop import ZyklusZustand

    z = ZyklusZustand()
    for k, v in felder.items():
        setattr(z, k, v)
    return z


def _lauf(coro):
    return asyncio.run(coro)


@pytest.fixture
def flags(monkeypatch):
    """Flags am echten Config-Objekt setzen; monkeypatch stellt sie danach zurück."""
    from config import get_config

    cfg = get_config()

    def _setzen(**werte):
        for k, v in werte.items():
            monkeypatch.setattr(cfg, k, v, raising=False)

    _setzen(
        REGIME_THROTTLE_ENABLED=False,
        EARNINGS_GUARD_ENABLED=False,
        ROUND_TABLE_TOP_K_EVAL=0,
        LSTM_RANK_PANEL_STRATEGY_INDEPENDENT=False,
        GATEKEEPER_PORTFOLIO_CONTEXT_ENABLED=True,
        FULL_UNIVERSE_TRADING_ENABLED=False,
        SIM_MODE=False,
    )
    return _setzen


@pytest.fixture
def schlaf():
    with patch("core.engine.trading_loop.asyncio.sleep", new=AsyncMock()) as s:
        yield s


@pytest.fixture
def wurzel():
    uhr = SimpleNamespace(time=lambda: 1_790_000_000.0)
    with patch("core.engine.trading_loop.CompositionRoot") as cr:
        cr.get_instance.return_value = SimpleNamespace(clock_port=uhr)
        yield cr


# ── Typen ────────────────────────────────────────────────────────────────────


def test_zyklus_zustand_traegt_die_kontext_felder_frisch():
    a = _zustand()
    b = _zustand()
    a.snapshots["AAPL"] = 1
    a.positions_by_symbol["AAPL"] = 1
    a.symbols_order.append("AAPL")
    a.graph_states.append({})
    a.results.append({})
    assert (b.snapshots, b.positions_by_symbol, b.symbols_order) == ({}, {}, [])
    assert (b.graph_states, b.results) == ([], [])
    assert (b.t_start, b.t_data_fetched, b.t_strategy_done) == (0.0, 0.0, 0.0)
    assert (b.current_time_utc, b.portfolio_context, b.position_confirmed) == (
        None,
        None,
        False,
    )


# ── _zyklus_kontext_aufbauen ─────────────────────────────────────────────────


def test_kontext_setzt_die_neun_felder(flags):
    s = MagicMock(spec=["strategy_name"])
    s.strategy_name = "RLAgent"
    m = _mixin(strategie=s)
    kontext = object()
    positionen = {"AAPL": {"qty": 1.0}}
    z = _zustand(local_active_strategy=s, symbols_to_process=["AAPL", "MSFT"])
    with patch(
        "core.engine.trading_loop.build_portfolio_context",
        new=AsyncMock(return_value=kontext),
    ), patch(
        "core.engine.trading_loop._fetch_position_snapshot",
        new=AsyncMock(return_value=(positionen, True)),
    ):
        assert _lauf(m._zyklus_kontext_aufbauen(z)) is None
    assert 0.0 < z.t_start <= z.t_data_fetched
    assert z.t_strategy_done == z.t_start
    assert z.current_time_utc is not None and z.current_time_utc.tzinfo is not None
    assert z.snapshots == m._fetch_snapshots_chunked.return_value
    assert z.symbols_order == ["AAPL", "MSFT"]
    assert z.portfolio_context is kontext
    assert z.positions_by_symbol is positionen
    assert z.position_confirmed is True
    m._note_position_snapshot.assert_called_once_with(positionen, True)


def test_kontext_kappt_bei_200_ohne_volles_universum(flags):
    m = _mixin()
    z = _zustand(
        local_active_strategy=None, symbols_to_process=[f"S{i}" for i in range(250)]
    )
    with patch(
        "core.engine.trading_loop._fetch_position_snapshot",
        new=AsyncMock(return_value=({}, False)),
    ), patch(
        "core.engine.trading_loop.build_portfolio_context",
        new=AsyncMock(return_value=None),
    ):
        _lauf(m._zyklus_kontext_aufbauen(z))
    assert len(z.symbols_to_process) == 200
    assert z.symbols_order == z.symbols_to_process


def test_kontext_ordnet_lstm_dynamic_nach_rang(flags):
    s = MagicMock()
    s.strategy_name = "LSTMDynamic"
    s.update_lstm_rankings = AsyncMock()
    s._lstm_rank_cache = [("MSFT", 0.9), ("AAPL", 0.5)]
    m = _mixin(strategie=s)
    z = _zustand(local_active_strategy=s, symbols_to_process=["AAPL", "TSLA", "MSFT"])
    with patch(
        "core.engine.trading_loop._fetch_position_snapshot",
        new=AsyncMock(return_value=({}, False)),
    ), patch(
        "core.engine.trading_loop.build_portfolio_context",
        new=AsyncMock(return_value=None),
    ):
        _lauf(m._zyklus_kontext_aufbauen(z))
    assert z.symbols_order == ["MSFT", "AAPL", "TSLA"]
    s.update_lstm_rankings.assert_awaited_once()


def test_kontext_ohne_gatekeeper_flag_baut_keinen_portfolio_snapshot(flags):
    flags(GATEKEEPER_PORTFOLIO_CONTEXT_ENABLED=False)
    m = _mixin()
    z = _zustand(symbols_to_process=["AAPL"])
    bauen = AsyncMock(return_value=object())
    with patch("core.engine.trading_loop.build_portfolio_context", new=bauen), patch(
        "core.engine.trading_loop._fetch_position_snapshot",
        new=AsyncMock(return_value=({}, False)),
    ):
        _lauf(m._zyklus_kontext_aufbauen(z))
    bauen.assert_not_awaited()
    assert z.portfolio_context is None


# ── _zyklus_auswerten ────────────────────────────────────────────────────────


def test_auswerten_misst_latenz_und_meldet_leeren_zyklus(wurzel):
    m = _mixin()
    z = _zustand(
        t_start=1.0,
        t_data_fetched=1.5,
        symbols_to_process=["AAPL"],
        graph_states=[{"symbol": "AAPL"}],
        results=[],
    )
    with patch("core.engine.trading_loop.time.perf_counter", return_value=3.0):
        assert _lauf(m._zyklus_auswerten(z)) is None
    assert z.t_strategy_done == 3.0
    assert m._last_cycle_details["total_ms"] == 2000.0
    assert m._last_cycle_details["data_fetch_ms"] == 500.0
    assert m._last_cycle_details["symbols_processed"] == 1
    assert m._cycle_latencies == [2000.0]
    m.cloud_logger.log_latency_metric.assert_called_once()
    m._cycle_watchdog.record_empty_cycle.assert_called_once_with(1)
    assert m._cycles_completed == 1


def test_auswerten_meldet_erfolgreichen_zyklus(wurzel):
    m = _mixin()
    z = _zustand(
        t_start=1.0,
        t_data_fetched=1.0,
        graph_states=[{"symbol": "AAPL"}],
        results=[{"signal": None}],
    )
    with patch("core.engine.trading_loop.time.perf_counter", return_value=1.1):
        _lauf(m._zyklus_auswerten(z))
    m._cycle_watchdog.record_successful_cycle.assert_called_once()
    m._cycle_watchdog.record_empty_cycle.assert_not_called()


# ── _zyklusgrenze ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("geschlossen,dauer", [(False, 60), (True, 300)])
def test_grenze_live_schlaeft_im_takt(flags, schlaf, geschlossen, dauer):
    m = _mixin()
    m.agent_registry = MagicMock()
    m.agent_registry.has_pending_swap.return_value = True
    assert _lauf(m._zyklusgrenze(_zustand(cycle_market_closed=geschlossen))) is None
    m._perform_graceful_handover.assert_awaited_once()
    schlaf.assert_awaited_once_with(dauer)


def test_grenze_sim_rollt_tag_und_bewertet_buch(flags, schlaf):
    flags(SIM_MODE=True)
    m = _mixin()
    m._sim_complete = threading.Event()
    reihenfolge = []
    uhr = MagicMock()
    uhr.advance.side_effect = lambda: reihenfolge.append("advance") or True
    uhr.current_time = datetime(2026, 8, 4, 13, 30, tzinfo=timezone.utc)
    uhr.days_completed, uhr.n_days, uhr.is_open = 1, 2, False
    m.api.mark_to_market.side_effect = lambda: reihenfolge.append("mark")
    with patch("core.sim.clock.get_sim_clock", return_value=uhr):
        _lauf(m._zyklusgrenze(_zustand()))
    assert reihenfolge == ["advance", "mark"]
    assert m._sim_cycles == 1
    assert not m.strategy_running.is_set()
    assert m._sim_complete.is_set()
    schlaf.assert_awaited_once_with(0)


# ── Lauf: Snapshots je Durchlauf genau einmal, Fehler im Kontext ─────────────


def _schleife():
    from tests.unit.test_pr4_cycle_resilience import _make_loop

    obj = _make_loop()
    obj._sichere_schreibberechtigung = AsyncMock(return_value=True)
    obj._starte_lease_erneuerung = MagicMock()
    obj._process_signal_event = AsyncMock()
    obj.cloud_logger = MagicMock()
    obj._cycle_latencies = []
    obj._last_cycle_details = {}
    return obj


async def test_snapshots_einmal_je_durchlauf_und_dieselben_objekte(flags):
    obj = _schleife()
    obj._cycle_watchdog = MagicMock()
    obj._fetch_snapshots_chunked = AsyncMock(
        return_value={"AAPL": SimpleNamespace(latest_trade=object())}
    )
    kontext = object()
    bauen = AsyncMock(return_value=kontext)
    positionen = AsyncMock(return_value=({"AAPL": {"qty": 1.0}}, True))
    gesehen = []

    async def _ainvoke(state, config=None):
        gesehen.append(state)
        obj._shutdown_event.set()  # nach diesem Durchlauf endet die Schleife
        return {"symbol": state["symbol"], "signal": None}

    graph = SimpleNamespace(ainvoke=_ainvoke)
    ohlc = {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0}
    with patch("core.engine.trading_loop.kill_switch", None), patch(
        "core.engine.trading_loop.build_portfolio_context", new=bauen
    ), patch(
        "core.engine.trading_loop._fetch_position_snapshot", new=positionen
    ), patch(
        "core.engine.trading_loop.build_symbol_eval_graph", return_value=graph
    ), patch(
        "core.engine.trading_loop._extract_ohlc_from_snapshot",
        return_value=(ohlc, 1.5, datetime.now(timezone.utc)),
    ), patch(
        "core.engine.trading_loop.CompositionRoot"
    ):
        await obj.live_trading_loop()

    assert bauen.await_count == 1
    assert positionen.await_count == 1
    assert len(gesehen) == 1
    assert gesehen[0]["_portfolio_context"] is kontext
    obj._cycle_watchdog.record_successful_cycle.assert_called_once()


async def test_broker_fehler_im_kontext_wird_behandelt(flags):
    from core.exceptions import BrokerConnectionError

    obj = _schleife()
    obj._cycle_watchdog = MagicMock()
    obj._fetch_snapshots_chunked = AsyncMock(return_value={})
    schlaefe = []

    async def _schlaf(delay=0, *_a, **_k):
        schlaefe.append(delay)
        if delay == 30:
            obj._shutdown_event.set()

    with patch("core.engine.trading_loop.kill_switch", None), patch(
        "core.engine.trading_loop.build_portfolio_context",
        new=AsyncMock(return_value=None),
    ), patch(
        "core.engine.trading_loop._fetch_position_snapshot",
        new=AsyncMock(side_effect=BrokerConnectionError("weg")),
    ), patch(
        "core.engine.trading_loop.asyncio.sleep", new=_schlaf
    ):
        await obj.live_trading_loop()

    obj._cycle_watchdog.record_empty_cycle.assert_called_once_with(0)
    assert 30 in schlaefe
    assert obj.strategy_running.is_set()


# ── Dirigent ─────────────────────────────────────────────────────────────────


def _klasse():
    from tests.unit import _schleifen_quelle

    baum = ast.parse(_schleifen_quelle.PFAD.read_text(encoding="utf-8"))
    return next(
        k
        for k in baum.body
        if isinstance(k, ast.ClassDef) and k.name == "TradingLoopMixin"
    )


def test_dirigent_ruft_die_drei_schritte_in_reihenfolge():
    from tests.unit import _schleifen_quelle

    schritte = _schleifen_quelle.schritte()
    positionen = [schritte.index(n) for n in _SCHRITTE]
    assert positionen == sorted(positionen)
    assert schritte.index("_schutz_vor_konsens") < positionen[0]


def test_dirigent_liest_keine_kontext_lokalen_mehr():
    """Die Kontext-Werte leben im Zustand; der Dirigent bindet sie nicht mehr lokal."""
    dirigent = next(
        f for f in _klasse().body if getattr(f, "name", "") == "live_trading_loop"
    )
    lokal = {
        n.id
        for n in ast.walk(dirigent)
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
    }
    assert not lokal & {
        "t_start",
        "t_data_fetched",
        "t_strategy_done",
        "current_time_utc",
        "snapshots",
        "symbols_order",
        "_portfolio_context",
        "_positions_by_symbol",
        "_position_confirmed",
        "graph_states",
        "results",
    }


def test_die_groessen_der_schleife_stimmen_mit_dem_vertrag():
    """Datei- und Dirigentenzahl gemessen eingetragen — in beide Richtungen.

    Direkt gegen ``regeln.pruefe_groessen``: die Größenregel steht im Vertrag noch auf
    ``warnen`` (G-0) und bräche den allgemeinen Lauf nicht.
    """
    from pathlib import Path

    from tests.architecture import regeln

    wurzel = Path(__file__).resolve().parents[2]
    meldungen = regeln.pruefe_groessen(wurzel, regeln.lade_vertrag())
    eigene = [
        m
        for m in meldungen
        if m.startswith("Groessen: core/engine/trading_loop.py ")
        or "live_trading_loop" in m
    ]
    assert not eigene, "\n".join(eigene)


def test_jeder_neue_schritt_liegt_unter_der_funktionsschwelle():
    laengen = {
        f.name: f.end_lineno - f.lineno + 1
        for f in _klasse().body
        if isinstance(f, (ast.AsyncFunctionDef, ast.FunctionDef))
        and (f.name in _SCHRITTE or f.name.startswith("_kontext_"))
    }
    assert set(_SCHRITTE) <= set(laengen)
    assert all(n < 150 for n in laengen.values()), laengen
