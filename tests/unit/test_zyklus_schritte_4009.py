"""#4009 (ARC-E6 G-2d) — Symbolvorbereitung, Round-Table-Bewertung und Signal-Übergabe als Schritte.

Plan: ``docs/4009-g2d-schleife-bewertung-und-ausfuehrung/implementation_plan.md`` §2/§5/§6.

Der letzte Schleifen-Teil: ``live_trading_loop`` ruft für die Symbolvorbereitung, die Bewertung
durch den Round Table und die Übergabe der Signale je einen Schritt auf und ist danach ein
Dirigent unter der Funktionsschwelle. Geprüft wird am ganzen Lauf (vor dem Umbau grün): die
Reihenfolge der Signale am Order-Executor, der Shutdown während der Vorbereitung, der leere
Durchlauf ohne Shutdown und die Zeitüberschreitung — und je Schritt: Zustand hinein, Zustand und
Rückgabe heraus.
"""

from __future__ import annotations

import ast
import asyncio
import logging
import threading
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc3]

_SCHRITTE = ["_symbole_vorbereiten", "_round_table_bewerten", "_signale_ausfuehren"]
_OHLC = {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0}


def _signal(symbol, action="BUY"):
    from core.cloud_logger import DecisionContext
    from core.events import SignalEvent

    return SignalEvent(
        symbol=symbol,
        action=action,
        decision_context=DecisionContext(reasoning_summary="test", action=action),
    )


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
        GATEKEEPER_PORTFOLIO_CONTEXT_ENABLED=False,
        FULL_UNIVERSE_TRADING_ENABLED=False,
        SIM_MODE=False,
    )
    return _setzen


@pytest.fixture
def frischer_kurs():
    with patch(
        "core.engine.trading_loop._extract_ohlc_from_snapshot",
        side_effect=lambda _s: (dict(_OHLC), 1.5, datetime.now(timezone.utc)),
    ) as p:
        yield p


# ── Lauf: das Verhalten, das der Umbau halten muss (vor dem Umbau grün) ──────


def _schleife(symbole):
    from tests.unit.test_pr4_cycle_resilience import _make_loop

    obj = _make_loop()
    obj.active_strategy.symbols = list(symbole)
    obj._sichere_schreibberechtigung = AsyncMock(return_value=True)
    obj._starte_lease_erneuerung = MagicMock()
    obj._process_signal_event = AsyncMock()
    obj._reconcile_specialist_coverage = AsyncMock()
    obj.cloud_logger = MagicMock()
    obj._cycle_latencies = []
    obj._last_cycle_details = {}
    obj._cycle_watchdog = MagicMock()
    obj._fetch_snapshots_chunked = AsyncMock(
        return_value={s: SimpleNamespace(latest_trade=object()) for s in symbole}
    )
    obj._zyklusgrenze = AsyncMock(side_effect=lambda _z: obj._shutdown_event.set())
    return obj


def _umgebung(graph):
    """Außenwelt eines Durchlaufs: kein Kill Switch, keine Positionen, der Graph als Double."""
    from contextlib import ExitStack

    stapel = ExitStack()
    stapel.enter_context(patch("core.engine.trading_loop.kill_switch", None))
    stapel.enter_context(
        patch(
            "core.engine.trading_loop._fetch_position_snapshot",
            new=AsyncMock(return_value=({}, False)),
        )
    )
    stapel.enter_context(
        patch("core.engine.trading_loop.build_symbol_eval_graph", return_value=graph)
    )
    stapel.enter_context(patch("core.engine.trading_loop.CompositionRoot"))
    return stapel


async def test_jedes_signal_geht_in_derselben_reihenfolge_an_den_executor(
    flags, frischer_kurs
):
    obj = _schleife(["AAPL", "MSFT"])

    async def _ainvoke(state, **_kw):
        if state["symbol"] == "AAPL":
            await asyncio.sleep(
                0.01
            )  # AAPL fertig NACH MSFT — die Reihenfolge darf nicht wandern
        return {"symbol": state["symbol"], "signal": _signal(state["symbol"])}

    with _umgebung(SimpleNamespace(ainvoke=AsyncMock(side_effect=_ainvoke))):
        await obj.live_trading_loop()

    gesendet = [c.args[0] for c in obj._process_signal_event.await_args_list]
    assert [(s.symbol, s.action) for s in gesendet] == [
        ("AAPL", "BUY"),
        ("MSFT", "BUY"),
    ]
    obj._cycle_watchdog.record_successful_cycle.assert_called_once()
    obj._zyklusgrenze.assert_awaited_once()


