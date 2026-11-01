"""Abnahme auf Epic-Ebene für #3738 — scharf geschaltet (#4049).

Jeder Schritt ruft die Größenregel, die die Sub-Issues gebaut haben
(``tests/architecture/regeln.py``), und prüft sie gegen den Stand von main (Plan #4049
§3, Option A). Die Gegenproben laufen in ``tmp_path`` mit den Schwellen aus dem echten
Vertrag — so zeigt jedes Szenario, dass die Regel anschlägt, und dass der Code sie hält.

Das fünfte Szenario (Verhalten) hat eine eigene Schrittdatei,
``test_epic_3738_verhalten.py``; hier sind deshalb nur die vier Struktur-Szenarien gebunden.
"""

from functools import lru_cache

import pytest
from pytest_bdd import given, scenario, then, when

from tests.architecture import regeln

pytestmark = [pytest.mark.vc0]

FEATURE = "../epic_3738.feature"

# Befund vom 28./30.09.2026: #3738 Abschnitt 3 und 4.1, docs/3738-arc-e6-gestalt/
# implementation_plan.md §3. Dateien unter ihrem Pfad von damals, Funktionen unter
# ihrem Namen — sie werden am heutigen Ort gesucht (G-4, G-7 haben einige verschoben).
BEFUND_DATEIEN = (
    "core/engine/api_routes.py",
    "core/engine/order_executor.py",
    "core/engine/trading_loop.py",
    "core/round_table/agents.py",
    "settings.py",
    "core/stock_specialist.py",
)
BEFUND_FUNKTIONEN = (
    "_execute_tenant_order",
    "_process_signal_event",
    "live_trading_loop",
    "calculate_position_size",
    "get_benchmark_equity",
    "_run_deconcentration_and_rotation_exits",
    "_run_for_symbol_impl",
    "_submit_order_safe",
)

STUFE_1 = "Stufe-1-Grenze"
SENKE = "Senke die Zahl"


@scenario(FEATURE, "Die Größenregel gilt blockierend und trägt die Stufe-2-Schwellen")
def test_die_groessenregel_gilt_blockierend():
    pass


@scenario(FEATURE, "Kein Rest über Stufe 1 ohne eingecheckte Begründung")
def test_kein_rest_ueber_stufe_1_ohne_begruendung():
    pass


@scenario(
    FEATURE, "Die im Befund genannten Einheiten liegen unter ihren Stufe-1-Zahlen"
)
def test_die_befund_einheiten_liegen_unter_stufe_1():
    pass


@scenario(FEATURE, "Die Ratsche kann nicht still zurückrollen")
def test_die_ratsche_rollt_nicht_still_zurueck():
    pass


@lru_cache(maxsize=1)
def _meldungen_am_code() -> tuple[str, ...]:
    return tuple(regeln.pruefe_groessen(regeln.PAKET, regeln.lade_vertrag()))


def _probe_vertrag(**ausnahmen) -> dict:
    """Der echte Abschnitt ``[groessen]``, nur Bereich und Ausnahmen für die Probe."""
    teil = dict(regeln.lade_vertrag()["groessen"])
    teil.update(bereiche=["."], ausnahmen_dateien={}, ausnahmen_funktionen={})
    teil.update(ausnahmen)
    return {"groessen": teil}


def _funktion(name: str, zeilen: int) -> str:
    """``def`` plus Rumpf, zusammen genau ``zeilen`` Zeilen."""
    return f"def {name}():\n" + "    x = 1\n" * (zeilen - 1)


# --- Szenario 1: Modus und Stufe-2-Schwellen ----------------------------------


@given(
    "der Architektur-Vertrag `ai_trading_bot/tests/architecture/vertrag.toml`",
    target_fixture="vertrag",
)
def schritt_1():
    return regeln.lade_vertrag()


@when("die Abnahme des Epics läuft", target_fixture="groessen")
def schritt_2(vertrag):
    return {"modus": vertrag["modus"]["groessen"], **vertrag["groessen"]}


@then("enthält der Abschnitt `[modus]` den Eintrag `groessen = 'blockieren'`")
def schritt_3(groessen):
    assert groessen["modus"] == "blockieren"


