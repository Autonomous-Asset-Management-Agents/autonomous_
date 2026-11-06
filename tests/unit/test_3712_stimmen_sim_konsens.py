"""#3712 schnelle Sim: Gewichte, Enthaltungs-Schutz, Schwellen, Veto (Plan TDD-Schritt 1)."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.vc1]

LSTM = "LSTMSignalAgent"
MOM = "MomentumAgent"


def _strategie(**kw):
    from core.sim.stimmen_sim import StimmenSimStrategie

    return StimmenSimStrategie(name=kw.pop("name", "s"), **kw)


def test_gewichte_ersetzen_scores_bleiben():
    from core.analysis.attribution.metrics import replicate_consensus
    from core.sim.stimmen_sim import tausche_gewichte

    stimmen = {LSTM: (0.8, 1.0, False), MOM: (0.4, 1.0, False)}
    a = tausche_gewichte(stimmen, {LSTM: 3.0, MOM: 1.0})
    b = tausche_gewichte(stimmen, {LSTM: 1.0, MOM: 3.0})

    assert [v.score for v in a] == [v.score for v in b] == [0.8, 0.4]
    assert [v.weight for v in a] == [3.0, 1.0]
    assert replicate_consensus(a) == pytest.approx((0.8 * 3 + 0.4) / 4)
    assert replicate_consensus(b) == pytest.approx((0.8 + 0.4 * 3) / 4)
    # Ein Agent, den die Strategie nicht nennt, behaelt sein Gewicht.
    assert tausche_gewichte({LSTM: (0.8, 0.7, False)}, {})[0].weight == 0.7


def test_enthaltungs_schutz_null_bleibt_null():
    from core.analysis.attribution.metrics import replicate_consensus
    from core.sim.stimmen_sim import tausche_gewichte

    stimmen = {LSTM: (0.8, 1.0, False), MOM: (0.1, 0.0, False)}
    votes = tausche_gewichte(stimmen, {LSTM: 1.0, MOM: 5.0})

    assert {v.agent_name: v.weight for v in votes}[MOM] == 0.0
    assert replicate_consensus(votes) == pytest.approx(0.8)


def test_dynamische_schwellen_ohne_modul_mutation():
    import core.analysis.attribution.metrics as metrics
    import core.round_table.consensus as consensus
    from core.sim.stimmen_sim import entscheide, tausche_gewichte

    vorher = (consensus.SIGNAL_BUY_THRESHOLD, metrics.SIGNAL_BUY_THRESHOLD)
    votes = tausche_gewichte({LSTM: (0.6, 1.0, False)}, {})

    assert entscheide(votes, _strategie(buy_threshold=0.55)).aktion == "BUY"
    assert entscheide(votes, _strategie(buy_threshold=0.65)).aktion == "HOLD"
    assert entscheide(votes, _strategie(sell_threshold=0.62)).aktion == "SELL"
    assert (consensus.SIGNAL_BUY_THRESHOLD, metrics.SIGNAL_BUY_THRESHOLD) == vorher


def test_veto_blockiert_ausser_sell():
    from core.sim.stimmen_sim import entscheide, tausche_gewichte

    kauf = tausche_gewichte(
        {LSTM: (0.9, 1.0, False), "DrawdownGuardAgent": (0.1, 1.0, True)}, {}
    )
    verkauf = tausche_gewichte(
        {LSTM: (0.1, 1.0, False), "DrawdownGuardAgent": (0.1, 1.0, True)}, {}
    )

    e = entscheide(kauf, _strategie())
    assert (e.aktion, e.grund) == ("HOLD", "agent_veto")
    assert entscheide(verkauf, _strategie()).aktion == "SELL"


def test_keine_richtungsstimme_kein_trade():
    from core.sim.stimmen_sim import entscheide, tausche_gewichte

    votes = tausche_gewichte(
        {"RegimeDetectionAgent": (0.2, 1.0, False), LSTM: (0.1, 0.0, False)}, {}
    )
    e = entscheide(votes, _strategie())
    assert (e.aktion, e.grund) == ("HOLD", "keine_richtungsstimme")
