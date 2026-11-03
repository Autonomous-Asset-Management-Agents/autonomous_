"""#4862 Paritaet schnelle Sim gegen run_sim: der Vergleich (Plan TDD-Schritt 1)."""

from __future__ import annotations

import copy
import logging

import pytest

from core.sim.stimmen_paritaet import bericht_json, trades_voll, vergleiche

pytestmark = [pytest.mark.vc1]

TAGE = ["2026-01-02", "2026-01-05", "2026-01-06"]


def _schnell(equities=(100_000.0, 100_500.0, 101_000.0), trades=None, ents=None):
    """Ein Strategie-Eintrag in der Form von ``stimmen_sim.main``."""
    trades = (
        trades if trades is not None else [("2026-01-02", "AAA", "BUY", 10.0, 100.0)]
    )
    orders = [
        {
            "tag": t,
            "symbol": s,
            "side": side,
            "qty": q,
            "status": "filled",
            "fill_price": p,
            "blocked_reason": None,
        }
        for t, s, side, q, p in trades
    ]
    kurve = [{"date": t, "equity": e} for t, e in zip(TAGE, equities)]
    return {
        "strategie": "a",
        "ergebnis": {
            "starting_equity": 100_000.0,
            "ending_equity": equities[-1],
            "orders": orders,
            "daily_equity": kurve,
        },
        "entscheidungen": ents
        or [
            {"tag": t, "symbol": s, "aktion": side, "grund": "konsens"}
            for t, s, side, _q, _p in trades
        ],
    }


def _voll(equities=(100_000.0, 100_500.0, 101_000.0), trades=None):
    """Ein Ergebnis in der Form von ``run_signal_replay`` (``SimResult.__dict__``)."""
    trades = (
        trades if trades is not None else [("2026-01-02", "AAA", "BUY", 10.0, 100.0)]
    )
    orders = [
        {
            "symbol": s,
            "side": side,
            "qty": q,
            "status": "filled",
            "fill_price": p,
            "blocked_reason": None,
            "filled_at": f"{t}T15:55:00-05:00",
        }
        for t, s, side, q, p in trades
    ]
    orders.append({"symbol": "BBB", "side": None, "qty": None, "status": "blocked"})
    kurve = [{"date": t, "equity": e} for t, e in zip(TAGE, equities)]
    return {
        "starting_equity": 100_000.0,
        "ending_equity": equities[-1],
        "orders": orders,
        "daily_equity": kurve,
    }


def test_gleiche_ergebnisse_innerhalb_toleranz():
    b = vergleiche(_schnell(), _voll(), n_tage=len(TAGE))
    assert (
        b.innerhalb_toleranz and b.trades_nur_schnell == () and b.trades_nur_voll == ()
    )
    assert (
        b.trade_quote == 1.0 and b.max_tagesrendite_bp == 0.0 and b.endbilanz_bp == 0.0
    )


def test_trade_nur_in_einer_sim_ausserhalb():
    extra = ("2026-01-05", "CCC", "BUY", 5.0, 50.0)
    voll = _voll(trades=[("2026-01-02", "AAA", "BUY", 10.0, 100.0), extra])
    b = vergleiche(_schnell(), voll, n_tage=len(TAGE))
    assert (
        b.trades_nur_voll == (("2026-01-05", "CCC", "BUY"),)
        and b.trades_nur_schnell == ()
    )
    assert b.trade_quote == pytest.approx(0.5) and not b.innerhalb_toleranz


def test_tagesrendite_ueber_1bp_ausserhalb():
    voll = _voll(equities=(100_000.0, 100_520.0, 101_000.0))
    b = vergleiche(_schnell(), voll, n_tage=len(TAGE))
    assert (
        b.max_tagesrendite_bp == pytest.approx(2.0, abs=0.01) and b.endbilanz_bp == 0.0
    )
    assert not b.innerhalb_toleranz


