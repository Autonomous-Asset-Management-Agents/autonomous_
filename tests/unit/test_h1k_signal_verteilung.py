"""#4240 (H-1k) — Verteilung, Markt-Tor und Protokollpflicht wohnen in ``signal_uebergabe.py``.

Plan: ``docs/4240-*/implementation_plan.md`` §2/§4/§6. Entscheidung:
``docs/3738-arc-e6-gestalt/H1_SCHNITT_order_executor.md``, Abschnitt H-1k.

Vier Blöcke von ``_process_signal_event`` ziehen als Schritte nach ``SignalUebergabeMixin``:
Protokollpflicht (HOLD-Tabelle), Markt-Tor mit Abbruch-Ergebnis (Muster G-1b), die
Mandanten-Verteilung und der Rumpf des äußeren Fehlerfängers. Dazu zwei Abbildungen ohne
Präfix ``_schritt_``: die Anlage des Zustands ``_Uebergabe`` und die Schlüsselwörter des
``_schritt_absenden``-Aufrufs. Danach liegt der Dirigent unter 150 Zeilen (Epic §8.2).
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.architecture import regeln

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
MODUL = PAKET / "core" / "engine" / "signal_uebergabe.py"
#: Epic #3738 §8.2: höchstens 150 Zeilen je Funktion. Der Vertrag steht noch auf Stufe 2
#: (``[groessen].funktion_schwelle``); das Ziel dieses Umzugs ist die Epic-Zahl.
EPIC_FUNKTION_SCHWELLE = 150
SCHRITTE = (
    "_schritt_protokollpflicht",
    "_schritt_markt_offen",
    "_schritt_verteilung",
    "_schritt_uebergabe_fehler",
)
HILFEN = ("_uebergabe_anlegen", "_absende_argumente")


# ── Heimat, Aufbau, Größen ────────────────────────────────────────────────────


def test_schritte_und_hilfen_wohnen_im_uebergabe_modul():
    from core.engine.order_executor import OrderExecutorMixin
    from core.engine.signal_uebergabe import SignalUebergabeMixin

    for name in SCHRITTE + HILFEN:
        assert name in SignalUebergabeMixin.__dict__, name
        assert name not in OrderExecutorMixin.__dict__, name


def _gemessen():
    return {
        b.was: int(b.zusatz)
        for b in regeln.funktions_groessen(PAKET, "core/engine")
        if b.datei
        in ("core/engine/order_executor.py", "core/engine/signal_uebergabe.py")
    }


def test_dirigent_liegt_unter_der_funktionsschwelle():
    n = _gemessen()["OrderExecutorMixin._process_signal_event"]
    schwelle = regeln.lade_vertrag()["groessen"]["funktion_schwelle"]
    assert n <= min(EPIC_FUNKTION_SCHWELLE, schwelle), f"Dirigent: {n} Zeilen"


def test_jeder_neue_schritt_liegt_unter_der_funktionsschwelle():
    gemessen = _gemessen()
    for name in SCHRITTE + HILFEN:
        n = gemessen[f"SignalUebergabeMixin.{name}"]
        assert n <= EPIC_FUNKTION_SCHWELLE, f"{name}: {n} Zeilen"


def test_kein_neuer_schritt_ruft_einen_schritt():
    """Option A: der Dirigent ruft jeden Schritt direkt (``_uebergabe_quelle`` liest flach)."""
    from core.engine.signal_uebergabe import SignalUebergabeMixin

    for name in SCHRITTE + HILFEN:
        baum = ast.parse(
            textwrap.dedent(inspect.getsource(vars(SignalUebergabeMixin)[name]))
        )
        gerufen = [
            n.func.attr
            for n in ast.walk(baum)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr.startswith("_schritt_")
        ]
        assert not gerufen, f"{name} ruft {gerufen}"


def test_jeder_kern_name_existiert():
    """Ein falsch geschriebener ``order_executor.<name>`` fiele erst zur Laufzeit auf."""
    from core.engine import order_executor

    baum = ast.parse(MODUL.read_text(encoding="utf-8"))
    namen = {
        n.attr
        for n in ast.walk(baum)
        if isinstance(n, ast.Attribute)
        and isinstance(n.value, ast.Name)
        and n.value.id == "order_executor"
    }
    fehlend = sorted(n for n in namen if n not in vars(order_executor))
    assert not fehlend, fehlend


# ── Hilfen ────────────────────────────────────────────────────────────────────


def _engine(**felder):
    from core.engine.base import BotEngine

    engine = BotEngine.__new__(BotEngine)
    engine.cloud_logger = MagicMock()
    for k, v in felder.items():
        setattr(engine, k, v)
    return engine


def _lauf(coro):
    return asyncio.run(coro)


def test_absende_argumente_decken_die_signatur():
    from core.engine.signal_uebergabe import SignalUebergabeMixin

    parameter = [
        p
        for p in inspect.signature(SignalUebergabeMixin._schritt_absenden).parameters
        if p != "self"
    ]
    st = SimpleNamespace(**{k: object() for k in _ABBILDUNG.values()})
    assert sorted(SignalUebergabeMixin._absende_argumente(st)) == sorted(parameter)


#: Schlüsselwort von ``_schritt_absenden`` → Feld in ``_Uebergabe`` (Stand vor H-1k).
_ABBILDUNG = {
    "symbol": "symbol",
    "action": "action",
    "context": "context",
    "qty": "qty",
    "req": "req",
    "side_enum": "side_enum",
    "trade_client": "trade_client",
    "uid": "uid",
    "_schutz_exit": "schutz_exit",
    "_fb_pm": "fb_pm",
    "_fb_redis": "fb_redis",
    "_pc_pm": "pc_pm",
    "_pc_symbol_to_close": "pc_symbol_to_close",
    "_fallback_displacement_sell_order_id": "fallback_displacement_sell_order_id",
    "_fallback_displacement_sell_qty": "fallback_displacement_sell_qty",
    "_fb_deferred_close_symbol": "fb_deferred_close_symbol",
    "_fb_deferred_close_qty": "fb_deferred_close_qty",
}


def test_absende_argumente_lesen_jedes_feld_aus_dem_zustand():
    from core.engine.signal_uebergabe import SignalUebergabeMixin

    werte = {feld: object() for feld in _ABBILDUNG.values()}
    argumente = SignalUebergabeMixin._absende_argumente(SimpleNamespace(**werte))
    for schluessel, feld in _ABBILDUNG.items():
        assert argumente[schluessel] is werte[feld], schluessel


def test_uebergabe_anlegen_traegt_kopf_und_anfangswerte():
    from core.engine.order_executor import _Uebergabe

    ctx = SimpleNamespace()
    event = SimpleNamespace(
        symbol="AAPL", action="BUY", suggested_quantity=3.0, decision_context=ctx
    )
    st = _engine(active_uid="uid-4240")._uebergabe_anlegen(event, True, 150.0)

    assert isinstance(st, _Uebergabe)
    assert (st.event, st.symbol, st.action, st.context) == (event, "AAPL", "BUY", ctx)
    assert (st.should_log, st.qty, st.curr, st.uid) == (True, 3.0, 150.0, "uid-4240")
    assert st.held_broker_qty == 0.0  # #2553, vormals im Dirigenten
    assert st.sizing_trace == {}  # #2811, vormals im Dirigenten
    assert st.fallback_displacement_sell_order_id is None
    assert st.fallback_displacement_sell_qty == 0.0
    assert st.fb_deferred_close_symbol is None
    assert st.fb_deferred_close_qty == 0.0


def test_uebergabe_anlegen_ohne_aktiven_nutzer():
    event = SimpleNamespace(
        symbol="AAPL", action="SELL", suggested_quantity=0.0, decision_context=None
    )
    assert _engine()._uebergabe_anlegen(event, False, 0.0).uid is None


# ── Protokollpflicht ──────────────────────────────────────────────────────────


def _kontext(**felder):
    werte = {
        "current_price": 150.0,
        "lstm_prediction": 0.0,
        "rl_stabilized_action": 1,
        "risk_approved": True,
    }
    werte.update(felder)
    return SimpleNamespace(**werte)


@pytest.mark.parametrize(
    "action, felder, erwartet",
    [
        ("BUY", {}, True),
        ("SELL", {}, True),
        ("HOLD", {"lstm_prediction": 0.7, "rl_stabilized_action": 0}, True),
        ("HOLD", {"lstm_prediction": -0.7, "rl_stabilized_action": 0}, True),
        ("HOLD", {"lstm_prediction": 0.7}, False),
        ("HOLD", {"risk_approved": False}, True),
        ("HOLD", {"portfolio_approved": False}, True),
        ("HOLD", {"intelligence_approved": False}, True),
        ("HOLD", {}, False),
    ],
)
def test_protokollpflicht(action, felder, erwartet):
    assert _engine()._schritt_protokollpflicht(action, _kontext(**felder)) is erwartet


# ── Markt-Tor ─────────────────────────────────────────────────────────────────


def _event(action="SELL", **felder):
    werte = {
        "symbol": "AAPL",
        "action": action,
        "suggested_quantity": 2.0,
        "decision_context": _kontext(),
        "is_simulation": False,
    }
    werte.update(felder)
    return SimpleNamespace(**werte)


def test_markt_zu_protokolliert_und_bricht_ab():
    engine = _engine(_market_closed_blocks_order=AsyncMock(return_value=True))
    event = _event()
    with patch("core.engine.order_executor._capture_outcome") as capture:
        assert _lauf(engine._schritt_markt_offen(event, True)) is False
    engine._market_closed_blocks_order.assert_awaited_once_with("AAPL", "SELL")
    engine.cloud_logger.log_decision.assert_called_once_with(event.decision_context)
    capture.assert_called_once_with(event.decision_context)


def test_markt_zu_ohne_protokollpflicht_bricht_still_ab():
    engine = _engine(_market_closed_blocks_order=AsyncMock(return_value=True))
    with patch("core.engine.order_executor._capture_outcome") as capture:
        assert _lauf(engine._schritt_markt_offen(_event(), False)) is False
    engine.cloud_logger.log_decision.assert_not_called()
    capture.assert_not_called()


def test_markt_offen_liefert_true():
    engine = _engine(_market_closed_blocks_order=AsyncMock(return_value=False))
    with patch("core.engine.order_executor._capture_outcome") as capture:
        assert _lauf(engine._schritt_markt_offen(_event(), True)) is True
    engine.cloud_logger.log_decision.assert_not_called()
    capture.assert_not_called()


def test_dirigent_markt_zu_sendet_nichts_und_protokolliert_einmal():
    """Gherkin „Das Markt-Tor sperrt und protokolliert wie bisher" — am Dirigenten."""
    engine = _engine(
        _market_closed_blocks_order=AsyncMock(return_value=True),
        get_active_tenant_clients=AsyncMock(return_value=[]),
        _execute_tenant_order=AsyncMock(),
        api=MagicMock(),
    )
    event = _event()
    with patch("core.engine.order_executor._capture_outcome") as capture:
        _lauf(engine._process_signal_event(event))
    engine.get_active_tenant_clients.assert_not_awaited()
    engine._execute_tenant_order.assert_not_awaited()
    engine.api.submit_order.assert_not_called()
    engine.cloud_logger.log_decision.assert_called_once_with(event.decision_context)
    capture.assert_called_once_with(event.decision_context)


