"""#4125 Teil 2 (Plan §2 Punkt 3, §12b–12f): der Vertrag ``Verkaufsgrund`` und seine Erzeugung.

Der Grund entsteht am Ort der Entscheidung, mit den Ausloese-Werten als Feldern — nicht als
Freitext, der spaeter geparst wird (Plan, Option B verworfen). Die Klassen sind eine
geschlossene Liste (§12b); ``unknown`` ist der Rueckfall und wird als WARNING geloggt.
"""

import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import core.intelligent_exit as ie
from core.contracts.verkaufsgrund import (
    VERKAUFSGRUND_KLASSEN,
    Verkaufsgrund,
    unbekannter_grund,
)
from core.intelligent_exit import PositionContext, analyze_exit

pytestmark = pytest.mark.vc3


def _ctx(entry=100.0, current=100.0, hwm=100.0, hours=30.0, lstm=0.0, momentum=None):
    return PositionContext(
        symbol="X",
        entry_price=entry,
        current_price=current,
        high_water_mark=hwm,
        hours_held=hours,
        lstm_prediction=lstm,
        momentum_history=momentum or [],
    )


# -- Vertrag ---------------------------------------------------------------


def test_klassenliste_ist_geschlossen_und_vollstaendig():
    assert VERKAUFSGRUND_KLASSEN == {
        "hard_stop",
        "trailing",
        "loss_cut",
        "loss_escalation",
        "momentum_fade",
        "composite",
        "rank_drop",
        "rotation",
        "book_overflow",
        "trim",
        "consensus_sell",
        "model_sell",
        "displacement",
        "panic",
        "breaker",
        "strategy_switch",
        "broker_stop",
        "manual",
        "unknown",
    }
    assert "loss_watch" not in VERKAUFSGRUND_KLASSEN  # §12c Option A


def test_unbekannte_klasse_wird_abgewiesen():
    with pytest.raises(ValidationError):
        Verkaufsgrund(klasse="loss_watch")


def test_grund_ist_unveraenderlich():
    grund = Verkaufsgrund(klasse="trailing", werte={"abstand_pct": 4.0})
    with pytest.raises(ValidationError):
        grund.klasse = "loss_cut"


def test_zeitpunkt_wird_uebergeben_nicht_erfunden():
    assert Verkaufsgrund(klasse="trailing").zeitpunkt is None
    zeit = datetime(2026, 10, 12, 15, 0, tzinfo=timezone.utc)
    assert Verkaufsgrund(klasse="trailing", zeitpunkt=zeit).zeitpunkt == zeit


def test_unbekannt_ist_ein_lauter_rueckfall(caplog):
    with caplog.at_level(logging.WARNING):
        grund = unbekannter_grund("Konsens-Pfad ohne Grund")
    assert grund.klasse == "unknown"
    assert any("Konsens-Pfad ohne Grund" in r.getMessage() for r in caplog.records)


# -- analyze_exit: jeder Verkaufszweig traegt Klasse und Werte (Wege 1-7) ------


def test_hard_stop_im_panikfenster():
    analyse = analyze_exit(_ctx(current=91.5, hours=0.5))
    grund = analyse.verkaufsgrund
    assert grund.klasse == "hard_stop"
    assert grund.werte["verlust_pct"] == pytest.approx(-8.5)
    assert grund.werte["stufe"] == pytest.approx(ie._loss_tiers().hard_stop)
    assert grund.werte["haltedauer_h"] == pytest.approx(0.5)


def test_loss_cut_mit_stufe_cut():
    analyse = analyze_exit(_ctx(current=93.5, hours=30.0))
    grund = analyse.verkaufsgrund
    assert grund.klasse == "loss_cut"
    assert grund.werte["verlust_pct"] == pytest.approx(-6.5)
    assert grund.werte["stufe"] == pytest.approx(ie._loss_tiers().cut)
    assert grund.werte["haltedauer_h"] == pytest.approx(30.0)
    assert grund.werte["verlustdruck"] >= 90


def test_loss_cut_mit_stufe_escalation():
    tiers = ie._loss_tiers()
    preis = 100.0 + (tiers.escalation + tiers.hard_stop) / 2
    grund = analyze_exit(_ctx(current=preis, hours=30.0)).verkaufsgrund
    assert grund.klasse == "loss_escalation"
    assert grund.werte["stufe"] == pytest.approx(tiers.escalation)


def test_loss_cut_mit_stufe_hard_stop():
    grund = analyze_exit(_ctx(current=90.0, hours=30.0)).verkaufsgrund
    assert grund.klasse == "hard_stop"
    assert grund.werte["stufe"] == pytest.approx(ie._loss_tiers().hard_stop)


def test_trailing_mit_hoch_und_abstand(monkeypatch):
    monkeypatch.setattr(ie, "_calculate_trailing_stop_score", lambda *a: 90.0)
    grund = analyze_exit(_ctx(current=110.0, hwm=125.0)).verkaufsgrund
    assert grund.klasse == "trailing"
    assert grund.werte["gewinn_pct"] == pytest.approx(10.0)
    assert grund.werte["hoch"] == pytest.approx(125.0)
    assert grund.werte["abstand_pct"] == pytest.approx(12.0)
    assert grund.werte["haltedauer_h"] == pytest.approx(30.0)


