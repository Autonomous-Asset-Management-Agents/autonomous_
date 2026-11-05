"""#5044: Ein Stimmen-Store ganz ohne LSTM-Zeile gilt als „LSTM abgeschaltet" (Weg a).

Plan: ``docs/5044-e2e-abnahme-scharf-schalten-fuer-4625/implementation_plan.md`` §2 Punkte 1-3.
Fehlt die Stimme nur an einzelnen Tagen, bleibt das Gate fail-closed.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

LSTM, RL, MOMENTUM = "LSTMSignalAgent", "RLConfidenceAgent", "MomentumAgent"
TAG_1, TAG_2 = "2026-01-02", "2026-01-05"
KURSE = {TAG_1: {"A": 100.0}, TAG_2: {"A": 100.0}}
KAUF_OHNE_LSTM = {MOMENTUM: (0.9, 1.0, False)}
KAUF_MIT_LSTM = {LSTM: (0.9, 1.0, False), MOMENTUM: (0.9, 1.0, False)}


def _store(table):
    from core.sim.stimmen_sim import StimmenStore

    return StimmenStore(table)


def _lauf(table, **kw):
    from core.sim.stimmen_sim import StimmenSimStrategie, simuliere

    strategie = StimmenSimStrategie(name="s", pacing_aktiv=False, **kw)
    return simuliere(_store(table), KURSE, strategie, TAG_1, TAG_2)


def _gruende(result):
    return {(e["tag"], e["aktion"], e["grund"]) for e in result.entscheidungen}


def test_ohne_lstm_wahr_nur_ohne_jede_lstm_zeile():
    assert _store({("A", TAG_1): KAUF_OHNE_LSTM}).ohne_lstm() is True
    # Eine einzige Zeile reicht, auch als Enthaltung mit Gewicht 0.0.
    gemischt = {
        ("A", TAG_1): KAUF_OHNE_LSTM,
        ("A", TAG_2): {LSTM: (0.5, 0.0, False)},
    }
    assert _store(gemischt).ohne_lstm() is False


def test_simuliere_handelt_auf_store_ohne_lstm():
    r = _lauf({("A", TAG_1): KAUF_OHNE_LSTM, ("A", TAG_2): KAUF_OHNE_LSTM})

    assert [o["status"] for o in r.orders] == ["filled"]
    assert (TAG_1, "BUY", "konsens") in _gruende(r)
    assert r.ohne_lstm is True


def test_ergebnis_dict_traegt_ohne_lstm():
    from core.sim.stimmen_sim import StimmenSimStrategie, ergebnis_dict

    strategie = StimmenSimStrategie(name="s")
    ohne = _lauf({("A", TAG_1): KAUF_OHNE_LSTM})
    mit = _lauf({("A", TAG_1): KAUF_MIT_LSTM})

    assert ergebnis_dict(strategie, ohne)["ohne_lstm"] is True
    assert ergebnis_dict(strategie, mit)["ohne_lstm"] is False


def test_luecke_an_einem_tag_blockt_weiter():
    r = _lauf({("A", TAG_1): KAUF_OHNE_LSTM, ("A", TAG_2): KAUF_MIT_LSTM})

    assert r.ohne_lstm is False
    assert (TAG_1, "HOLD", "strict_ml") in _gruende(r)
    assert (TAG_2, "BUY", "konsens") in _gruende(r)


def test_requires_rl_blockt_weiter_ohne_rl_stimme():
    r = _lauf({("A", TAG_1): KAUF_OHNE_LSTM}, strict_ml_requires_rl=True)

    assert _gruende(r) == {(TAG_1, "HOLD", "strict_ml")}
    assert r.orders == []


def test_requires_rl_laesst_mit_rl_stimme_durch():
    stimmen = {MOMENTUM: (0.9, 1.0, False), RL: (0.9, 1.0, False)}

    r = _lauf({("A", TAG_1): stimmen}, strict_ml_requires_rl=True)

    assert (TAG_1, "BUY", "konsens") in _gruende(r)


def test_entscheide_blockt_ohne_lstm_im_standard():
    from core.round_table.base_agent import VoteResult
    from core.sim.stimmen_sim import StimmenSimStrategie, entscheide

    votes = [VoteResult(MOMENTUM, "", 0.9, 1.0, "", False)]
    strategie = StimmenSimStrategie(name="s")

    assert entscheide(votes, strategie).grund == "strict_ml"
    assert entscheide(votes, strategie, lstm_aus=True).aktion == "BUY"


def test_store_ohne_lstm_warnt_einmal(caplog):
    with caplog.at_level("WARNING", logger="core.sim.stimmen_sim"):
        _lauf({("A", TAG_1): KAUF_OHNE_LSTM, ("A", TAG_2): KAUF_OHNE_LSTM})

    treffer = [r for r in caplog.records if "ohne LSTM" in r.getMessage()]
    assert len(treffer) == 1
