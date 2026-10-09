"""Bewertung und Ausführung liegen in ``core/engine/zyklus_bewertung.py``, der Kern erbt sie.

#4252 (H-2k), Teil von ARC-E6 (#3738). Reiner Umzug der sieben Schritte von der
Symbolvorbereitung bis zur Zyklusgrenze aus ``core/engine/trading_loop.py`` nach
``ZyklusBewertungMixin`` (Schnitt-Entscheidung #4184 §2). Die Patch-Ziele der Tests und die
OTel-Objekte liegen weiter am Kern; das Modul liest sie zur Laufzeit als ``_tl.<name>``
(§3, §6). Plan: ``docs/4252-*/implementation_plan.md`` §6.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc3]

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
MODUL = PAKET / "core" / "engine" / "zyklus_bewertung.py"

METHODEN = {
    "_symbole_vorbereiten",
    "_symbol_zustand",
    "_round_table_bewerten",
    "_round_table_ausrollen",
    "_signale_ausfuehren",
    "_zyklus_auswerten",
    "_zyklusgrenze",
}
#: Am Kern gepatcht oder dort gehalten (Entscheidung §1, §3, §6) — das Modul bindet keinen davon.
KERN_NAMEN = {
    "get_config",
    "build_symbol_eval_graph",
    "_symbol_eval_timeout",
    "_extract_ohlc_from_snapshot",
    "engine_now",
    "CompositionRoot",
    "_obs_tracer",
    "_otel_trace",
    "_run_symbol_eval",
    "produce_news_headlines",
}
_OHLC = {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0}


# ── Ort ──────────────────────────────────────────────────────────────────────


def test_bewertung_liegt_in_zyklus_bewertung():
    from core.engine.trading_loop import TradingLoopMixin
    from core.engine.zyklus_bewertung import ZyklusBewertungMixin

    funktionen = {
        name
        for name, wert in vars(ZyklusBewertungMixin).items()
        if inspect.isfunction(wert)
    }
    assert funktionen == METHODEN
    assert issubclass(TradingLoopMixin, ZyklusBewertungMixin)
    assert not METHODEN & set(vars(TradingLoopMixin))
    for name in METHODEN:
        assert getattr(TradingLoopMixin, name) is vars(ZyklusBewertungMixin)[name]


def test_kein_modulimport_von_kern_namen():
    baum = ast.parse(MODUL.read_text(encoding="utf-8"))
    gebunden = set()
    for knoten in baum.body:
        if isinstance(knoten, (ast.Import, ast.ImportFrom)):
            gebunden |= {(a.asname or a.name).split(".")[0] for a in knoten.names}
        elif isinstance(knoten, ast.Assign):
            gebunden |= {t.id for t in knoten.targets if isinstance(t, ast.Name)}
    assert not gebunden & KERN_NAMEN
    letzte = baum.body[-1]
    assert isinstance(letzte, ast.ImportFrom)
    assert letzte.module == "core.engine"
    assert [(a.name, a.asname) for a in letzte.names] == [("trading_loop", "_tl")]


# ── Charakterisierung: vor dem Umzug grün, danach unverändert ────────────────


def _mixin():
    from core.engine.trading_loop import TradingLoopMixin

    m = TradingLoopMixin.__new__(TradingLoopMixin)
    m._shutdown_event = threading.Event()
    m.strategy_running = threading.Event()
    m.strategy_running.set()
    m._skipped_symbols = set()
    m._log_strategy_thought = MagicMock()
    m._hitl_symbol_pending = AsyncMock(return_value=False)
    m.current_market_data = {}
    m.active_strategy = None
    m.cloud_logger = MagicMock()
    m._cycle_latencies = []
    m._last_cycle_details = {}
    m._cycle_watchdog = None
    return m


def _zustand(**felder):
    from core.engine.zyklus import ZyklusZustand

    z = ZyklusZustand(current_time_utc=datetime.now(timezone.utc))
    for k, v in felder.items():
        setattr(z, k, v)
    return z


def _cfg(**werte):
    basis = {
        "FULL_UNIVERSE_TRADING_ENABLED": False,
        "FULL_UNIVERSE_EVAL_CONCURRENCY": 4,
        "MAX_QUOTE_AGE_SECONDS": 900.0,
        "SIM_MODE": False,
    }
    basis.update(werte)
    return SimpleNamespace(**basis)


def test_bewertung_folgt_kern_patches():
    # Review #4419 (FINDING-01, §12.5): AsyncMock statt lokaler async-Attrappen.
    # `wait_for` umhuellt das echte asyncio.wait_for - es wird weiter abgewartet.
    warte = AsyncMock(wraps=asyncio.wait_for)
    graph = SimpleNamespace(
        ainvoke=AsyncMock(
            side_effect=lambda state, **_kw: {"symbol": state["symbol"], "signal": None}
        )
    )
    bauen = MagicMock(return_value=graph)
    jetzt = datetime.now(timezone.utc)
    m = _mixin()
    z = _zustand(
        symbols_order=["AAPL"],
        snapshots={"AAPL": SimpleNamespace(latest_trade=object())},
    )
    with patch("core.engine.trading_loop.get_config", return_value=_cfg()), patch(
        "core.engine.trading_loop.build_symbol_eval_graph", new=bauen
    ), patch("core.engine.trading_loop._symbol_eval_timeout", return_value=7.0), patch(
        "core.engine.trading_loop._extract_ohlc_from_snapshot",
        return_value=(dict(_OHLC), 1.5, jetzt),
    ), patch(
        "core.engine.trading_loop.engine_now", return_value=jetzt
    ), patch(
        "asyncio.wait_for", new=warte
    ):
        asyncio.run(m._symbole_vorbereiten(z))
        asyncio.run(m._round_table_bewerten(z))
    assert [s["ohlc"] for s in z.graph_states] == [_OHLC]
    bauen.assert_called_once_with()
    assert [r["symbol"] for r in z.results] == ["AAPL"]
    aufruf = warte.call_args
    timeout = aufruf.kwargs.get(
        "timeout", aufruf.args[1] if len(aufruf.args) > 1 else None
    )
    assert timeout == 7.0


def test_zyklusgrenze_liest_kern_config():
    m = _mixin()
    m.agent_registry = None
    uhr = MagicMock()
    uhr.advance.return_value = False
    uhr.is_open = True
    with patch(
        "core.engine.trading_loop.get_config", return_value=_cfg(SIM_MODE=True)
    ), patch("core.sim.clock.get_sim_clock", return_value=uhr), patch(
        "asyncio.sleep", new=AsyncMock()
    ) as schlaf:
        asyncio.run(m._zyklusgrenze(_zustand()))
    assert m._sim_cycles == 1
    schlaf.assert_awaited_once_with(0)


def test_auswerten_nutzt_kern_composition_root():
    m = _mixin()
    z = _zustand(t_start=1.0, t_data_fetched=1.5, symbols_to_process=["AAPL"])
    wurzel = MagicMock()
    wurzel.get_instance.return_value.clock_port.time.return_value = 4252.0
    with patch("core.engine.trading_loop.CompositionRoot", new=wurzel), patch(
        "time.perf_counter", return_value=2.0
    ):
        asyncio.run(m._zyklus_auswerten(z))
    assert m._last_cycle_details["timestamp"] == 4252.0
    assert m._cycles_completed == 1


# ── OTel (Entscheidung §6): der Zyklus-Span am Code ──────────────────────────


class _Span:
    def __init__(self, name):
        self.name = name
        self.attribute = {}
        self.beendet = 0

    def set_attribute(self, schluessel, wert):
        self.attribute[schluessel] = wert

    def end(self):
        self.beendet += 1


class _Tracer:
    def __init__(self):
        self.spans = []

    def start_span(self, name):
        span = _Span(name)
        self.spans.append(span)
        return span


def test_round_table_ausrollen_oeffnet_trading_cycle_span():
    """Ein Span ``trading.cycle`` je Fan-out, Attribut ``cycle.symbol_count``, beendet; jedes
    Symbol läuft mit dessen Kontext als Parent (OBS-4 #2637)."""
    tracer = _Tracer()
    _eval = AsyncMock(
        side_effect=lambda _t, _ctx, symbol, _f: {"symbol": symbol, "signal": None}
    )
    otel = SimpleNamespace(set_span_in_context=lambda span: ("ctx", span))
    z = _zustand(graph_states=[{"symbol": "AAPL"}, {"symbol": "MSFT"}])
    with patch("core.engine.trading_loop.get_config", return_value=_cfg()), patch(
        "core.engine.trading_loop._obs_tracer", new=tracer
    ), patch("core.engine.trading_loop._otel_trace", new=otel), patch(
        "core.engine.trading_loop._run_symbol_eval", new=_eval
    ):
        asyncio.run(_mixin()._round_table_ausrollen(z, object()))
    (span,) = tracer.spans
    assert span.name == "trading.cycle"
    assert span.attribute == {"cycle.symbol_count": 2}
    assert span.beendet == 1
    assert [c.args[:3] for c in _eval.await_args_list] == [
        (tracer, ("ctx", span), "AAPL"),
        (tracer, ("ctx", span), "MSFT"),
    ]
    assert [r["symbol"] for r in z.results] == ["AAPL", "MSFT"]


def test_telemetrie_fallback_meldet_warning():
    """Review #4419 (FINDING-02, CLAUDE.md §5.6): Ein Fallback loggt WARNING, nie DEBUG.

    Der Span-Fehler stand wortgleich schon im Kern; mit dem Umzug wird er sichtbar."""
    quelle = (
        Path(__file__).resolve().parents[2] / "core" / "engine" / "zyklus_bewertung.py"
    ).read_text(encoding="utf-8")
    baum = ast.parse(quelle)
    debug_fallbacks = [
        knoten.lineno
        for knoten in ast.walk(baum)
        if isinstance(knoten, ast.Call)
        and getattr(knoten.func, "attr", "") == "debug"
        and any(
            isinstance(a, ast.Constant)
            and isinstance(a.value, str)
            and a.value.startswith("Telemetry span error")
            for a in knoten.args
        )
    ]
    assert (
        debug_fallbacks == []
    ), f"Telemetrie-Fallback auf DEBUG: Zeilen {debug_fallbacks}"
