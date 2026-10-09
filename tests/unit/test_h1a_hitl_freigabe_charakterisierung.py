"""#4230 (ARC-E6 H-1a) — Charakterisierung der HITL-Freigabe und des Markt-Tors vor dem Schnitt.

Plan: ``docs/4230-*/implementation_plan.md`` §6 Schritt 4. Netz fuer H-1e
(``hitl_freigabe.py``): ``execute_approved_order`` und ``_market_closed_blocks_order``.
Bisher gab es dafuer nur Fachtests, die den Mandanten-Zugang am heutigen Ort patchen.

Die Tests halten fest, was der Code **heute** tut, nicht was er tun sollte. Nach dem Umzug
muessen sie unveraendert gruen sein; jede Abweichung ist eine Verhaltensaenderung.

Beobachtet wird an ``core.hitl_gate.log_execution_event`` (Art-14-Kette), am
Ergebnis-Recorder und am Broker-Double. ``_execute_tenant_order``, ``_broker_zugang_fuer`` und
``get_active_tenant_clients`` werden auf der **Instanz** ersetzt. Einziger Patch am Kern ist
``core.engine.order_executor.create_trading_client`` fuer den Materialitaets-Riegel: Den Namen
liest das spaetere Modul nach der Zugriffsregel (Entscheidung #4183 §3, Weg b) weiter als
``order_executor.create_trading_client``.

Eine **Beobachtung** steht in ``test_h5_riegel_ohne_sim_wird_still_uebersprungen``: Ausserhalb
von ``SIM_MODE`` erreicht der Riegel nie seine Pruefung. Das ist Ist, kein Soll; eine Korrektur
gehoert in ein eigenes Issue (Epic §5), nicht in den Umbau.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import config as _config
from core.events import SignalEvent

pytestmark = [pytest.mark.unit, pytest.mark.vc3]

_TENANT = {"user_id": "u1", "client": "tenant-client", "equity": 100_000.0}


def _engine(*, zugang=(_TENANT, None), gesendet=True, api=None):
    from core.engine.base import BotEngine

    engine = BotEngine.__new__(BotEngine)
    engine.api = api
    engine.compliance_guardian = None
    engine.live_universe = []
    engine._broker_zugang_fuer = AsyncMock(return_value=zugang)
    engine._execute_tenant_order = AsyncMock(return_value=gesendet)
    engine.get_active_tenant_clients = AsyncMock(return_value=[_TENANT])
    return engine


def _payload(**felder):
    p = {
        "approval_id": "appr-4230",
        "user_id": "u1",
        "symbol": "AAPL",
        "action": "BUY",
        "qty": 3.0,
        "price": 100.0,
        "conviction": 0.7,
    }
    p.update(felder)
    return p


def _freigabe(engine, payload, *, material=0.0, sim=False, extra=()):
    """Ein Lauf durch ``execute_approved_order``; liefert Rueckgabe, Kette und Reihenfolge."""
    reihenfolge: list[str] = []
    kette = AsyncMock(side_effect=lambda ev: reihenfolge.append(f"kette:{ev.branch}"))
    engine._execute_tenant_order.side_effect = lambda *a, **k: (
        reihenfolge.append("absendung") or engine._execute_tenant_order.return_value
    )
    cfg = _config.get_config()
    with ExitStack() as stack:
        stack.enter_context(patch("core.hitl_gate.log_execution_event", kette))
        stack.enter_context(patch("core.hitl_gate.policy_snapshot", return_value={}))
        stack.enter_context(patch("core.hitl_gate.policy_hash", return_value="pol"))
        stack.enter_context(
            patch.object(cfg, "MATERIAL_ENTRY_MIN_PCT_OF_TARGET", material, create=True)
        )
        stack.enter_context(patch.object(cfg, "SIM_MODE", sim, create=True))
        for p in extra:
            stack.enter_context(p)
        rueckgabe = asyncio.run(engine.execute_approved_order(payload))
    ereignisse = [c.args[0] for c in kette.await_args_list]
    return rueckgabe, ereignisse, reihenfolge


# ── H1 Ablauf ─────────────────────────────────────────────────────────────────


def test_h1_audit_vor_absendung_und_rueckgabe_wahr():
    engine = _engine()
    rueckgabe, ereignisse, reihenfolge = _freigabe(engine, _payload())

    assert rueckgabe is True
    assert reihenfolge == ["kette:approved", "absendung"]
    (ev,) = ereignisse
    assert (ev.symbol, ev.action, ev.branch, ev.reason) == (
        "AAPL",
        "BUY",
        "approved",
        None,
    )
    assert (ev.policy_hash, ev.order_value, ev.approval_id) == (
        "pol",
        300.0,
        "appr-4230",
    )
    engine._broker_zugang_fuer.assert_awaited_once_with("u1")

    (tenant, event), kwargs = engine._execute_tenant_order.call_args
    assert tenant is _TENANT and kwargs == {"source": "human_approved"}
    assert isinstance(event, SignalEvent)
    assert (
        event.symbol,
        event.action,
        event.suggested_quantity,
        event.is_simulation,
    ) == (
        "AAPL",
        "BUY",
        3.0,
        False,
    )
    ctx = event.decision_context
    assert ctx.client_order_id == "hitl-appr-4230"
    assert (ctx.current_price, ctx.conviction_score) == (100.0, 0.7)
    assert (ctx.risk_approved, ctx.portfolio_approved, ctx.intelligence_approved) == (
        True,
        True,
        True,
    )


def test_h1_client_order_id_wird_auf_128_gekappt():
    engine = _engine()
    _freigabe(engine, _payload(approval_id="x" * 200))
    coid = engine._execute_tenant_order.call_args.args[
        1
    ].decision_context.client_order_id
    assert coid == ("hitl-" + "x" * 200)[:128] and len(coid) == 128


def test_h1_ohne_approval_id_bleibt_der_vorgabeschluessel():
    engine = _engine()
    _freigabe(engine, _payload(approval_id="  "))
    coid = engine._execute_tenant_order.call_args.args[
        1
    ].decision_context.client_order_id
    assert not coid.startswith("hitl-")


def test_h1_quelle_wird_durchgereicht():
    engine = _engine()
    with patch("core.hitl_gate.log_execution_event", AsyncMock()), patch(
        "core.hitl_gate.policy_snapshot", return_value={}
    ), patch("core.hitl_gate.policy_hash", return_value="pol"):
        asyncio.run(engine.execute_approved_order(_payload(), source="drain-x"))
    assert engine._execute_tenant_order.call_args.kwargs == {"source": "drain-x"}


# ── H2 fehlerhafte Nutzlast ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "felder",
    [{"price": 0.0}, {"price": None}, {"qty": 0.0}, {"qty": -1.0}],
    ids=["preis_null", "preis_none", "kauf_menge_null", "kauf_menge_negativ"],
)
def test_h2_fehlerhafte_nutzlast_wird_abgelehnt(felder):
    engine = _engine()
    rueckgabe, ereignisse, reihenfolge = _freigabe(engine, _payload(**felder))
    assert rueckgabe is False
    assert [(e.branch, e.reason) for e in ereignisse] == [
        ("rejected", "malformed_payload")
    ]
    assert reihenfolge == ["kette:rejected"]
    engine._broker_zugang_fuer.assert_not_awaited()


def test_h2_verkauf_ohne_menge_ist_nicht_fehlerhaft():
    engine = _engine()
    rueckgabe, ereignisse, _ = _freigabe(engine, _payload(action="SELL", qty=0.0))
    assert rueckgabe is True
    assert [e.branch for e in ereignisse] == ["approved"]
    assert ereignisse[0].order_value == 0.0


# ── H3 ohne Zugang ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("grund", ["no_oauth_tenant", "no_broker_access"])
def test_h3_ohne_zugang_wird_mit_dem_grund_abgelehnt(grund, caplog):
    engine = _engine(zugang=(None, grund))
    with caplog.at_level(logging.WARNING):
        rueckgabe, ereignisse, reihenfolge = _freigabe(engine, _payload())
    assert rueckgabe is False
    assert [(e.branch, e.reason) for e in ereignisse] == [("rejected", grund)]
    assert reihenfolge == ["kette:rejected"]
    assert any(
        r.levelno == logging.WARNING
        and "[HITL] approved order BUY AAPL" in r.getMessage()
        for r in caplog.records
    )


# ── H4 Absendung blockiert ────────────────────────────────────────────────────


@pytest.mark.parametrize("ergebnis", [False, None])
def test_h4_blockierte_absendung_wird_auditiert(ergebnis):
    engine = _engine(gesendet=ergebnis)
    rueckgabe, ereignisse, reihenfolge = _freigabe(engine, _payload())
    assert rueckgabe is False
    assert [(e.branch, e.reason) for e in ereignisse] == [
        ("approved", None),
        ("iron_dome_rejected", "execution_blocked"),
    ]
    assert reihenfolge == ["kette:approved", "absendung", "kette:iron_dome_rejected"]


# ── H5 Materialitaets-Riegel ──────────────────────────────────────────────────


def _fabrik(equity):
    client = MagicMock()
    client.get_account.return_value = SimpleNamespace(equity=equity)
    return patch(
        "core.engine.order_executor.create_trading_client",
        MagicMock(return_value=client),
    )


def test_h5_riegel_haelt_eine_unwesentliche_order_an():
    engine = _engine()
    fabrik = _fabrik("1000000")
    rueckgabe, ereignisse, reihenfolge = _freigabe(
        engine, _payload(), material=0.5, extra=(fabrik,)
    )
    assert rueckgabe is False
    (ev,) = ereignisse
    assert ev.branch == "skipped:immaterial_entry"
    assert ev.reason == "Order size  is < 50.0% of target allocation "
    assert reihenfolge == ["kette:skipped:immaterial_entry"]
    engine._broker_zugang_fuer.assert_not_awaited()


def test_h5_riegel_ruft_die_fabrik_mit_dem_mandanten_dict():
    engine = _engine()
    with _fabrik("1000000") as fabrik:
        _freigabe(engine, _payload(), material=0.5)
    assert fabrik.call_args.args == (_TENANT,) and fabrik.call_args.kwargs == {}


def test_h5_riegel_laesst_eine_wesentliche_order_durch():
    engine = _engine()
    rueckgabe, ereignisse, _ = _freigabe(
        engine, _payload(), material=0.5, extra=(_fabrik("1000"),)
    )
    assert rueckgabe is True and [e.branch for e in ereignisse] == ["approved"]


@pytest.mark.parametrize(
    "felder",
    [{"action": "SELL"}, {"user_id": "fremd"}],
    ids=["verkauf", "kein_passender_mandant"],
)
def test_h5_riegel_greift_nur_beim_kauf_eines_bekannten_mandanten(felder):
    engine = _engine()
    with _fabrik("1000000") as fabrik:
        rueckgabe, ereignisse, _ = _freigabe(engine, _payload(**felder), material=0.5)
    assert rueckgabe is True and [e.branch for e in ereignisse] == ["approved"]
    fabrik.assert_not_called()


def test_h5_riegel_aus_bei_null_prozent():
    engine = _engine()
    with _fabrik("1000000") as fabrik:
        rueckgabe, _, _ = _freigabe(engine, _payload(), material=0.0)
    assert rueckgabe is True
    fabrik.assert_not_called()
    engine.get_active_tenant_clients.assert_not_awaited()


def test_h5_riegel_ohne_sim_wird_still_uebersprungen():
    """Beobachtung, nicht Soll (Plan §7).

    Der Riegel ruft ``create_trading_client(tenant)`` mit dem Mandanten-**Dict** als
    Positionsargument. Ausserhalb von ``SIM_MODE`` baut die Fabrik daraus einen
    ``TradingClient(api_key=<dict>)``; der wirft ``ValueError`` (kein ``secret_key``), das
    ``except Exception: pass`` schluckt ihn, und die Order geht ohne Materialitaets-Pruefung
    weiter. Selbst eine winzige Order gegen ein riesiges Konto wird ausgefuehrt.
    """
    engine = _engine()
    engine.get_active_tenant_clients = AsyncMock(
        return_value=[{**_TENANT, "equity": 10_000_000.0}]
    )
    rueckgabe, ereignisse, reihenfolge = _freigabe(
        engine, _payload(qty=0.01), material=0.5, sim=False
    )
    assert rueckgabe is True
    assert [e.branch for e in ereignisse] == ["approved"]
    assert reihenfolge == ["kette:approved", "absendung"]
    engine.get_active_tenant_clients.assert_awaited_once()


# ── H6 Markt-Tor ──────────────────────────────────────────────────────────────


def _markt(engine, *, bypass=False):
    ergebnisse: list[tuple[str, str]] = []
    cfg = _config.get_config()
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "core.round_table.execution_outcomes.record_execution_outcome",
                side_effect=lambda symbol, code, *a, **k: ergebnisse.append(
                    (symbol, code)
                ),
            )
        )
        stack.enter_context(
            patch.object(cfg, "BYPASS_MARKET_HOURS", bypass, create=True)
        )
        gesperrt = asyncio.run(engine._market_closed_blocks_order("AAPL", "BUY"))
    return gesperrt, ergebnisse


def _uhr(offen=None, fehler=None):
    api = MagicMock()
    if fehler is not None:
        api.get_clock.side_effect = fehler
    else:
        api.get_clock.return_value = SimpleNamespace(is_open=offen)
    return _engine(api=api)


def test_h6_bypass_gibt_frei_ohne_uhr():
    engine = _uhr(offen=False)
    assert _markt(engine, bypass=True) == (False, [])
    engine.api.get_clock.assert_not_called()
    assert not hasattr(engine, "_last_market_open")


def test_h6_uhrfehler_gibt_frei_mit_warnung(caplog):
    engine = _uhr(fehler=RuntimeError("uhr weg"))
    with caplog.at_level(logging.WARNING):
        assert _markt(engine) == (False, [])
    assert any(
        r.levelno == logging.WARNING
        and "clock check failed (uhr weg)" in r.getMessage()
        for r in caplog.records
    )
    assert not hasattr(engine, "_last_market_open")


def test_h6_offener_markt_gibt_frei():
    engine = _uhr(offen=True)
    assert _markt(engine) == (False, [])
    assert engine._last_market_open is True


def test_h6_geschlossener_markt_sperrt_und_meldet():
    engine = _uhr(offen=False)
    assert _markt(engine) == (True, [("AAPL", "blocked:market_closed")])
    assert engine._last_market_open is False
