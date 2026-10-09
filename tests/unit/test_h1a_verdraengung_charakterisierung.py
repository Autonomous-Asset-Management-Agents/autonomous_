"""#4230 (ARC-E6 H-1a) — Charakterisierung des Rueckkaufs nach Verdraengung vor dem Schnitt.

Plan: ``docs/4230-*/implementation_plan.md`` §6 Schritt 5. Netz fuer H-1l
(``verdraengung.py``): ``_recover_displacement``. Bisher fuhr ihn nur der Fachtest
``test_displacement_recovery.py``; ``test_3821_absendung_charakterisierung.py`` ersetzt ihn
durch ein ``AsyncMock`` und prueft nur den Aufruf.

Die Tests halten fest, was der Code **heute** tut, nicht was er tun sollte. Nach dem Umzug
muessen sie unveraendert gruen sein; jede Abweichung ist eine Verhaltensaenderung.

Beobachtet wird am Broker-Double (Storno, Abfrage, Order), am Compliance-Waechter, am
PortfolioManager-Double, am Redis-Double (``persist_pm_state_to_redis`` laeuft echt) und am
Kanal ``explainability:<user_id>``. Die Order geht **durch das echte Tor**
(``_sende_durchs_tor``), nicht an ihm vorbei: das Tor verschiebt H-1h, und ein Netz, das es
ersetzte, hinge an seinem Namen. Der Kill-Switch wird an seinem Singleton ersetzt,
``RedisClient.get_redis`` an der Klasse. Einziger Patch am Kern ist
``_record_gateway_decision`` (bleibt im Kern, Entscheidung #4183 §3), wie in G-1b.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from alpaca.common.exceptions import APIError

import core.engine.order_executor as oe
from core.exceptions import TradingHaltedError
from core.idempotency import derive_client_order_id
from core.kill_switch import kill_switch as _kill_switch
from core.redis_client import RedisClient

pytestmark = [pytest.mark.unit, pytest.mark.vc3]

_USER = "u-4230"
_KONTEXT = SimpleNamespace(decision_id="dec-4230")


def _broker(*, gefuellt=0.0, storno=None, abfrage=None):
    api = MagicMock()
    api.submit_order.side_effect = lambda req: SimpleNamespace(id="rueck-1")
    if storno is not None:
        api.cancel_order_by_id.side_effect = storno
    if abfrage is not None:
        api.get_order_by_id.side_effect = abfrage
    else:
        api.get_order_by_id.return_value = SimpleNamespace(filled_qty=gefuellt)
    return api


def _pm():
    pm = MagicMock()
    pm.user_id = _USER
    pm._trade_history = {}
    pm._consecutive_sell_signals = {"GATE": 2}
    return pm


def _rueckkauf(
    api,
    *,
    symbol="GATE",
    order_id="verkauf-1",
    menge=5.0,
    halt=None,
    pm="neu",
    kontext=_KONTEXT,
):
    """Ein Lauf durch ``_recover_displacement``; liefert die Beobachtung."""
    from core.engine.base import BotEngine

    engine = BotEngine.__new__(BotEngine)
    engine.compliance_guardian = MagicMock()
    engine.compliance_guardian.check_order.return_value = True
    pm = _pm() if pm == "neu" else pm
    speicher = MagicMock(set=AsyncMock())
    kanal = MagicMock(publish=AsyncMock())
    with ExitStack() as stack:
        check_halt = stack.enter_context(
            patch.object(oe.kill_switch, "check_halt", side_effect=halt)
        )
        stack.enter_context(
            patch.object(oe.kill_switch, "is_halted", return_value=False)
        )
        if _kill_switch is not oe.kill_switch:
            stack.enter_context(
                patch.object(_kill_switch, "check_halt", side_effect=halt)
            )
            stack.enter_context(
                patch.object(_kill_switch, "is_halted", return_value=False)
            )
        stack.enter_context(
            patch.object(RedisClient, "get_redis", AsyncMock(return_value=kanal))
        )
        stack.enter_context(
            patch.object(oe, "_record_gateway_decision", lambda d: None)
        )
        asyncio.run(
            engine._recover_displacement(
                _USER, api, pm, speicher, symbol, order_id, menge, context=kontext
            )
        )
    return {
        "storno": [c.args for c in api.cancel_order_by_id.call_args_list],
        "abfrage": [c.args for c in api.get_order_by_id.call_args_list],
        "orders": [
            (
                r.symbol,
                r.side.value,
                float(r.qty),
                r.time_in_force.value,
                r.client_order_id,
            )
            for r in (c.args[0] for c in api.submit_order.call_args_list)
        ],
        "halt_pruefungen": [c.args for c in check_halt.call_args_list],
        "waechter": [
            (o["symbol"], o["side"], o["quantity"], o["strategy_id"], o["user_id"])
            for o in (
                c.args[0] for c in engine.compliance_guardian.check_order.call_args_list
            )
        ],
        "pm": (
            [c.args for c in pm.record_trade.call_args_list] if pm is not None else None
        ),
        "persistiert": [c.args[:2] for c in speicher.set.await_args_list],
        "kanal": [
            (c.args[0], json.loads(c.args[1])) for c in kanal.publish.await_args_list
        ],
    }


def _api_fehler():
    http = MagicMock()
    http.response.status_code = 422
    return APIError(
        error=json.dumps({"code": 42210000, "message": "gefuellt"}), http_error=http
    )


_COID = derive_client_order_id("dec-4230", "displacement", 1)


# ── V1 kein Symbol ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("symbol", ["", None])
def test_v1_ohne_symbol_keine_broker_handlung(symbol):
    api = _broker(gefuellt=5.0)
    beob = _rueckkauf(api, symbol=symbol)
    assert api.method_calls == []
    assert beob["halt_pruefungen"] == [] and beob["pm"] == [] and beob["kanal"] == []


# ── V2 nichts gefuellt ────────────────────────────────────────────────────────


def test_v2_ohne_fuellung_kein_rueckkauf():
    beob = _rueckkauf(_broker(gefuellt=0))
    assert beob["storno"] == [("verkauf-1",)]
    assert beob["abfrage"] == [("verkauf-1",)]
    assert beob["orders"] == [] and beob["halt_pruefungen"] == []
    assert beob["pm"] == [] and beob["persistiert"] == []


def test_v2_ohne_verkaufsorder_gilt_die_uebergebene_menge():
    beob = _rueckkauf(_broker(), order_id=None, menge=3.0)
    assert beob["storno"] == [] and beob["abfrage"] == []
    assert beob["orders"] == [("GATE", "buy", 3.0, "day", _COID)]


def test_v2_ohne_verkaufsorder_und_ohne_menge_kein_rueckkauf():
    beob = _rueckkauf(_broker(), order_id=None, menge=0.0)
    assert beob["orders"] == [] and beob["halt_pruefungen"] == []


# ── V3 Teilfuellung ───────────────────────────────────────────────────────────


def test_v3_teilfuellung_kauft_genau_die_gefuellte_menge_zurueck():
    beob = _rueckkauf(_broker(gefuellt="2.5"), menge=5.0)

    assert beob["storno"] == [("verkauf-1",)]
    assert beob["halt_pruefungen"] == [(_USER,)]
    assert beob["orders"] == [("GATE", "buy", 2.5, "day", _COID)]
    assert beob["waechter"] == [
        ("GATE", "buy", 2.5, "displacement_recovery_buy", _USER)
    ]
    assert beob["pm"] == [("GATE", "buy")]
    assert beob["persistiert"] == [
        (f"pm:trade_history:{_USER}:GATE", "[]"),
        (f"pm:sell_signals:{_USER}:GATE", "2"),
    ]
    assert beob["kanal"] == []


def test_v3_ohne_pm_kein_nachtrag():
    beob = _rueckkauf(_broker(gefuellt=1.0), pm=None)
    assert beob["orders"] == [("GATE", "buy", 1.0, "day", _COID)]
    assert beob["persistiert"] == [] and beob["kanal"] == []


def test_v3_ohne_kontext_ein_zufallsschluessel(caplog):
    with caplog.at_level(logging.WARNING):
        beob = _rueckkauf(_broker(gefuellt=1.0), kontext=None)
    ((_, _, _, _, coid),) = beob["orders"]
    assert coid != _COID and len(coid) == 36
    assert any("kein Entscheidungskontext" in r.getMessage() for r in caplog.records)


# ── V4 Fehler bei Storno und Abfrage ──────────────────────────────────────────


def test_v4_storno_api_fehler_wird_geschluckt():
    beob = _rueckkauf(_broker(gefuellt=4.0, storno=_api_fehler()))
    assert beob["abfrage"] == [("verkauf-1",)]
    assert beob["orders"] == [("GATE", "buy", 4.0, "day", _COID)]


def test_v4_abfragefehler_faellt_auf_die_uebergebene_menge_zurueck(caplog):
    with caplog.at_level(logging.WARNING):
        beob = _rueckkauf(_broker(abfrage=RuntimeError("weg")), menge=5.0)
    assert beob["orders"] == [("GATE", "buy", 5.0, "day", _COID)]
    assert any(
        "Falling back to original qty: 5.0" in r.getMessage() for r in caplog.records
    )


def test_v4_anderer_stornofehler_ueberspringt_die_abfrage():
    beob = _rueckkauf(_broker(gefuellt=1.0, storno=RuntimeError("weg")), menge=5.0)
    assert beob["abfrage"] == []
    assert beob["orders"] == [("GATE", "buy", 5.0, "day", _COID)]


def test_v4_leere_abfrage_behaelt_die_uebergebene_menge():
    beob = _rueckkauf(_broker(abfrage=lambda oid: None), menge=5.0)
    assert beob["orders"] == [("GATE", "buy", 5.0, "day", _COID)]


# ── V5 Kill-Switch ────────────────────────────────────────────────────────────


def test_v5_ausgeloester_kill_switch_keine_order_und_meldung(caplog):
    halt = TradingHaltedError("TRADING HALTED: Kill-Switch")
    with caplog.at_level(logging.CRITICAL):
        beob = _rueckkauf(_broker(gefuellt=2.0), halt=halt)

    assert beob["orders"] == [] and beob["waechter"] == []
    assert beob["pm"] == [] and beob["persistiert"] == []
    ((kanal, nachricht),) = beob["kanal"]
    assert kanal == f"explainability:{_USER}"
    assert {k: nachricht[k] for k in ("type", "severity", "title")} == {
        "type": "state_inconsistency",
        "severity": "CRITICAL",
        "title": "Recovery Failure: GATE",
    }
    assert nachricht["message"] == (
        "Failed to submit recovery re-BUY for GATE after failed swap: "
        "TRADING HALTED: Kill-Switch"
    )
    assert "timestamp" in nachricht
    assert any(
        r.levelno == logging.CRITICAL
        and "Could not re-buy 2.0 shares of GATE" in r.getMessage()
        for r in caplog.records
    )


def test_v5_brokerfehler_beim_rueckkauf_meldet_ebenso():
    api = _broker(gefuellt=2.0)
    api.submit_order.side_effect = RuntimeError("broker weg")
    beob = _rueckkauf(api)
    assert len(beob["orders"]) == 1
    assert beob["pm"] == []
    ((kanal, nachricht),) = beob["kanal"]
    assert (
        kanal == f"explainability:{_USER}"
        and nachricht["type"] == "state_inconsistency"
    )