def test_momentum_fade_mit_score():
    grund = analyze_exit(
        _ctx(current=110.0, hwm=110.0, lstm=-0.6, momentum=[0.3, 0.1, -0.1])
    ).verkaufsgrund
    assert grund.klasse == "momentum_fade"
    assert grund.werte["gewinn_pct"] == pytest.approx(10.0)
    assert grund.werte["momentum_score"] >= 80


def test_gesamt_score_mit_allen_einzelscores(monkeypatch):
    monkeypatch.setattr(ie, "_calculate_loss_pressure", lambda *a: 75.0)
    monkeypatch.setattr(ie, "_calculate_trailing_stop_score", lambda *a: 0.0)
    monkeypatch.setattr(ie, "_calculate_momentum_fade", lambda *a: 60.0)
    monkeypatch.setattr(ie, "_calculate_news_pressure", lambda *a: 90.0)
    monkeypatch.setattr(ie, "_calculate_time_pressure", lambda *a: 90.0)
    grund = analyze_exit(_ctx(current=97.0)).verkaufsgrund
    assert grund.klasse == "composite"
    assert grund.werte["schwelle"] == pytest.approx(ie.SELL_THRESHOLD)
    assert grund.werte["gesamt_score"] >= ie.SELL_THRESHOLD
    for feld in (
        "verlustdruck",
        "trailing_score",
        "momentum_score",
        "news_score",
        "zeit_score",
        "pnl_pct",
        "haltedauer_h",
    ):
        assert feld in grund.werte


def test_halten_traegt_keinen_grund():
    assert analyze_exit(_ctx(current=100.0)).verkaufsgrund is None


def test_der_freitext_bleibt_wie_bisher():
    """``reason`` speist Log und ``stop_type``; Teil 2 aendert ihn nicht."""
    assert analyze_exit(_ctx(current=93.5)).reason.startswith("LOSS CUT:")


# -- Kontext ohne eigenen Vertrag (Wege 10-12) und unknown -------------------


def _event(portfolio_reason="", verkaufsgrund=None, triggered_by_stop=False):
    return SimpleNamespace(
        verkaufsgrund=verkaufsgrund,
        decision_context=SimpleNamespace(
            triggered_by_stop=triggered_by_stop, portfolio_reason=portfolio_reason
        ),
    )


@pytest.mark.parametrize(
    "grund,klasse",
    [
        ("rotation: dropped to rank 14/40 (out of top-10)", "rotation"),
        (
            "book_overflow_unwind: 12/10 positions held — orderly wind-down (#2886)",
            "book_overflow",
        ),
        ("deconcentration_trim: drift +3.2% → target", "trim"),
    ],
)
def test_kontext_ohne_vertrag_wird_nach_dem_weg_eingeordnet(grund, klasse):
    from core.engine.order_aufbau import verkaufsgrund_aus_kontext

    assert verkaufsgrund_aus_kontext(_event(grund)).klasse == klasse


def test_ein_mitgegebener_grund_geht_vor():
    from core.engine.order_aufbau import verkaufsgrund_aus_kontext

    mitgegeben = Verkaufsgrund(klasse="rotation", werte={"rang": 14})
    assert verkaufsgrund_aus_kontext(_event("trim", mitgegeben)) is mitgegeben


def test_ohne_grund_unknown_mit_warning(caplog):
    from core.engine.order_aufbau import verkaufsgrund_aus_kontext

    with caplog.at_level(logging.WARNING):
        grund = verkaufsgrund_aus_kontext(_event(""))
    assert grund.klasse == "unknown"
    assert caplog.records


def test_classify_exit_kind_bleibt_unveraendert():
    from core.engine.order_aufbau import classify_exit_kind

    ev = SimpleNamespace(
        triggered_by_stop=False, portfolio_reason="book_overflow_unwind: 1"
    )
    assert classify_exit_kind(ev) is None


# -- LSTM-Strategie (Wege 8, 9) ---------------------------------------------


def test_rangverlust_der_lstm_strategie(monkeypatch):
    from core.strategies import lstm_strategy as ls

    monkeypatch.setattr(
        ls,
        "rank_drop_exit",
        lambda **kw: SimpleNamespace(sell=True, reason="rank drop"),
    )
    strat = ls.LSTMDynamicStrategy.__new__(ls.LSTMDynamicStrategy)
    urteil = strat._exit_decision("X", 100.0, 99.0, 100.0, 80.0, False, 23)
    assert urteil.action == "SELL"
    assert urteil.verkaufsgrund.klasse == "rank_drop"
    assert urteil.verkaufsgrund.werte["rang"] == 23
    assert urteil.verkaufsgrund.werte["top_n"] == ls.LSTM_DYNAMIC_TOP_N
    assert urteil.verkaufsgrund.werte["haltedauer_h"] == pytest.approx(80.0)


def test_analyse_der_lstm_strategie_reicht_den_grund_durch(monkeypatch):
    from core.strategies import lstm_strategy as ls

    monkeypatch.setattr(
        ls, "rank_drop_exit", lambda **kw: SimpleNamespace(sell=False, reason="")
    )
    strat = ls.LSTMDynamicStrategy.__new__(ls.LSTMDynamicStrategy)
    monkeypatch.setattr(strat, "_get_pred_for_symbol", lambda s: 0.0, raising=False)
    urteil = strat._exit_decision("X", 100.0, 93.5, 100.0, 30.0, True, 3)
    assert urteil.action == "SELL"
    assert urteil.verkaufsgrund.klasse == "loss_cut"