# ── Verteilung ────────────────────────────────────────────────────────────────


_MANDANTEN = [{"user_id": "u-1"}, {"user_id": "u-2"}]


def test_verteilung_ruft_jeden_mandanten():
    engine = _engine(_execute_tenant_order=AsyncMock(return_value=True))
    event = _event(action="BUY")
    with patch(
        "core.engine.order_executor._audit_skipped_signal", new=AsyncMock()
    ) as audit:
        _lauf(engine._schritt_verteilung(event, _MANDANTEN))
    assert [c.args for c in engine._execute_tenant_order.await_args_list] == [
        (_MANDANTEN[0], event),
        (_MANDANTEN[1], event),
    ]
    audit.assert_not_awaited()


def test_verteilung_auditiert_jede_mandanten_ausnahme():
    from core.engine.order_executor import SKIP_REASON_EXECUTION_ERROR

    engine = _engine(
        _execute_tenant_order=AsyncMock(side_effect=[True, RuntimeError("boom")])
    )
    with patch(
        "core.engine.order_executor._audit_skipped_signal", new=AsyncMock()
    ) as audit:
        _lauf(engine._schritt_verteilung(_event(action="BUY"), _MANDANTEN))
    audit.assert_awaited_once_with(
        "AAPL", "BUY", SKIP_REASON_EXECUTION_ERROR, "tenant u-2: boom"
    )


