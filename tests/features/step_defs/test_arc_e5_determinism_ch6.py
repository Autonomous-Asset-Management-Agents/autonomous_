"""ARC-E5: Enthaltung am echten Pfad, Reproduzierbarkeit ehrlich ausgewiesen (#3747).

**Was diese Datei vorher tat** (#3402/#3403, bis 28.09.2026)::

    pytestmark = pytest.mark.xfail(reason="TDD pending implementation")

    @when("die Simulation dreimal laeuft", target_fixture="sim_results")
    def run_simulation_three_times():
        client = RealisticSimulationClient(api=None)
        return {"spread_pp": 6.6}          # es laeuft keine Simulation

    @then("seine Stimme geht nicht als Richtungsvotum in den Konsens ein")
    def vote_does_not_count():
        vr = VoteResult(..., weight=1.0)   # der Test baut das Objekt selbst
        assert vr.weight == 0.0            # und prueft, dass es anders ist

Lauf: ``3 xfailed``. Der Schritt „der Rat stimmt ab" war ein leeres ``pass``, der
CH-6-Abschnitt bestand aus einem Kommentar.

**Was sie jetzt tut.** Die Enthaltung wird am **echten** Pfad geprueft:

* ``core.round_table.agents.DrawdownGuardAgent.vote`` mit ungueltigem Kursband
  (``high <= 0``) — der Agent entscheidet selbst, ob er sich enthaelt (agents.py:426,
  §5.6 / ADR-R09).
* ``core.round_table.consensus.ConsensusEngine.aggregate`` — der echte Konsens, einmal
  mit und einmal ohne die Enthaltung.
* ``core.contracts.signal_candidate.SignalCandidate`` — der Validator aus #3404, der
  Score **und** Enthaltungsgrund gemeinsam abweist.

Gefahren wird damit die **Naht Agent → Konsens**, nicht der ganze LangGraph-Zyklus. Was
diese Datei also nicht beweist: dass der Zyklus den Agenten auch aufruft. Das deckt der
Nahttest ``test_vc3_execution_nahttest.py`` ab.

**Warum die Reproduzierbarkeit uebersprungen wird statt ``xfail``.** Das Szenario
verlangt *drei* Simulationslaeufe ueber denselben Korpus; ein Lauf dauert Minuten bis
Stunden — das ist keine Arbeit fuer eine Testsuite. Der Sachstand ist ausserdem belegt:
``docs/1_architecture_and_adr/TARGET_ARCHITECTURE.md`` §8 haelt seit dem 16.09.2026 fest,
dass die Rendite ueber drei Laeufe bei identischem Code, Korpus und Fenster um **6,6
Prozentpunkte** streute. Die Zusage von 0,5 pp ist damit **nachweislich nicht erfuellt**,
und kein Test in dieser Suite kann das aendern. Ein ``xfail`` mit einer im Test
hinterlegten Zahl verdeckt das; ein ``skip`` mit Begruendung zeigt es.
"""

import asyncio

import pytest
from pytest_bdd import given, scenario, scenarios, then, when

from core.contracts.signal_candidate import AbstainReason, SignalCandidate
from core.round_table.agents import DrawdownGuardAgent
from core.round_table.consensus import ConsensusEngine

pytestmark = [pytest.mark.vc1]

_REPRODUZIERBARKEIT_GRUND = (
    "Drei Simulationslaeufe ueber denselben Korpus dauern Minuten bis Stunden — keine "
    "Arbeit fuer eine Testsuite. Belegter Stand: TARGET_ARCHITECTURE.md §8, gemessen am "
    "2026-09-16 — die Rendite streute ueber drei Laeufe bei identischem Code, Korpus und "
    "Fenster um 6,6 Prozentpunkte; die Zusage von 0,5 pp ist nicht erfuellt. "
    "Owner-Entscheidung (#3747): eigener Nachtlauf mit Bericht, oder die Zusage faellt. "
    "Bis dahin wird hier nichts Gruenes gemeldet."
)


@pytest.mark.skip(reason=_REPRODUZIERBARKEIT_GRUND)
@scenario("../reproduzierbarkeit.feature", "Reproduzierbarer Lauf")
def test_reproduzierbarer_lauf():
    """Uebersprungen mit Begruendung — siehe Modulkopf."""


scenarios("../reproduzierbarkeit.feature")
scenarios("../enthaltung.feature")