@then("die eingetragene Dateischwelle ist höchstens 1000 Zeilen")
def schritt_4(groessen):
    assert groessen["datei_schwelle"] <= 1000


@then("die eingetragene Funktionsschwelle ist höchstens 200 Zeilen")
def schritt_5(groessen):
    assert groessen["funktion_schwelle"] <= 200


# --- Szenario 2: Stufe 1 nur mit Begründung -----------------------------------


@given(
    "der gemessene Produktivcode unter `ai_trading_bot/` ohne Tests und Vendor",
    target_fixture="stufe_1_am_code",
)
def schritt_6():
    return [m for m in _meldungen_am_code() if STUFE_1 in m]


@when(
    "eine Datei über 2000 Zeilen oder eine Funktion über 400 Zeilen liegt",
    target_fixture="probe",
)
def schritt_7(tmp_path):
    teil = regeln.lade_vertrag()["groessen"]
    datei_zeilen = teil["stufe_1_datei_schwelle"] + 1
    funktion_zeilen = teil["stufe_1_funktion_schwelle"] + 1
    (tmp_path / "gross.py").write_text("x = 1\n" * datei_zeilen, encoding="utf-8")
    (tmp_path / "lang.py").write_text(
        "x = 0\n\n\n" + _funktion("lang", funktion_zeilen), encoding="utf-8"
    )
    return {
        "wurzel": tmp_path,
        "datei_zeilen": datei_zeilen,
        "funktion_zeilen": funktion_zeilen,
    }


@when("die zugehörige Vertragszeile kein gefülltes Feld `begruendung` trägt")
def schritt_8(probe):
    probe["vertrag"] = _probe_vertrag(
        ausnahmen_dateien={"gross.py": probe["datei_zeilen"]},
        ausnahmen_funktionen={
            "lang.py": {
                "lang": {"zeilen": probe["funktion_zeilen"], "begruendung": " "}
            }
        },
    )


@then("schlägt die Abnahme fehl und nennt Datei und Zeile")
def schritt_9(probe, stufe_1_am_code):
    meldungen = [
        m
        for m in regeln.pruefe_groessen(probe["wurzel"], probe["vertrag"])
        if STUFE_1 in m
    ]
    assert len(meldungen) == 2, meldungen
    text = "\n".join(meldungen)
    assert f"gross.py: {probe['datei_zeilen']} Zeilen" in text, text
    assert "lang.py:4 lang" in text, "Die Meldung nennt Datei und def-Zeile."
    assert stufe_1_am_code == [], "\n".join(stufe_1_am_code)


# --- Szenario 3: die Einheiten aus dem Befund ---------------------------------


@given(
    "die sechs Dateien über 2000 Zeilen und die acht Funktionen über 400 Zeilen aus dem Befund vom 28./30.09.2026",
    target_fixture="befund",
)
def schritt_10():
    assert len(BEFUND_DATEIEN) == 6 and len(BEFUND_FUNKTIONEN) == 8
    return {"dateien": BEFUND_DATEIEN, "funktionen": BEFUND_FUNKTIONEN}


@when("die Abnahme läuft", target_fixture="einheiten")
def schritt_11(befund):
    """Je Einheit (Bezeichnung, gemessen, Vertragseintrag, Grenze) — oder ein Befund,
    wenn sie nicht (eindeutig) auffindbar ist."""
    teil = regeln.lade_vertrag()["groessen"]
    dateien = {b.was: int(b.zusatz) for b in regeln.datei_groessen(regeln.PAKET, ".")}
    funktionen = regeln.funktions_groessen(regeln.PAKET, ".")
    einheiten, unauffindbar = [], []
    for datei in befund["dateien"]:
        if datei not in dateien:
            unauffindbar.append(f"{datei}: Datei nicht mehr auffindbar")
            continue
        eintrag = teil.get("ausnahmen_dateien", {}).get(datei)
        einheiten.append(
            (datei, dateien[datei], eintrag, teil["stufe_1_datei_schwelle"])
        )
    for name in befund["funktionen"]:
        treffer = [b for b in funktionen if b.was.rsplit(".", 1)[-1] == name]
        if len(treffer) != 1:
            orte = [f"{b.datei}::{b.was}" for b in treffer]
            unauffindbar.append(f"{name}: {len(treffer)} Fundstellen {orte}")
            continue
        (b,) = treffer
        eintrag = teil.get("ausnahmen_funktionen", {}).get(b.datei, {}).get(b.was)
        einheiten.append(
            (
                f"{b.datei}:{b.zeile} {b.was}",
                int(b.zusatz),
                eintrag,
                teil["stufe_1_funktion_schwelle"],
            )
        )
    return {"einheiten": einheiten, "unauffindbar": unauffindbar}


