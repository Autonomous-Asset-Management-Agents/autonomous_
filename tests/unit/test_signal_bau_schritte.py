"""#4277 (H-4e) — ``_score_to_signal`` in benannten Schritten, je Schritt direkt geprüft.

Plan: ``docs/4277-*/implementation_plan.md`` §2.2 und TDD-Schritt 3. Entscheidung:
``docs/3738-arc-e6-gestalt/H4_SCHNITT_round_table_runner.md`` §5 „H-4e“.

Die Schritte liegen in ``core/round_table/signal_bau.py``. Jeder Test ruft einen Schritt ohne
``run_round_table`` und hält fest, was der Block vor der Zerlegung tat — auch die Fail-safe-
Zweige (IV/Skew-Ausnahme, ungültiger Dämpfungsparameter), die das Netz aus H-4a nicht trifft.
Die Schwellen werden zur Laufzeit am Modul ``signal_bau`` gelesen (Weg (c), #4186 §3).
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

import config
from core.risk_manager import VIX_UNCONFIRMED_MARKER
from core.round_table import signal_bau
from core.round_table.consensus import SIGNAL_BUY_THRESHOLD, SIGNAL_SELL_THRESHOLD
from core.round_table.signal_bau import (
    _aktion_aus_schwellen,
    _begruendungen,
    _drawdown_daempfung,
    _forecast_vol,
    _regime_felder,
    _score_to_signal,
    _skew_perzentil,
    _ta_felder,
)

pytestmark = [pytest.mark.unit, pytest.mark.vc2]

LOGGER = "core.round_table.runner"


def _vote(name="A", score=0.5, weight=1.0, reasoning="r", vetoed=False):
    return SimpleNamespace(
        agent_name=name, score=score, weight=weight, reasoning=reasoning, vetoed=vetoed
    )


def _cfg(monkeypatch, **werte):
    monkeypatch.setattr(config, "get_config", lambda: SimpleNamespace(**werte))


# ── Aktion aus den Schwellen ──────────────────────────────────────────────────


def test_aktion_grenzwerte_sind_strikt():
    assert _aktion_aus_schwellen(SIGNAL_BUY_THRESHOLD) == "HOLD"
    assert _aktion_aus_schwellen(SIGNAL_BUY_THRESHOLD + 1e-9) == "BUY"
    assert _aktion_aus_schwellen(SIGNAL_SELL_THRESHOLD) == "HOLD"
    assert _aktion_aus_schwellen(SIGNAL_SELL_THRESHOLD - 1e-9) == "SELL"


def test_aktion_liest_die_schwelle_zur_laufzeit(monkeypatch):
    monkeypatch.setattr(signal_bau, "SIGNAL_BUY_THRESHOLD", 0.5)
    monkeypatch.setattr(signal_bau, "SIGNAL_SELL_THRESHOLD", 0.2)
    assert _aktion_aus_schwellen(0.51) == "BUY"
    assert _aktion_aus_schwellen(0.3) == "HOLD"
    assert _aktion_aus_schwellen(0.19) == "SELL"


# ── Begründungen (#3251) ──────────────────────────────────────────────────────


def test_begruendungen_digest_top3_ohne_veto_spur_mit_veto():
    votes = [
        _vote("A", weight=0.1, reasoning="a"),
        None,
        _vote("B", weight=0.9, reasoning="b", vetoed=True),
        _vote("C", weight=0.5, reasoning="c"),
        _vote("D", weight=0.3, reasoning="d"),
    ]
    reasoning, spur = _begruendungen(votes)
    # Top-3 nach Gewicht: B (Veto, im Digest ausgelassen), C, D — A fällt heraus.
    assert reasoning == "c | d"
    assert spur == "B [VETO]: b | C: c | D: d | A: a"


def test_begruendungen_spur_ohne_reasoning_und_kappung():
    leer, spur_leer = _begruendungen([_vote("A", reasoning="")])
    assert leer == "" and spur_leer == ""
    _, spur = _begruendungen([_vote("A", reasoning="x" * 9000)])
    assert len(spur) == signal_bau.REASONING_TRACE_MAX_CHARS == 8000


# ── DrawdownGuard-Dämpfung (#1951/#2205) ──────────────────────────────────────


def _dg(score):
    return _vote("DrawdownGuardAgent", score=score)


def test_daempfung_flag_aus_laesst_alles_unveraendert(monkeypatch):
    _cfg(monkeypatch, DRAWDOWN_GUARD_CONDITIONER_ENABLED=False)
    assert _drawdown_daempfung({"symbol": "X"}, 0.9, "BUY", [_dg(0.0)]) == (
        "BUY",
        0.9,
        "",
    )


def test_daempfung_ohne_dg_stimme_warnt_fail_open(monkeypatch, caplog):
    _cfg(monkeypatch, DRAWDOWN_GUARD_CONDITIONER_ENABLED=True)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        ergebnis = _drawdown_daempfung({"symbol": "X"}, 0.9, "BUY", [_vote("A")])
    assert ergebnis == ("BUY", 0.9, "")
    (record,) = [r for r in caplog.records if "fail-open" in r.getMessage()]
    assert record.levelno == logging.WARNING and record.name == LOGGER
    assert "X" in record.getMessage()


def test_daempfung_unter_die_schwelle_wird_hold(monkeypatch):
    monkeypatch.setattr(signal_bau, "SIGNAL_BUY_THRESHOLD", 0.65)
    _cfg(
        monkeypatch,
        DRAWDOWN_GUARD_CONDITIONER_ENABLED=True,
        DRAWDOWN_CONVICTION_DAMP_MAX=0.8,
    )
    action, conviction, note = _drawdown_daempfung(
        {"symbol": "X"}, 0.8, "BUY", [_dg(0.5)]
    )
    assert action == "HOLD"
    assert conviction == pytest.approx(0.48)
    assert note == " (damped to 0.480 → HOLD: X ~10% drawdown below buy gate)"


def test_daempfung_ueber_der_schwelle_bleibt_buy(monkeypatch):
    monkeypatch.setattr(signal_bau, "SIGNAL_BUY_THRESHOLD", 0.4)
    _cfg(
        monkeypatch,
        DRAWDOWN_GUARD_CONDITIONER_ENABLED=True,
        DRAWDOWN_CONVICTION_DAMP_MAX=0.8,
    )
    action, conviction, note = _drawdown_daempfung(
        {"symbol": "X"}, 0.8, "BUY", [_dg(0.5)]
    )
    assert action == "BUY"
    assert conviction == pytest.approx(0.48)
    assert note == " (damped to 0.480 due to X ~10% drawdown)"


@pytest.mark.parametrize("damp", ["kaputt", None])
def test_daempfung_ungueltiger_parameter_faellt_auf_08(monkeypatch, damp):
    monkeypatch.setattr(signal_bau, "SIGNAL_BUY_THRESHOLD", 0.1)
    _cfg(
        monkeypatch,
        DRAWDOWN_GUARD_CONDITIONER_ENABLED=True,
        DRAWDOWN_CONVICTION_DAMP_MAX=damp,
    )
    _, conviction, _ = _drawdown_daempfung({"symbol": "X"}, 1.0, "BUY", [_dg(0.0)])
    assert conviction == pytest.approx(1.0 * (1.0 - 0.8 * 1.0))


@pytest.mark.parametrize("action", ["SELL", "HOLD"])
def test_daempfung_beruehrt_sell_und_hold_nie(monkeypatch, action):
    def _nie():
        raise AssertionError("get_config darf für SELL/HOLD nicht laufen")

    monkeypatch.setattr(config, "get_config", _nie)
    assert _drawdown_daempfung({"symbol": "X"}, 0.2, action, [_dg(0.0)]) == (
        action,
        0.2,
        "",
    )


# ── TA-Felder (#2389) ─────────────────────────────────────────────────────────


def test_ta_felder_defaults_ohne_features():
    assert _ta_felder({}) == {
        "rsi_14": 50.0,
        "macd": 0.0,
        "bb_pct": 0.5,
        "volume_ratio": 1.0,
        "atr_14d": 0.0,
    }


def test_ta_felder_abbildung_und_kaputte_werte():
    felder = _ta_felder(
        {
            "rsi_14": "kaputt",
            "macd_hist": 0.3,
            "bb_position": 0.4,
            "vol_anomaly": None,
            "atr_14d": "2.5",
        }
    )
    assert felder == {
        "rsi_14": 50.0,
        "macd": 0.3,
        "bb_pct": pytest.approx(0.7),
        "volume_ratio": 1.0,
        "atr_14d": 2.5,
    }


# ── forecast_vol (#1953, #3094) ───────────────────────────────────────────────


def test_forecast_vol_ohne_ml_ist_none(monkeypatch):
    _cfg(monkeypatch, IMPLIED_VOL_FORECAST_ENABLED=False)
    assert _forecast_vol({"ml": None}) is None
    assert _forecast_vol({}) is None


def test_forecast_vol_har_rv_bei_flag_aus(monkeypatch):
    _cfg(monkeypatch, IMPLIED_VOL_FORECAST_ENABLED=False)
    assert _forecast_vol({"ml": {"forecast_vol": 0.02}, "implied_vol": 0.3}) == 0.02


def test_forecast_vol_iv_override(monkeypatch):
    import core.ml.vol_model as vol_model

    gesehen = {}

    def _iv(iv, vrp_factor):
        gesehen.update(iv=iv, vrp=vrp_factor)
        return 0.011

    monkeypatch.setattr(vol_model, "debiased_daily_vol_from_iv", _iv)
    _cfg(monkeypatch, IMPLIED_VOL_FORECAST_ENABLED=True, IV_VRP_DEBIAS_FACTOR=0.9)
    state = {"ml": {"forecast_vol": 0.02}, "implied_vol": 0.3}
    assert _forecast_vol(state) == 0.011
    assert gesehen == {"iv": 0.3, "vrp": 0.9}


def test_forecast_vol_iv_none_behaelt_har_rv(monkeypatch):
    import core.ml.vol_model as vol_model

    monkeypatch.setattr(vol_model, "debiased_daily_vol_from_iv", lambda iv, **k: None)
    _cfg(monkeypatch, IMPLIED_VOL_FORECAST_ENABLED=True)
    assert _forecast_vol({"ml": {"forecast_vol": 0.02}}) == 0.02


def test_forecast_vol_ausnahme_behaelt_har_rv_und_loggt(monkeypatch, caplog):
    import core.ml.vol_model as vol_model

    def _wirft(*a, **k):
        raise RuntimeError("iv kaputt")

    monkeypatch.setattr(vol_model, "debiased_daily_vol_from_iv", _wirft)
    _cfg(monkeypatch, IMPLIED_VOL_FORECAST_ENABLED=True)
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        assert _forecast_vol({"ml": {"forecast_vol": 0.02}}) == 0.02
    (record,) = [r for r in caplog.records if "IV Forecast Vol" in r.getMessage()]
    assert record.levelno == logging.ERROR and record.name == LOGGER
    assert record.exc_info is not None


# ── Skew-Perzentil (#3199) ────────────────────────────────────────────────────


def test_skew_ohne_referenz_ist_none():
    assert _skew_perzentil({}) is None
    assert (
        _skew_perzentil({"risk_reversal": 0.1, "risk_reversal_reference": []}) is None
    )
    assert _skew_perzentil({"risk_reversal_reference": [0.1, 0.2]}) is None


def test_skew_perzentil_aus_rr(monkeypatch):
    import core.options_skew as options_skew

    gesehen = {}

    def _pct(rr, ref):
        gesehen.update(rr=rr, ref=ref)
        return 0.9

    monkeypatch.setattr(options_skew, "rr_percentile", _pct)
    state = {"risk_reversal": 0.1, "risk_reversal_reference": (0.0, 0.2)}
    assert _skew_perzentil(state) == 0.9
    assert gesehen == {"rr": 0.1, "ref": [0.0, 0.2]}


def test_skew_ausnahme_ist_none_und_loggt(monkeypatch, caplog):
    import core.options_skew as options_skew

    def _wirft(*a):
        raise RuntimeError("rr kaputt")

    monkeypatch.setattr(options_skew, "rr_percentile", _wirft)
    with caplog.at_level(logging.ERROR, logger=LOGGER):
        state = {"risk_reversal": 0.1, "risk_reversal_reference": [0.0]}
        assert _skew_perzentil(state) is None
    (record,) = [r for r in caplog.records if "#3199" in r.getMessage()]
    assert record.levelno == logging.ERROR and record.name == LOGGER


# ── Regime und VIX (#2958, #2980) ─────────────────────────────────────────────


def test_regime_vix_bestaetigt():
    assert _regime_felder({"vix": 25.0, "regime": "bull"}) == (
        {"vix_level": 25.0, "vix_confirmed": True, "market_regime": "bull"},
        "",
    )


@pytest.mark.parametrize("vix", [None, "kaputt"])
def test_regime_vix_unbestaetigt_stempelt_marker(vix):
    assert _regime_felder({"vix": vix}) == (
        {"vix_confirmed": False},
        VIX_UNCONFIRMED_MARKER,
    )


@pytest.mark.parametrize("regime", ["", 5, None])
def test_regime_leer_oder_ungueltig_setzt_kein_market_regime(regime):
    felder, _ = _regime_felder({"vix": 20.0, "regime": regime})
    assert "market_regime" not in felder


# ── Dirigent: ein Fehler in einem Schritt → kein Signal ───────────────────────


def test_fehler_in_einem_schritt_liefert_none_mit_warnung(monkeypatch, caplog):
    def _wirft(state):
        raise RuntimeError("schritt kaputt")

    monkeypatch.setattr(signal_bau, "_regime_felder", _wirft)
    state = {"symbol": "X", "ohlc": {"close": 1.0}}
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        assert _score_to_signal(state, 0.5, [_vote()]) is None
    (record,) = [
        r
        for r in caplog.records
        if "Signal-Erstellung fehlgeschlagen" in r.getMessage()
    ]
    assert record.levelno == logging.WARNING and record.name == LOGGER
    assert "schritt kaputt" in record.getMessage()