def test_verteilung_setzt_multi_tenant_batch():
    engine = _engine(_execute_tenant_order=AsyncMock(return_value=None))
    event = _event(action="BUY")
    with patch("core.engine.order_executor._audit_skipped_signal", new=AsyncMock()):
        _lauf(engine._schritt_verteilung(event, _MANDANTEN))
    assert event.decision_context.alpaca_order_id == "multi-tenant-batch"


def test_verteilung_patch_am_kern_trifft():
    """Weg b (Entscheidung §3): ein Patch am Kern trifft den umgezogenen Block."""
    ersatz = AsyncMock()
    engine = _engine(_execute_tenant_order=AsyncMock(side_effect=RuntimeError("x")))
    with patch("core.engine.order_executor._audit_skipped_signal", new=ersatz):
        _lauf(engine._schritt_verteilung(_event(action="BUY"), _MANDANTEN[:1]))
    ersatz.assert_awaited_once()


def test_dirigent_verteilt_an_beide_mandanten_und_auditiert_den_fehler():
    """Gherkin „Die Verteilung ruft jeden Mandanten und auditiert jede Ausnahme"."""
    from core.engine.order_executor import SKIP_REASON_EXECUTION_ERROR

    engine = _engine(
        _market_closed_blocks_order=AsyncMock(return_value=False),
        get_active_tenant_clients=AsyncMock(return_value=_MANDANTEN),
        _execute_tenant_order=AsyncMock(side_effect=[True, RuntimeError("boom")]),
    )
    event = _event(action="BUY")
    with (
        patch(
            "core.engine.order_executor._audit_skipped_signal", new=AsyncMock()
        ) as audit,
        patch("core.engine.order_executor._capture_outcome"),
    ):
        _lauf(engine._process_signal_event(event))
    assert engine._execute_tenant_order.await_count == 2
    audit.assert_awaited_once_with(
        "AAPL", "BUY", SKIP_REASON_EXECUTION_ERROR, "tenant u-2: boom"
    )
    assert event.decision_context.alpaca_order_id == "multi-tenant-batch"