def _zustand_ohne_kursband(symbol: str = "AAPL") -> dict:
    """Ein Symbol, dessen Kursband unbrauchbar ist (high <= 0)."""
    return {
        "symbol": symbol,
        "ohlc": {"open": 0.0, "high": 0.0, "low": 0.0, "close": 0.0, "volume": 0.0},
        "market_data_keys": [],
        "current_time": "2026-09-16T09:35:00Z",
        "signal": None,
        "error": None,
        "round_table_scores": None,
        "consensus_ranking": None,
        "ml": None,
    }


# ---------------------------------------------------------------------------
# Enthaltung statt Rauschen — echter Agent, echter Konsens
# ---------------------------------------------------------------------------


@given(
    "die Datenquelle eines Agenten liefert keine Werte fuer ein Symbol",
    target_fixture="zustand",
)
def _kein_kursband():
    return _zustand_ohne_kursband()


@when("der Rat ueber das Symbol abstimmt", target_fixture="abstimmung")
def _rat_stimmt_ab(zustand):
    enthaltung = asyncio.run(DrawdownGuardAgent().vote(zustand))
    bewertend = SignalCandidate(
        agent_name="LSTMSignalAgent",
        symbol=zustand["symbol"],
        weight=1.0,
        score=0.80,
        reasoning="Kursreihe vollstaendig",
        data_source="lstm",
    )
    konsens = ConsensusEngine()
    return {
        "enthaltung": enthaltung,
        "mit": konsens.aggregate([bewertend, enthaltung]),
        "ohne": konsens.aggregate([bewertend]),
    }


@then("meldet der Agent eine Enthaltung mit Grund")
def _enthaltung_mit_grund(abstimmung):
    kandidat = abstimmung["enthaltung"]
    assert kandidat.abstain_reason == AbstainReason.NO_DATA, (
        f"Der Agent hat sich nicht enthalten, sondern {kandidat.abstain_reason!r} "
        f"gemeldet (score={kandidat.score!r}). Ohne Kursband darf er kein "
        "Richtungsvotum abgeben (§5.6)."
    )
    assert kandidat.reasoning and kandidat.reasoning.strip(), (
        "Die Enthaltung traegt keine Begruendung — ein Leser kann dann nicht "
        "unterscheiden, ob Daten fehlen oder der Agent abgeschaltet ist."
    )


@then("seine Stimme geht nicht als Richtungsvotum in den Konsens ein")
def _kein_richtungsvotum(abstimmung):
    kandidat = abstimmung["enthaltung"]
    assert kandidat.score is None, (
        f"Die Enthaltung traegt einen Score ({kandidat.score!r}) — genau der "
        "Ersatzwert, den ARC-E5.3 beseitigen sollte."
    )
    assert kandidat.weight == 0.0, (
        f"Die Enthaltung traegt Gewicht {kandidat.weight!r} und verduennt damit den "
        "Konsens in Richtung neutral."
    )


@then("der Konsens ist identisch mit dem Konsens ohne diese Stimme")
def _konsens_unveraendert(abstimmung):
    assert abstimmung["mit"] == abstimmung["ohne"], (
        f"Der Konsens aendert sich durch die Enthaltung: {abstimmung['ohne']} ohne, "
        f"{abstimmung['mit']} mit. Eine Enthaltung muss ausgeschlossen werden "
        "(consensus.py:305-317), nicht eingerechnet."
    )


# ---------------------------------------------------------------------------
# Der Vertrag aus #3404: Score und Enthaltung schliessen sich aus
# ---------------------------------------------------------------------------


@given("eine Stimme mit Score und Enthaltungsgrund", target_fixture="stimmdaten")
def _stimme_mit_beidem():
    return {
        "agent_name": "TestAgent",
        "symbol": "AAPL",
        "weight": 1.0,
        "score": 0.5,
        "abstain_reason": AbstainReason.NO_DATA,
        "reasoning": "beides gesetzt",
    }


@when("sie gebaut wird", target_fixture="bauversuch")
def _bauversuch(stimmdaten):
    try:
        return {"kandidat": SignalCandidate(**stimmdaten), "fehler": None}
    except Exception as exc:  # pydantic ValidationError
        return {"kandidat": None, "fehler": exc}


@then("wird sie abgewiesen")
def _wird_abgewiesen(bauversuch):
    assert bauversuch["fehler"] is not None, (
        "SignalCandidate hat Score UND Enthaltungsgrund angenommen. Dann ist nicht "
        "entscheidbar, ob die Stimme zaehlt — der Validator aus #3404 ist tot."
    )