@then(
    "liegt jede dieser Einheiten unter ihrer Stufe-1-Zahl oder trägt eine begründete Vertragszeile"
)
def schritt_12(einheiten):
    befunde = list(einheiten["unauffindbar"])
    for wo, gemessen, eintrag, grenze in einheiten["einheiten"]:
        if gemessen <= grenze:
            continue
        if isinstance(eintrag, dict) and str(eintrag.get("begruendung", "")).strip():
            continue
        befunde.append(
            f"{wo}: {gemessen} Zeilen über der Stufe-1-Zahl {grenze}, ohne begruendung"
        )
    assert befunde == [], "\n".join(befunde)
    assert len(einheiten["einheiten"]) == 14


# --- Szenario 4: die Ratsche --------------------------------------------------


@given("eine Einheit mit eingetragener Obergrenze im Vertrag", target_fixture="ratsche")
def schritt_13(tmp_path):
    teil = regeln.lade_vertrag()["groessen"]
    datei_zeilen = teil["datei_schwelle"] + 20
    funktion_zeilen = teil["funktion_schwelle"] + 20
    (tmp_path / "ratsche.py").write_text(
        _funktion("lang", funktion_zeilen)
        + "y = 1\n" * (datei_zeilen - funktion_zeilen),
        encoding="utf-8",
    )
    ratsche = {
        "wurzel": tmp_path,
        "vertrag": _probe_vertrag(
            ausnahmen_dateien={"ratsche.py": datei_zeilen},
            ausnahmen_funktionen={"ratsche.py": {"lang": funktion_zeilen}},
        ),
    }
    assert regeln.pruefe_groessen(tmp_path, ratsche["vertrag"]) == []
    return ratsche


@when("ihr gemessener Wert unter die eingetragene Zahl fällt")
def schritt_14(ratsche):
    teil = ratsche["vertrag"]["groessen"]
    datei_zeilen = teil["datei_schwelle"] + 10
    funktion_zeilen = teil["funktion_schwelle"] + 10
    (ratsche["wurzel"] / "ratsche.py").write_text(
        _funktion("lang", funktion_zeilen)
        + "y = 1\n" * (datei_zeilen - funktion_zeilen),
        encoding="utf-8",
    )
    ratsche["gesenkt"] = {"datei": datei_zeilen, "funktion": funktion_zeilen}


@then("schlägt die Abnahme fehl, bis die Zahl im Vertrag mitgesenkt ist")
def schritt_15(ratsche):
    wurzel, vertrag, n = ratsche["wurzel"], ratsche["vertrag"], ratsche["gesenkt"]
    meldungen = regeln.pruefe_groessen(wurzel, vertrag)
    assert len(meldungen) == 2 and all(SENKE in m for m in meldungen), meldungen
    assert f"auf {n['datei']}" in meldungen[0] and "ratsche.py" in meldungen[0]
    assert f"auf {n['funktion']}" in meldungen[1] and " lang" in meldungen[1]

    vertrag["groessen"]["ausnahmen_dateien"]["ratsche.py"] = n["datei"]
    vertrag["groessen"]["ausnahmen_funktionen"]["ratsche.py"]["lang"] = n["funktion"]
    assert regeln.pruefe_groessen(wurzel, vertrag) == []

    am_code = [m for m in _meldungen_am_code() if SENKE in m]
    assert am_code == [], "\n".join(am_code)
