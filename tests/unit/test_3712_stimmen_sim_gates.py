"""#3712 schnelle Sim: Slots, Pacing, Circuit Breaker, Strict-ML (Plan TDD-Schritt 2)."""

from __future__ import annotations

import itertools
from types import SimpleNamespace

import pytest

pytestmark = [pytest.mark.vc1]

LSTM = "LSTMSignalAgent"
KAUF = {LSTM: (0.9, 1.0, False)}
VERKAUF = {LSTM: (0.1, 1.0, False)}
NEUTRAL = {LSTM: (0.5, 1.0, False)}


def _lauf(tage, kurse, **kw):
    """tage: {tag: {symbol: stimmen}} — Store und Lauf ueber alle Tage."""
    from core.sim.stimmen_sim import StimmenSimStrategie, StimmenStore, simuliere

    table = {(sym, tag): st for tag, je in tage.items() for sym, st in je.items()}
    strategie = StimmenSimStrategie(name=kw.pop("name", "s"), **kw)
    return simuliere(StimmenStore(table), kurse, strategie, min(tage), max(tage))


def _gefuellt(result, seite):
    return [
        (o["tag"], o["symbol"])
        for o in result.orders
        if o["side"] == seite and o["status"] == "filled"
    ]


def test_slot_deckel_aus_strategie_nicht_global():
    import core.risk_manager as rm

    global_vorher = rm.effective_max_positions()
    kurse = {"2026-01-02": {"A": 100.0, "B": 50.0, "C": 20.0}}
    r = _lauf(
        {"2026-01-02": {"A": KAUF, "B": KAUF, "C": KAUF}},
        kurse,
        max_positions=2,
        pacing_aktiv=False,
    )

    assert len(_gefuellt(r, "BUY")) == 2
    assert len(r.end_positions) == 2
    assert [o["blocked_reason"] for o in r.orders if o["status"] == "blocked"] == [
        "slot_deckel"
    ]
    assert rm.effective_max_positions() == global_vorher


def test_pacing_nur_buy():
    kurse = {
        "2026-01-02": {"A": 100.0, "B": 50.0, "C": 20.0},
        "2026-01-05": {"A": 100.0, "B": 50.0, "C": 20.0},
    }
    r = _lauf(
        {
            "2026-01-02": {"A": KAUF, "B": KAUF, "C": KAUF},
            "2026-01-05": {"A": VERKAUF, "B": KAUF, "C": KAUF},
        },
        kurse,
        pacing_aktiv=True,
        buy_cooldown_minuten=30,
        max_buys_pro_stunde=2,
    )

    # Ein Zyklus je Tag: nach dem ersten BUY steht der Cooldown (0 min < 30 min).
    assert _gefuellt(r, "BUY") == [("2026-01-02", "A"), ("2026-01-05", "B")]
    assert _gefuellt(r, "SELL") == [("2026-01-05", "A")]
    gruende = {o["blocked_reason"] for o in r.orders if o["status"] == "blocked"}
    assert gruende == {"buy_pacing_cooldown"}


def test_portfolio_equity_circuit_breaker():
    kurse = {
        "2026-01-02": {"A": 100.0, "B": 100.0, "C": 100.0},
        "2026-01-05": {"A": 10.0, "B": 10.0, "C": 100.0},
    }
    r = _lauf(
        {
            "2026-01-02": {"A": KAUF, "B": KAUF},
            "2026-01-05": {"A": VERKAUF, "B": NEUTRAL, "C": KAUF},
        },
        kurse,
        pacing_aktiv=False,
        max_positions=3,
        max_drawdown_limit=0.07,
    )

    assert r.max_drawdown_pct < -7.0
    assert ("2026-01-05", "C") not in _gefuellt(r, "BUY")
    assert ("2026-01-05", "A") in _gefuellt(r, "SELL")
    blockiert = [o for o in r.orders if o["status"] == "blocked"]
    assert [(o["symbol"], o["blocked_reason"]) for o in blockiert] == [
        ("C", "circuit_breaker")
    ]


def test_strict_ml_ohne_config_kollision(monkeypatch):
    import config
    from core.round_table import gate_stufen
    from core.sim.stimmen_sim import (
        StimmenSimStrategie,
        entscheide,
        strict_ml_blockiert,
        tausche_gewichte,
    )

    # Drift-Waechter: die pure Funktion entscheidet wie das echte Gate, je Flag-Stand.
    for requires_rl, lstm, rl, aus in itertools.product([True, False], repeat=4):
        monkeypatch.setattr(
            config,
            "get_config",
            lambda r=requires_rl: SimpleNamespace(GATEKEEPER_STRICT_ML_REQUIRES_RL=r),
        )
        echt = gate_stufen._strict_ml_gate_blocks(lstm, rl, lstm_disabled=aus)
        assert strict_ml_blockiert(lstm, rl, aus, requires_rl) is echt
    monkeypatch.undo()

    votes = tausche_gewichte(KAUF, {})
    streng = StimmenSimStrategie(name="streng", strict_ml_requires_rl=True)
    locker = StimmenSimStrategie(name="locker", strict_ml_requires_rl=False)
    assert (entscheide(votes, streng).aktion, entscheide(votes, streng).grund) == (
        "HOLD",
        "strict_ml",
    )
    assert entscheide(votes, locker).aktion == "BUY"
    # Ohne gueltige LSTM-Stimme blockt das Gate immer — auch einen SELL (wie runner.py).
    ohne_lstm = tausche_gewichte({"MomentumAgent": (0.1, 1.0, False)}, {})
    assert entscheide(ohne_lstm, locker).grund == "strict_ml"