def test_endbilanz_toleranz_skaliert_mit_tagen():
    schnell, voll = _schnell(), _voll()
    schnell["ergebnis"]["daily_equity"] = []
    voll["daily_equity"] = []
    voll["ending_equity"] = 101_000.0 * (1 + 2e-4)

    eng = vergleiche(schnell, voll, n_tage=1)
    weit = vergleiche(schnell, voll, n_tage=3)
    assert eng.endbilanz_bp == pytest.approx(2.0, abs=0.01)
    assert not eng.innerhalb_toleranz and weit.innerhalb_toleranz


def test_voll_zeile_ohne_filled_at_warning_nicht_still(caplog):
    voll = _voll()
    del voll["orders"][0]["filled_at"]

    with caplog.at_level(logging.WARNING, logger="core.sim.stimmen_paritaet"):
        trades, nicht_zuordenbar = trades_voll(voll)
        b = vergleiche(_schnell(), voll, n_tage=len(TAGE))

    assert (
        trades == []
        and len(nicht_zuordenbar) == 1
        and nicht_zuordenbar[0]["symbol"] == "AAA"
    )
    assert any("filled_at" in r.getMessage() for r in caplog.records)
    assert b.nicht_zuordenbar == 1 and not b.innerhalb_toleranz


def test_ursachen_zugeordnet_nicht_abgezogen():
    ents = [
        {"tag": "2026-01-02", "symbol": "AAA", "aktion": "HOLD", "grund": "agent_veto"}
    ]
    schnell = _schnell(trades=[], ents=ents)
    b = vergleiche(schnell, _voll(), n_tage=len(TAGE))
    assert b.trades_nur_voll == (("2026-01-02", "AAA", "BUY"),)
    assert b.ursachen == (("2026-01-02", "AAA", "BUY", "agent_veto"),)
    assert not b.innerhalb_toleranz


def test_ursache_aus_blockiertem_auftrag():
    schnell = _schnell(trades=[])
    schnell["ergebnis"]["orders"] = [
        {
            "tag": "2026-01-02",
            "symbol": "AAA",
            "side": "BUY",
            "qty": 0.0,
            "status": "blocked",
            "fill_price": None,
            "blocked_reason": "slot_deckel",
        }
    ]
    schnell["entscheidungen"] = [
        {"tag": "2026-01-02", "symbol": "AAA", "aktion": "BUY", "grund": "konsens"}
    ]
    b = vergleiche(schnell, _voll(), n_tage=len(TAGE))
    assert b.ursachen == (("2026-01-02", "AAA", "BUY", "slot_deckel"),)


def test_mengen_abweichung_getrennt_ausgewiesen():
    voll = _voll(trades=[("2026-01-02", "AAA", "BUY", 12.5, 100.0)])
    b = vergleiche(_schnell(), voll, n_tage=len(TAGE))
    assert b.trades_nur_schnell == () and b.trades_nur_voll == ()
    assert b.mengen_abweichung == (("2026-01-02", "AAA", "BUY", pytest.approx(0.2)),)
    assert b.innerhalb_toleranz


def test_tag_ohne_gegenstueck_warning_und_ausserhalb(caplog):
    voll = _voll()
    voll["daily_equity"] = voll["daily_equity"][:-1]
    with caplog.at_level(logging.WARNING, logger="core.sim.stimmen_paritaet"):
        b = vergleiche(_schnell(), voll, n_tage=len(TAGE))
    assert b.tage_ohne_gegenstueck == ("2026-01-06",)
    assert any("2026-01-06" in r.getMessage() for r in caplog.records)
    assert not b.innerhalb_toleranz


@pytest.mark.parametrize("flag", ["timed_out", "halted"])
def test_abgebrochene_referenz_ausserhalb(flag):
    voll = _voll()
    voll[flag] = True
    b = vergleiche(_schnell(), voll, n_tage=len(TAGE))
    assert b.voll_abgebrochen and not b.innerhalb_toleranz


def test_bericht_bytegleich():
    schnell, voll = _schnell(), _voll(equities=(100_000.0, 100_520.0, 101_000.0))
    eins = bericht_json(vergleiche(schnell, voll, n_tage=3))
    zwei = bericht_json(vergleiche(copy.deepcopy(schnell), copy.deepcopy(voll), 3))
    assert eins == zwei and '"innerhalb_toleranz": false' in eins