async def test_shutdown_waehrend_der_vorbereitung_beendet_die_schleife_sofort(
    flags, frischer_kurs
):
    obj = _schleife(["AAPL", "MSFT"])
    ainvoke = AsyncMock()

    def _kurs_und_shutdown(_s):
        obj._shutdown_event.set()
        return dict(_OHLC), 1.5, datetime.now(timezone.utc)

    frischer_kurs.side_effect = _kurs_und_shutdown
    obj._zyklus_auswerten = AsyncMock()
    with _umgebung(SimpleNamespace(ainvoke=ainvoke)):
        await obj.live_trading_loop()

    ainvoke.assert_not_awaited()
    obj._process_signal_event.assert_not_awaited()
    obj._zyklus_auswerten.assert_not_awaited()
    obj._zyklusgrenze.assert_not_awaited()


async def test_keine_symbole_ohne_shutdown_laeuft_bis_zur_zyklusgrenze(flags):
    obj = _schleife(["AAPL"])
    obj._fetch_snapshots_chunked = AsyncMock(
        return_value={}
    )  # kein Snapshot → kein State
    bauen = MagicMock()
    obj._zyklus_auswerten = AsyncMock()
    with _umgebung(None), patch(
        "core.engine.trading_loop.build_symbol_eval_graph", new=bauen
    ):
        await obj.live_trading_loop()

    bauen.assert_not_called()
    obj._process_signal_event.assert_not_awaited()
    obj._zyklus_auswerten.assert_awaited_once()
    obj._zyklusgrenze.assert_awaited_once()


async def test_zeitueberschreitung_erzeugt_keine_order(flags, frischer_kurs, caplog):
    obj = _schleife(["AAPL"])

    async def _haengt(state, **_kw):
        await asyncio.sleep(1.0)

    caplog.set_level(logging.WARNING)
    with _umgebung(SimpleNamespace(ainvoke=AsyncMock(side_effect=_haengt))), patch(
        "core.engine.trading_loop._symbol_eval_timeout", return_value=0.01
    ):
        await obj.live_trading_loop()

    obj._process_signal_event.assert_not_awaited()
    assert any(
        "MIFID_AUDIT[AAPL] SYMBOL_TIMEOUT" in r.getMessage() for r in caplog.records
    )


# ── Schritte ─────────────────────────────────────────────────────────────────


def _mixin():
    from core.engine.trading_loop import TradingLoopMixin

    m = TradingLoopMixin.__new__(TradingLoopMixin)
    m._shutdown_event = threading.Event()
    m._skipped_symbols = set()
    m._log_strategy_thought = MagicMock()
    m._hitl_symbol_pending = AsyncMock(return_value=False)
    m.current_market_data = {}
    m.active_strategy = None
    m._process_signal_event = AsyncMock()
    m._reconcile_specialist_coverage = AsyncMock()
    return m


def _zustand(**felder):
    from core.engine.trading_loop import ZyklusZustand

    z = ZyklusZustand(current_time_utc=datetime.now(timezone.utc))
    for k, v in felder.items():
        setattr(z, k, v)
    return z


def test_vorbereiten_fuellt_graph_states_und_gibt_weiter(flags, frischer_kurs):
    from core.engine.trading_loop import Zyklus

    m = _mixin()
    m._skipped_symbols.add("AAPL")
    z = _zustand(
        symbols_order=["AAPL", "TSLA"],
        snapshots={"AAPL": SimpleNamespace(latest_trade=object())},
    )
    assert _lauf(m._symbole_vorbereiten(z)) is Zyklus.WEITER
    assert [s["symbol"] for s in z.graph_states] == ["AAPL"]
    assert z.graph_states[0]["ohlc"] == _OHLC
    assert z.graph_states[0]["current_time"] == z.current_time_utc.isoformat()
    assert m._skipped_symbols == {"TSLA"}  # AAPL wieder gültig, TSLA ohne Snapshot


def test_vorbereiten_ohne_symbole_ist_kein_abbruch(flags):
    from core.engine.trading_loop import Zyklus

    z = _zustand(symbols_order=[], snapshots={})
    assert _lauf(_mixin()._symbole_vorbereiten(z)) is Zyklus.WEITER
    assert z.graph_states == []


def test_vorbereiten_gibt_stopp_bei_shutdown(flags, frischer_kurs):
    from core.engine.trading_loop import Zyklus

    m = _mixin()
    m._shutdown_event.set()
    z = _zustand(
        symbols_order=["AAPL"],
        snapshots={"AAPL": SimpleNamespace(latest_trade=object())},
    )
    assert _lauf(m._symbole_vorbereiten(z)) is Zyklus.STOPP
    assert z.graph_states == []