# ── Äußerer Fehlerfänger ──────────────────────────────────────────────────────


def test_uebergabe_fehler_auditiert_und_setzt_order_id():
    from core.engine.order_executor import SKIP_REASON_EXECUTION_ERROR

    fehler = RuntimeError("x" * 80)
    ctx = _kontext()
    with patch(
        "core.engine.order_executor._audit_skipped_signal", new=AsyncMock()
    ) as audit:
        _lauf(_engine()._schritt_uebergabe_fehler("AAPL", "BUY", ctx, fehler))
    assert ctx.alpaca_order_id == f"failed: {'x' * 50}"
    audit.assert_awaited_once_with("AAPL", "BUY", SKIP_REASON_EXECUTION_ERROR, "x" * 80)


def test_dirigent_faengt_einen_fehler_der_mandantenliste():
    engine = _engine(
        _market_closed_blocks_order=AsyncMock(return_value=False),
        get_active_tenant_clients=AsyncMock(side_effect=RuntimeError("db weg")),
    )
    event = _event(action="BUY")
    with (
        patch(
            "core.engine.order_executor._audit_skipped_signal", new=AsyncMock()
        ) as audit,
        patch("core.engine.order_executor._capture_outcome") as capture,
    ):
        _lauf(engine._process_signal_event(event))
    assert event.decision_context.alpaca_order_id == "failed: db weg"
    audit.assert_awaited_once()
    capture.assert_called_once_with(event.decision_context)
