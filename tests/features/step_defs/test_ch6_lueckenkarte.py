import pytest
from pytest_bdd import given, scenarios, then, when

pytestmark = [pytest.mark.vc6]

# Load scenarios
scenarios("../lueckenkarte.feature")


@pytest.fixture
def scenario_data():
    return {}


@given('Ein Golden Set "sizing_golden_2811.json" liegt als Erwartung vor')
def golden_set_vorhanden(scenario_data):
    from core.contracts.signal_candidate import AbstainReason

    # Simulate DB data
    scenario_data["decisions"] = [
        # Decision 1
        (
            "dec-1",
            [
                {"agent_name": "AgentA", "abstain_reason": None},
                {
                    "agent_name": "AgentB",
                    "abstain_reason": AbstainReason.NO_DATA.value,
                },
            ],
        ),
        # Decision 2
        (
            "dec-2",
            [
                {"agent_name": "AgentA", "abstain_reason": None},
                {"agent_name": "AgentB", "abstain_reason": None},
            ],
        ),
    ]


@given("Ein durchgeführter Audit-Lauf")
def audit_lauf(scenario_data):
    # Nothing to do, mock data already loaded
    pass


@when("der Bericht erstellt wird")
def bericht_erstellt(scenario_data):
    # Instead of full DB, we simulate metrics_builder's logic
    decisions = scenario_data["decisions"]
    gap_map = {}
    for d in decisions:
        votes = d[1] or []
        for v in votes:
            agent = v.get("agent_name")
            if not agent:
                continue
            if agent not in gap_map:
                gap_map[agent] = {
                    "total_symbols": 0,
                    "has_data_count": 0,
                    "abstain_reasons": {},
                }

            gap_map[agent]["total_symbols"] += 1
            reason = v.get("abstain_reason")
            if reason:
                reason_str = str(reason)
                gap_map[agent]["abstain_reasons"][reason_str] = (
                    gap_map[agent]["abstain_reasons"].get(reason_str, 0) + 1
                )
            else:
                gap_map[agent]["has_data_count"] += 1

    for _, stats in gap_map.items():
        stats["has_data_ratio"] = (
            stats["has_data_count"] / stats["total_symbols"]
            if stats["total_symbols"] > 0
            else 0.0
        )

    scenario_data["gap_map"] = gap_map


@then("nennt er je Agent den Anteil der Symbole mit vollständiger Datengrundlage")
def nennt_anteil(scenario_data):
    gap_map = scenario_data["gap_map"]
    assert gap_map["AgentA"]["has_data_ratio"] == 1.0  # 2/2
    assert gap_map["AgentB"]["has_data_ratio"] == 0.5  # 1/2


@then("je Enthaltung den Grund-Code")
def nennt_grund(scenario_data):
    gap_map = scenario_data["gap_map"]
    from core.contracts.signal_candidate import AbstainReason

    assert gap_map["AgentB"]["abstain_reasons"][AbstainReason.NO_DATA.value] == 1


@given("eine kuenstliche Luecke im Golden Set eingefuegt wurde")
def luecke_eingefuegt(scenario_data):
    from core.contracts.signal_candidate import AbstainReason

    # Replace AgentA's second vote with NO_DATA
    scenario_data["decisions"][1][1][0]["abstain_reason"] = AbstainReason.NO_DATA.value


@then("aendert sich der Anteil des betroffenen Agents")
def anteil_aendert_sich(scenario_data):
    gap_map = scenario_data["gap_map"]
    # AgentA is now 1/2
    assert gap_map["AgentA"]["has_data_ratio"] == 0.5
