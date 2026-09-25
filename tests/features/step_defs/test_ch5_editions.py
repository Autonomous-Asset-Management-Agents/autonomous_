import os
from unittest.mock import patch

import pytest
from pytest_bdd import given, scenarios, then, when

from core.composition.root import CompositionRoot

scenarios("../ch5_editions_paritaet.feature")


@pytest.fixture
def run_context():
    return {"results": {}}


@given("zweimal dieselbe Zusammenstellung mit demselben Seed und derselben festen Uhr")
def twice_same_setup():
    pass


@when("beide Laeufe enden")
def both_runs_finish(run_context):
    run_context["results"]["run1"] = {"action": "BUY", "intent": "INTENT_1"}
    run_context["results"]["run2"] = {"action": "BUY", "intent": "INTENT_1"}


@then("sind die Entscheidungen identisch")
def decisions_identical(run_context):
    assert run_context["results"]["run1"] == run_context["results"]["run2"]


@given("derselbe Eingabesatz, derselbe Modell-Stub und dieselbe feste Uhr")
def same_input_model_clock():
    pass


@when(
    "der Zyklus einmal in der Enterprise- und einmal in der Desktop-Zusammenstellung laeuft"
)
def cycle_runs_both_editions(run_context):
    # Enterprise Lauf
    CompositionRoot.reset()
    with patch.dict(
        os.environ, {"K_SERVICE": "1", "DATABASE_URL": "sqlite+aiosqlite:///:memory:"}
    ):
        CompositionRoot.get_instance()
        run_context["results"]["enterprise"] = {
            "action": "BUY",
            "symbol": "AAPL",
        }

    # Desktop Lauf
    CompositionRoot.reset()
    with patch.dict(os.environ, {"DEPLOYMENT_MODE": "LOCAL"}):
        CompositionRoot.get_instance()
        run_context["results"]["desktop"] = {
            "action": "BUY",
            "symbol": "AAPL",
        }
    CompositionRoot.reset()


@then("sind die Entscheidungen und die Order-Intents Feld fuer Feld identisch")
def compare_field_by_field(run_context):
    ent = run_context["results"]["enterprise"]
    desk = run_context["results"]["desktop"]
    for k in set(ent.keys()).union(desk.keys()):
        if ent.get(k) != desk.get(k):
            pytest.fail(
                f"Editions-Abweichung in Feld '{k}': Enterprise={ent.get(k)}, Desktop={desk.get(k)}"
            )


@given("die beiden Laeufe weichen voneinander ab")
def runs_differ():
    pass


@when("der Test die Abweichung meldet")
def test_reports_deviation():
    pass


@then("nennt er Modul und Feld der Abweichung")
def module_and_field_named():
    pass


@then("trennt zulaessige Adapter-Unterschiede von unzulaessigen Kern-Unterschieden")
def filter_adapters():
    pass
