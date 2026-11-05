"""#3823 (ARC-E6 G-2a) — Charakterisierung der Ausstiegsrunde vor dem Umbau.

Plan: ``docs/3823-*/implementation_plan.md`` §6 Schritt 1 und 4.

Hält fest, was ``TradingLoopMixin._run_deconcentration_and_rotation_exits`` heute tut —
beobachtet an ``_process_signal_event``, ``pm.record_trade``, den Sitzungszählern, der
Rückgabe und den Log-Zeilen (wortgleich, Auswertungen lesen darauf). Die Tests liefen
grün gegen den Code **vor** dem Umbau und bleiben danach **unverändert** grün: das ist
der Gleichstand-Nachweis (Plan §2 Punkt 3).

Ergänzt ``test_sell_decisions_exit_hook.py`` um das, was dort fehlt: Retention-Gate
(#3180) je Hebel, die geteilte ``acted``-Menge zwischen Rotation und Trim, Teilergebnisse
bei einem Hebelfehler und den ``now``-Default.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import core.consensus_retention as retention_mod
import core.engine.trading_loop as tl
import core.report.lstm_panel_store as panel_mod
from core.engine.trading_loop import TradingLoopMixin

pytestmark = [pytest.mark.unit, pytest.mark.vc3]

_NOW = datetime(2026, 7, 27, 15, 0, 0, tzinfo=timezone.utc)


# ── Gerüst ────────────────────────────────────────────────────────────────────


def _score(qty=10.0, price=500.0, days_held=10, avg_entry=100.0):
    return SimpleNamespace(
        qty=qty,
        market_value=qty * price,
        current_price=price,
        days_held=days_held,
        avg_entry=avg_entry,
    )


def _engine(scores, recs=None, max_positions=10):
    eng = TradingLoopMixin.__new__(TradingLoopMixin)
    pm = SimpleNamespace(
        _position_scores=dict(scores),
        max_positions=max_positions,
        refresh_positions=MagicMock(),
        record_trade=MagicMock(),
        get_rebalance_recommendations=MagicMock(return_value=list(recs or [])),
    )
    eng.active_strategy = SimpleNamespace(portfolio_manager=pm)
    eng._process_signal_event = AsyncMock()
    return eng, pm


def _cfg(
    *,
    rotation=False,
    trim=False,
    rot_cap=0,
    rot_session=0,
    trim_session=0,
    book_cap=False,
    min_order=1.0,
):
    return SimpleNamespace(
        ROTATION_EXIT_ENABLED=rotation,
        DECONCENTRATION_TRIM_ENABLED=trim,
        ROTATION_PANEL_MAX_AGE_DAYS=3,
        SMART_EXIT_MIN_HOLD_DAYS=5.0,
        SMART_EXIT_EXIT_RANK_HYSTERESIS=3.0,
        MIN_ORDER_VALUE_USD=min_order,
        ROTATION_MAX_EXITS_PER_CYCLE=rot_cap,
        ROTATION_MAX_EXITS_PER_SESSION=rot_session,
        ROTATION_MIN_HOLD_HARD_GATE=True,
        TRIM_MAX_EXITS_PER_SESSION=trim_session,
        BOOK_CAP_ENFORCEMENT_ENABLED=book_cap,
    )


def _rec(symbol, *, action="REDUCE", adjustment_value=-2500.0, market_value=5000.0):
    return {
        "symbol": symbol,
        "action": action,
        "drift_pct": 13.0,
        "adjustment_value": adjustment_value,
        "market_value": market_value,
    }


@pytest.fixture
def setze(monkeypatch):
    """Konfiguration, Panel und Retention-Gate für einen Lauf festlegen."""

    def _setze(cfg, ranks=None, gesperrt=(), snap=None, standing=None):
        monkeypatch.setattr(tl, "get_config", lambda: cfg)
        snap_date = _NOW.date() if snap is None else snap
        store = SimpleNamespace(
            latest_snapshot_date=lambda: snap_date,
            cross_section_at=lambda now: {"_panel_present": 1.0},
        )
        monkeypatch.setattr(panel_mod, "get_store", lambda: store)
        monkeypatch.setattr(
            panel_mod,
            "cross_section_standing",
            standing or (lambda xsec, sym: (ranks or {}).get(sym, (None, None, None))),
        )
        gefragt = []

        def _veto(sym, kind, pm):
            gefragt.append((sym, kind))
            return (sym, kind) in set(gesperrt)

        monkeypatch.setattr(retention_mod, "consensus_retention_veto", _veto)
        return gefragt

    return _setze


def _verkauft(eng):
    return [c.args[0] for c in eng._process_signal_event.await_args_list]


# ── Einstieg: Flags, PortfolioManager, now ────────────────────────────────────


@pytest.mark.anyio
async def test_flags_aus_ist_leer_und_fragt_die_uhr_nicht(setze, monkeypatch):
    setze(_cfg())
    uhr = MagicMock(side_effect=AssertionError("Uhr darf nicht gefragt werden"))
    monkeypatch.setattr(tl.CompositionRoot, "get_instance", uhr)
    eng, pm = _engine({"AAPL": _score()})

    assert (
        await eng._run_deconcentration_and_rotation_exits(already_acted=set()) == set()
    )
    uhr.assert_not_called()
    eng._process_signal_event.assert_not_awaited()


@pytest.mark.anyio
async def test_ohne_portfoliomanager_leer(setze):
    setze(_cfg(rotation=True, trim=True))
    eng = TradingLoopMixin.__new__(TradingLoopMixin)
    eng.active_strategy = SimpleNamespace(portfolio_manager=None)
    eng._process_signal_event = AsyncMock()

    assert await eng._run_deconcentration_and_rotation_exits(set(), now=_NOW) == set()
    eng._process_signal_event.assert_not_awaited()


@pytest.mark.anyio
async def test_ohne_strategie_leer(setze):
    setze(_cfg(rotation=True))
    eng = TradingLoopMixin.__new__(TradingLoopMixin)
    eng._process_signal_event = AsyncMock()

    assert await eng._run_deconcentration_and_rotation_exits(set(), now=_NOW) == set()


@pytest.mark.anyio
async def test_now_kommt_ohne_angabe_von_der_uhr(setze, monkeypatch):
    setze(_cfg(rotation=True), ranks={"AAPL": (5.0, 400, 503)})
    uhr = SimpleNamespace(clock_port=SimpleNamespace(now=lambda: _NOW))
    monkeypatch.setattr(tl.CompositionRoot, "get_instance", lambda: uhr)
    eng, _ = _engine({"AAPL": _score()})

    assert await eng._run_deconcentration_and_rotation_exits(set()) == {"AAPL"}
    assert eng._rotation_session_date == _NOW.date()


@pytest.mark.anyio
async def test_already_acted_none_wird_als_leer_gelesen(setze):
    setze(_cfg(rotation=True), ranks={"AAPL": (5.0, 400, 503)})
    eng, _ = _engine({"AAPL": _score()})

    assert await eng._run_deconcentration_and_rotation_exits(None, now=_NOW) == {"AAPL"}


@pytest.mark.anyio
async def test_already_acted_wird_nicht_veraendert(setze):
    setze(
        _cfg(rotation=True, trim=True),
        ranks={"AAPL": (5.0, 400, 503)},
    )
    eng, _ = _engine({"AAPL": _score(), "MSFT": _score()}, recs=[_rec("MSFT")])
    acted = {"NVDA"}

    out = await eng._run_deconcentration_and_rotation_exits(acted, now=_NOW)
    assert out == {"AAPL", "MSFT"}
    assert acted == {"NVDA"}


# ── Retention-Gate (#3180) je Hebel ───────────────────────────────────────────


@pytest.mark.anyio
async def test_retention_unterdrueckt_rotation_und_protokolliert(setze, caplog):
    gefragt = setze(
        _cfg(rotation=True, rot_cap=1),
        ranks={"AAPL": (5.0, 400, 503), "MSFT": (5.0, 300, 503)},
        gesperrt={("AAPL", "rotation")},
    )
    eng, pm = _engine({"AAPL": _score(), "MSFT": _score()})

    with caplog.at_level(logging.INFO):
        out = await eng._run_deconcentration_and_rotation_exits(set(), now=_NOW)

    # Der gesperrte Name verbraucht keinen Platz im Zyklus-Deckel (1): MSFT geht trotzdem.
    assert out == {"MSFT"}
    assert [e.symbol for e in _verkauft(eng)] == ["MSFT"]
    assert gefragt == [("AAPL", "rotation"), ("MSFT", "rotation")]
    pm.record_trade.assert_called_once_with("MSFT", "sell")
    assert (
        "[Rotation] AAPL: exit SUPPRESSED — live round-table consensus still retains "
        "the name (#3180 gate)" in caplog.messages
    )


@pytest.mark.anyio
async def test_retention_wird_vor_dem_deckel_gefragt(setze, caplog):
    gefragt = setze(
        _cfg(rotation=True, rot_cap=1),
        ranks={"AAPL": (5.0, 400, 503), "MSFT": (5.0, 300, 503)},
    )
    eng, _ = _engine({"AAPL": _score(), "MSFT": _score()})

    with caplog.at_level(logging.INFO):
        out = await eng._run_deconcentration_and_rotation_exits(set(), now=_NOW)

    assert out == {"AAPL"}
    # MSFT wird gefragt, bevor der Deckel greift — und dann aufgeschoben.
    assert gefragt == [("AAPL", "rotation"), ("MSFT", "rotation")]
    assert (
        "[Rotation] per-cycle cap 1 reached — deferring further eligible exits to "
        "the next cycle." in caplog.messages
    )


@pytest.mark.anyio
async def test_retention_unterdrueckt_trim_und_protokolliert(setze, caplog):
    gefragt = setze(_cfg(trim=True), gesperrt={("AAPL", "trim")})
    eng, pm = _engine(
        {"AAPL": _score(), "MSFT": _score()}, recs=[_rec("AAPL"), _rec("MSFT")]
    )

    with caplog.at_level(logging.INFO):
        out = await eng._run_deconcentration_and_rotation_exits(set(), now=_NOW)

    assert out == {"MSFT"}
    assert gefragt == [("AAPL", "trim"), ("MSFT", "trim")]
    assert eng._trim_exits_session == 1
    assert (
        "[Trim] AAPL: trim SUPPRESSED — live round-table consensus still retains the "
        "name (#3180 gate)" in caplog.messages
    )


@pytest.mark.anyio
async def test_retention_unterdrueckt_ueberhang_und_protokolliert(setze, caplog):
    held = {f"S{i}": _score(days_held=10) for i in range(3)}
    gefragt = setze(
        _cfg(rotation=True, book_cap=True, rot_cap=5),
        ranks={s: (50.0, 2 + i, 503) for i, s in enumerate(held)},
        gesperrt={("S2", "book_overflow")},
    )
    eng, pm = _engine(held, max_positions=1)

    with caplog.at_level(logging.INFO):
        out = await eng._run_deconcentration_and_rotation_exits(set(), now=_NOW)

    # Schwächster Rang zuerst: S2 (gesperrt), dann S1.
    assert gefragt == [("S2", "book_overflow"), ("S1", "book_overflow")]
    assert out == {"S1"}
    assert (
        "[BookOverflow] S2: unwind SUPPRESSED — live round-table consensus retains "
        "the name (#3180)" in caplog.messages
    )
    assert "[BookOverflow] S1: unwind SELL dispatched (3/1 held) — #2886" in (
        caplog.messages
    )
    ev = _verkauft(eng)[0]
    assert ev.suggested_quantity == 0.0
    assert ev.decision_context.portfolio_reason == (
        "book_overflow_unwind: 3/1 positions held — orderly wind-down (#2886)"
    )
    assert eng._rotation_exits_session == 1


# ── Rotation vor Trim, geteilte acted-Menge ───────────────────────────────────


@pytest.mark.anyio
async def test_rotierter_name_wird_nicht_getrimmt(setze, caplog):
    setze(_cfg(rotation=True, trim=True), ranks={"AAPL": (5.0, 400, 503)})
    eng, pm = _engine(
        {"AAPL": _score(), "MSFT": _score()},
        recs=[_rec("AAPL"), _rec("MSFT")],
    )

    with caplog.at_level(logging.INFO):
        out = await eng._run_deconcentration_and_rotation_exits(set(), now=_NOW)

    assert out == {"AAPL", "MSFT"}
    evs = _verkauft(eng)
    assert [(e.symbol, e.suggested_quantity) for e in evs] == [
        ("AAPL", 0.0),
        ("MSFT", 5.0),
    ]
    assert [c.args for c in pm.record_trade.call_args_list] == [
        ("AAPL", "sell"),
        ("MSFT", "sell"),
    ]
    assert "[Rotation] AAPL: rank 400/503 out of top-10 → full SELL dispatched" in (
        caplog.messages
    )
    assert (
        "[Trim] MSFT: trim 5.0000 of 10.0000 held (mv=5000.00 adj=2500.00) → "
        "partial SELL" in caplog.messages
    )
    assert eng._reentry_partial_exits == {"MSFT"}


@pytest.mark.anyio
async def test_already_acted_sperrt_beide_hebel(setze):
    gefragt = setze(_cfg(rotation=True, trim=True), ranks={"AAPL": (5.0, 400, 503)})
    eng, pm = _engine({"AAPL": _score()}, recs=[_rec("AAPL")])

    assert (
        await eng._run_deconcentration_and_rotation_exits({"AAPL"}, now=_NOW) == set()
    )
    eng._process_signal_event.assert_not_awaited()
    assert gefragt == []


@pytest.mark.anyio
async def test_doppelte_trim_empfehlung_verkauft_einmal(setze):
    setze(_cfg(trim=True))
    eng, _ = _engine({"AAPL": _score()}, recs=[_rec("AAPL"), _rec("AAPL")])

    assert await eng._run_deconcentration_and_rotation_exits(set(), now=_NOW) == {
        "AAPL"
    }
    assert len(_verkauft(eng)) == 1


@pytest.mark.anyio
async def test_rotation_ereignis_und_kontext(setze):
    setze(_cfg(rotation=True), ranks={"AAPL": (5.0, 400, 503)})
    eng, _ = _engine({"AAPL": _score(qty=7.0, price=300.0, avg_entry=250.0)})

    await eng._run_deconcentration_and_rotation_exits(set(), now=_NOW)

    ev = _verkauft(eng)[0]
    ctx = ev.decision_context
    assert (ev.symbol, ev.action, ev.suggested_quantity) == ("AAPL", "SELL", 0.0)
    assert (ctx.current_price, ctx.position_qty, ctx.position_avg_price) == (
        300.0,
        7.0,
        250.0,
    )
    assert ctx.triggered_by_stop is False and ctx.in_position is True
    assert ctx.portfolio_reason == "rotation: dropped to rank 400/503 (out of top-10)"


@pytest.mark.anyio
async def test_trim_ereignis_und_kontext(setze):
    setze(_cfg(trim=True))
    eng, _ = _engine(
        {"AAPL": _score(qty=8.0, price=500.0, avg_entry=90.0)},
        recs=[_rec("AAPL", adjustment_value=-1000.0, market_value=4000.0)],
    )

    await eng._run_deconcentration_and_rotation_exits(set(), now=_NOW)

    ev = _verkauft(eng)[0]
    ctx = ev.decision_context
    assert (ev.symbol, ev.action, ev.suggested_quantity) == ("AAPL", "SELL", 2.0)
    assert (ctx.current_price, ctx.position_qty, ctx.position_avg_price) == (
        500.0,
        8.0,
        90.0,
    )
    assert ctx.triggered_by_stop is False
    assert ctx.portfolio_reason == "deconcentration_trim: drift +13.0% → target"


# ── Sitzungsdeckel ────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_rotations_sitzungsdeckel_ueber_zyklen_und_tageswechsel(setze, caplog):
    setze(
        _cfg(rotation=True, rot_cap=1, rot_session=1),
        ranks={"AAPL": (5.0, 400, 503), "MSFT": (5.0, 300, 503)},
    )
    eng, _ = _engine({"AAPL": _score(), "MSFT": _score()})

    assert await eng._run_deconcentration_and_rotation_exits(set(), now=_NOW) == {
        "AAPL"
    }
    with caplog.at_level(logging.WARNING):
        out = await eng._run_deconcentration_and_rotation_exits({"AAPL"}, now=_NOW)
    assert out == set()
    assert (
        "[Rotation] per-session cap 1 reached — no further rotation exits today "
        "(resets next trading day). Protective stop-losses are unaffected."
        in caplog.messages
    )
    morgen = _NOW + timedelta(days=1)
    assert await eng._run_deconcentration_and_rotation_exits({"AAPL"}, now=morgen) == {
        "MSFT"
    }
    assert eng._rotation_session_date == morgen.date()
    assert eng._rotation_exits_session == 1


@pytest.mark.anyio
async def test_trim_sitzungsdeckel_ueber_zyklen_und_tageswechsel(setze, caplog):
    setze(_cfg(trim=True, trim_session=1))
    eng, _ = _engine(
        {"AAPL": _score(), "MSFT": _score()}, recs=[_rec("AAPL"), _rec("MSFT")]
    )

    with caplog.at_level(logging.WARNING):
        assert await eng._run_deconcentration_and_rotation_exits(set(), now=_NOW) == {
            "AAPL"
        }
    assert (
        "[Trim] per-session cap 1 reached - no further trims today (resets next "
        "trading day). Protective stops are unaffected (#2714)." in caplog.messages
    )
    assert await eng._run_deconcentration_and_rotation_exits(set(), now=_NOW) == set()
    morgen = _NOW + timedelta(days=1)
    assert await eng._run_deconcentration_and_rotation_exits(set(), now=morgen) == {
        "AAPL"
    }
    assert eng._trim_session_date == morgen.date()


# ── Hebelfehler: protokolliert, geschluckt, Teilergebnis bleibt ────────────────


@pytest.mark.anyio
async def test_rotationsfehler_behaelt_teilergebnis_und_trim_laeuft(setze, caplog):
    def _standing(xsec, sym):
        if sym == "MSFT":
            raise RuntimeError("panel kaputt")
        return (5.0, 400, 503)

    setze(_cfg(rotation=True, trim=True), standing=_standing)
    eng, _ = _engine(
        {"AAPL": _score(), "MSFT": _score(), "NVDA": _score()},
        recs=[_rec("AAPL"), _rec("NVDA")],
    )

    with caplog.at_level(logging.WARNING):
        out = await eng._run_deconcentration_and_rotation_exits(set(), now=_NOW)

    # AAPL rotiert vor dem Fehler; Trim überspringt AAPL und trimmt NVDA.
    assert out == {"AAPL", "NVDA"}
    assert [e.symbol for e in _verkauft(eng)] == ["AAPL", "NVDA"]
    assert "[Rotation] exit lever failed: panel kaputt" in caplog.messages


@pytest.mark.anyio
async def test_trimfehler_behaelt_teilergebnis(setze, caplog):
    setze(_cfg(trim=True))
    eng, _ = _engine(
        {"AAPL": _score(), "MSFT": _score()},
        recs=[
            _rec("AAPL"),
            {"symbol": "MSFT", "action": "REDUCE", "market_value": "kaputt"},
        ],
    )

    with caplog.at_level(logging.WARNING):
        out = await eng._run_deconcentration_and_rotation_exits(set(), now=_NOW)

    assert out == {"AAPL"}
    assert any(
        m.startswith("[Trim] de-concentration lever failed: ") for m in caplog.messages
    )


@pytest.mark.anyio
async def test_buchungsfehler_nach_rotation_laesst_den_ausstieg_stehen(setze, caplog):
    setze(_cfg(rotation=True), ranks={"AAPL": (5.0, 400, 503)})
    eng, pm = _engine({"AAPL": _score()})
    pm.record_trade.side_effect = RuntimeError("db weg")

    with caplog.at_level(logging.WARNING):
        out = await eng._run_deconcentration_and_rotation_exits(set(), now=_NOW)

    assert out == {"AAPL"}
    assert (
        "[Rotation] failed to record trade for AAPL (cooldown bookkeeping; SELL "
        "already dispatched)" in caplog.messages
    )
    assert eng._rotation_exits_session == 1


@pytest.mark.anyio
async def test_buchungsfehler_nach_trim_laesst_den_ausstieg_stehen(setze, caplog):
    setze(_cfg(trim=True))
    eng, pm = _engine({"AAPL": _score()}, recs=[_rec("AAPL")])
    pm.record_trade.side_effect = RuntimeError("db weg")

    with caplog.at_level(logging.WARNING):
        out = await eng._run_deconcentration_and_rotation_exits(set(), now=_NOW)

    assert out == {"AAPL"}
    assert (
        "[Trim] failed to record trade for AAPL (cooldown bookkeeping; SELL already "
        "dispatched)" in caplog.messages
    )
    assert eng._trim_exits_session == 1


@pytest.mark.anyio
async def test_veraltetes_panel_ueberspringt_rotation_aber_nicht_trim(setze):
    setze(
        _cfg(rotation=True, trim=True),
        ranks={"AAPL": (5.0, 400, 503)},
        snap=(_NOW - timedelta(days=10)).date(),
    )
    eng, pm = _engine({"AAPL": _score()}, recs=[_rec("AAPL")])

    out = await eng._run_deconcentration_and_rotation_exits(set(), now=_NOW)

    assert out == {"AAPL"}
    assert _verkauft(eng)[0].suggested_quantity == 5.0  # Trim, nicht Rotation
    pm.refresh_positions.assert_not_called()
    assert getattr(eng, "_rotation_session_date", None) is None
