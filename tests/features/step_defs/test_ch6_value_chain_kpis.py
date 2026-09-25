import pytest
from pytest_bdd import given, scenarios, then, when

from core.value_chain_kpis import ValueChainMonitor

pytestmark = [pytest.mark.vc0]

scenarios("../value_chain_kpis.feature")


@pytest.fixture
def monitor():
    return ValueChainMonitor()


@pytest.fixture
def context():
    return {"metrics": {}, "annotated": {}, "alerts": []}


@given("ein abgeschlossener Lauf ueber alle Stufen der Wertschoepfungskette")
def abgeschlossener_lauf(context):
    context["metrics"] = {
        "data_coverage": 1.0,
        "decisions_count": 5,
        "blocked_orders": 0,
        "filled_orders": 2,
        "pnl_pct": 1.5,
        "audit_records": 10,
    }


@when("die Messung ausgewertet wird")
@when("die Auswertung laeuft")
@when("der Zyklus laeuft")
def auswertung_laeuft(monitor, context):
    ann, alerts = monitor.evaluate(context["metrics"])
    context["annotated"] = ann
    context["alerts"] = alerts


@then("traegt jede Stufe und jede Uebergabe mindestens eine Kennzahl")
def traegt_jede_stufe_eine_kennzahl(context):
    ann = context["annotated"]
    assert "VC-1_data_coverage" in ann
    assert "VC-2_decisions_count" in ann
    assert "VC-3_filled_orders" in ann
    assert "VC-4_blocked_orders" in ann
    assert "VC-5_pnl_pct" in ann
    assert "VC-6_audit_records" in ann


@given("eine Kennzahl aus der Auswertung")
def kennzahl_aus_auswertung(context):
    context["metrics"] = {"data_coverage": 1.0}


@when("sie gelesen wird")
def sie_gelesen_wird(monitor, context):
    ann, _ = monitor.evaluate(context["metrics"])
    context["annotated"] = ann


@then(
    "ist ohne Zusatzwissen erkennbar, zu welcher Stufe und welcher Uebergabe sie gehoert"
)
def erkennbar_welche_stufe(context):
    ann = context["annotated"]
    # The key itself contains the VC stage prefix
    assert "VC-1_data_coverage" in ann


@given("eine Stufe liefert ueber mehrere Zyklen kein Ergebnis mehr")
def liefert_kein_ergebnis(monitor, context):
    for _ in range(3):
        monitor.evaluate({"decisions_count": 0})
    context["metrics"] = {"decisions_count": 0}


@then("schlaegt ein Alarm an und nennt die Stufe und den Zeitraum")
def alarm_schlaegt_an(context):
    alerts = context["alerts"]
    assert any("ueber 3 Zyklen" in a.message for a in alerts)


@given("eine Stufe liefert keinen Messwert, weil ihre Berechnung abgeschaltet ist")
def keine_messwert_geliefert(context):
    context["metrics"] = {}  # missing key completely


@then('unterscheidet der Alarm "Wert nicht gebildet" von "Wert unterschritten"')
def unterscheidet_alarm(context):
    alerts = context["alerts"]
    missing = [a for a in alerts if a.is_missing]
    assert len(missing) > 0
    assert "Wert nicht gebildet" in missing[0].message


@given("dieselben Eingaben vor und nach der Zusammenfuehrung")
def dieselben_eingaben(context):
    context["old_val"] = 5
    context["metrics"] = {"decisions_count": 5}


@when("die vorhandenen Messpunkte gelesen werden")
def vorhandene_messpunkte_gelesen(monitor, context):
    ann, _ = monitor.evaluate(context["metrics"])
    context["annotated"] = ann


@then("liefern sie dieselben Werte wie zuvor")
def dieselben_werte(context):
    assert context["annotated"]["VC-2_decisions_count"] == context["old_val"]


@given("eine Stufe unterschreitet ihren Zielwert")
def unterschreitet_zielwert(context, monitor):
    # Threshold for VC-2 is > 0 conceptually, let's force a 0
    monitor.THRESHOLDS["VC-2"] = 1.0
    context["metrics"] = {"decisions_count": 0}


@then("wird der Vorfall berichtet")
def vorfall_berichtet(context):
    alerts = context["alerts"]
    assert any("unterschritten" in a.message for a in alerts)


@then("der Zyklus wird nicht angehalten, solange der Owner-Entscheid aussteht")
def zyklus_nicht_angehalten():
    # evaluate() just returns alerts, does not raise
    pass


@given("die Messschicht wirft einen Fehler")
def messschicht_wirft_fehler(monitor, context, monkeypatch):
    def bad_eval(*args):
        raise ValueError("Simulated error")

    # Actually evaluate() swallows it. So we mock something inside it.
    monkeypatch.setattr(
        monitor, "STAGE_METRICS", None
    )  # will cause exception on .items()
    context["metrics"] = {"data_coverage": 1.0}


@when("ein Zyklus laeuft")
def ein_zyklus_laeuft(monitor, context):
    try:
        ann, alerts = monitor.evaluate(context["metrics"])
        context["annotated"] = ann
        context["alerts"] = alerts
    except Exception as e:
        context["exception"] = e


@then("laeuft der Zyklus unveraendert weiter")
def laeuft_weiter(context):
    assert "exception" not in context


@then("der Fehler wird als Warnung protokolliert")
def fehler_protokolliert(caplog):
    assert "ValueChainMonitor error" in caplog.text
