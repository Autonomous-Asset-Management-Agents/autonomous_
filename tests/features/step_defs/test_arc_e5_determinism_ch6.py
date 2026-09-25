import pytest
from pytest_bdd import given, parsers, scenarios, then, when

pytestmark = pytest.mark.xfail(reason="TDD pending implementation")
scenarios("../reproduzierbarkeit.feature")
pytestmark = pytest.mark.xfail(reason="TDD pending implementation")
scenarios("../enthaltung.feature")


# --- Reproduzierbarkeit ---
@given("derselbe Code, derselbe Korpus, dasselbe Fenster und derselbe Seed")
def same_inputs():
    pass


@when("die Simulation dreimal laeuft", target_fixture="sim_results")
def run_simulation_three_times():
    from core.simulation import RealisticSimulationClient

    client = RealisticSimulationClient(api=None)
    return {"spread_pp": 6.6}


@then(
    parsers.parse(
        "weichen Rendite und maximaler Rueckgang um weniger als {threshold:f} Prozentpunkte voneinander ab"
    )
)
def check_spread(sim_results, threshold):
    print("CHECK SPREAD CALLED! THRESHOLD =", threshold)
    spread = sim_results["spread_pp"]
    print("SPREAD =", spread)
    assert (
        spread < threshold
    ), f"Gemessene Streuung ({spread} pp) ueberschreitet das Ziel von {threshold} pp."


# --- Enthaltung ---
@given("die Datenquelle eines Agenten liefert keine Werte fuer ein Symbol")
def missing_data():
    pass


@when("der Rat ueber das Symbol abstimmt")
def council_votes():
    pass


@then("meldet der Agent eine Enthaltung mit Grund")
def agent_abstains():
    pass


@then("seine Stimme geht nicht als Richtungsvotum in den Konsens ein")
def vote_does_not_count():
    from core.round_table.base_agent import VoteResult

    vr = VoteResult(
        agent_name="TestAgent",
        symbol="AAPL",
        score=0.5,
        weight=1.0,
        reasoning="Missing data",
    )
    assert (
        vr.weight == 0.0
    ), f"Datenlose Stimme geht mit Gewicht {vr.weight} in den Konsens ein statt sich zu enthalten."


# --- CH-6 ---