def test_bewerten_ohne_states_ruft_keinen_graphen(flags):
    bauen = MagicMock()
    z = _zustand()
    with patch("core.engine.trading_loop.build_symbol_eval_graph", new=bauen):
        assert _lauf(_mixin()._round_table_bewerten(z)) is None
    bauen.assert_not_called()
    assert z.results == []


def test_bewerten_fuellt_results_in_state_reihenfolge(flags):
    async def _ainvoke(state, **_kw):
        return {"symbol": state["symbol"], "signal": None}

    z = _zustand(graph_states=[{"symbol": "AAPL"}, {"symbol": "MSFT"}])
    with patch(
        "core.engine.trading_loop.build_symbol_eval_graph",
        return_value=SimpleNamespace(ainvoke=AsyncMock(side_effect=_ainvoke)),
    ):
        _lauf(_mixin()._round_table_bewerten(z))
    assert [r["symbol"] for r in z.results] == ["AAPL", "MSFT"]


def test_bewerten_enthaelt_sich_ohne_rat(flags):
    z = _zustand(graph_states=[{"symbol": "AAPL"}])
    with patch("core.engine.trading_loop.build_symbol_eval_graph", return_value=None):
        _lauf(_mixin()._round_table_bewerten(z))
    (r,) = z.results
    assert r["symbol"] == "AAPL"
    assert r["signal"].action == "HOLD"
    assert (
        r["signal"].decision_context.reasoning_summary == "abstain:council_unavailable"
    )


def test_ausfuehren_uebergibt_nur_signale_in_reihenfolge():
    m = _mixin()
    pm = MagicMock()
    m.active_strategy = SimpleNamespace(portfolio_manager=pm)
    a, b = _signal("AAPL"), _signal("NVDA", "SELL")
    z = _zustand(
        graph_states=[{"symbol": s} for s in ("AAPL", "MSFT", "TSLA", "NVDA")],
        results=[
            {"signal": a, "round_table_scores": {"x": 1}, "consensus_ranking": 0.7},
            asyncio.TimeoutError(),
            RuntimeError("kaputt"),
            b,
        ],
    )
    assert _lauf(m._signale_ausfuehren(z)) is None
    assert [c.args[0] for c in m._process_signal_event.await_args_list] == [a, b]
    assert m._last_round_table_state == {"x": 1}
    m._reconcile_specialist_coverage.assert_awaited_once()
    pm.set_live_consensus.assert_called_once_with("AAPL", 0.7)


# ── Dirigent ─────────────────────────────────────────────────────────────────


def _klasse():
    from tests.unit import _schleifen_quelle

    baum = ast.parse(_schleifen_quelle.PFAD.read_text(encoding="utf-8"))
    return next(
        k
        for k in baum.body
        if isinstance(k, ast.ClassDef) and k.name == "TradingLoopMixin"
    )


def _methoden():
    return {
        f.name: f
        for f in _klasse().body
        if isinstance(f, (ast.AsyncFunctionDef, ast.FunctionDef))
    }


def test_dirigent_ruft_die_drei_schritte_zwischen_kontext_und_auswertung():
    from tests.unit import _schleifen_quelle

    schritte = _schleifen_quelle.schritte()
    positionen = [schritte.index(n) for n in _SCHRITTE]
    assert positionen == sorted(positionen)
    assert schritte.index("_zyklus_kontext_aufbauen") < positionen[0]
    assert positionen[-1] < schritte.index("_zyklus_auswerten")


def test_live_trading_loop_ist_ein_dirigent_unter_der_schwelle():
    from tests.unit import _schleifen_quelle

    dirigent = _methoden()["live_trading_loop"]
    assert dirigent.end_lineno - dirigent.lineno + 1 < 150
    kopf = _schleifen_quelle.PFAD.read_text(encoding="utf-8").splitlines()[
        dirigent.lineno - 1
    ]
    assert "noqa: C901" not in kopf


def test_jeder_neue_schritt_liegt_unter_der_funktionsschwelle():
    laengen = {
        name: f.end_lineno - f.lineno + 1
        for name, f in _methoden().items()
        if name in _SCHRITTE or name.startswith(("_symbol_", "_round_table_"))
    }
    assert set(_SCHRITTE) <= set(laengen)
    assert all(n < 150 for n in laengen.values()), laengen
