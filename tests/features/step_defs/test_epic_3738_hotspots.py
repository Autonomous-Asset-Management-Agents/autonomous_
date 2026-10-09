"""Abnahme auf Epic-Ebene für #3738 — die Hotspots aus dem Neuzuschnitt (H-A #4190).

Epic #3738 §8.2: „Jeder Hotspot liegt bei höchstens 800 Zeilen je Datei und 150 je
Funktion." Geprüft wird je Hotspot die **Familie**: die Ausgangsdatei plus jedes
Themen-Modul aus ihrer Schnitt-Entscheidung (Plan #4190 §2.2). Sonst erreichte ein
Hotspot das Ziel, indem er 1000 Zeilen in ein einziges neues Modul schiebt.

Die Familie steht an einer Stelle, ``scripts/mess_hotspots_nachher.py::HOTSPOT_FAMILIEN``;
derselbe Stand speist den Nachher-Messpunkt in ``HOTSPOT_KENNZAHLEN.md``. Die Werkzeuge
liegen unter ``scripts/``; diese Abnahme liest sie nur (Vorbild ``test_epic_3863.py``,
Airlock CLAUDE.md 5.7 gilt ``ai_trading_bot/core/``, nicht ``tests/``).

Gezählt wird nur mit ``regeln.datei_groessen`` und ``regeln.funktions_groessen``. Die
Gegenproben laufen in ``tmp_path`` und verlangen je genau einen Befund.
"""

import re
import sys
from functools import lru_cache
from pathlib import Path

import pytest
from pytest_bdd import given, scenario, then, when

from tests.architecture import regeln

pytestmark = [pytest.mark.vc0]

REPO = regeln.PAKET.parent
KENNZAHLEN = REPO / "docs" / "3738-arc-e6-gestalt" / "HOTSPOT_KENNZAHLEN.md"

#: Epic #3738 §8.2 — Ziel je Hotspot, nicht die Vertragsschwelle (dort gilt Stufe 2).
DATEI_GRENZE = 800
FUNKTION_GRENZE = 150
AUSGANGS_MESSPUNKT = "2026-10-06"
NACHHER_ANLASS = "#4190"
#: Begründungen der Hotspot-Umzüge: G-8b #4154, G-8c #4175, H-1 bis H-5 #4183–#4187.
HOTSPOT_ISSUES = re.compile(r"#(?:4154|4175|418[3-7])\b")

_MESSPUNKT = re.compile(r"^## Messpunkt (\d{4}-\d{2}-\d{2})")
_ANLASS = re.compile(r"Anlass: (.*)$")
_ZEILE = re.compile(r"^\| `([^`]+)` \| (\d+) \|")


@lru_cache(maxsize=1)
def _familien() -> dict[str, tuple[str, ...]]:
    skripte = str(REPO / "scripts")
    if skripte not in sys.path:
        sys.path.insert(0, skripte)
    import mess_hotspots_nachher

    return mess_hotspots_nachher.HOTSPOT_FAMILIEN


def _alle(familien: dict) -> list[str]:
    return [m for module in familien.values() for m in module]


@scenario(
    "../epic_3738.feature",
    "Die Hotspots aus dem Neuzuschnitt sind lesbar und ihre Kennzahlen erhoben",
)
def test_die_hotspots_sind_lesbar_und_gemessen():
    pass


# --- Prüfungen (je eine Befundliste) ------------------------------------------


def datei_befunde(wurzel: Path, familien: dict) -> list[str]:
    gemessen = {b.was: int(b.zusatz) for b in regeln.datei_groessen(wurzel, ".")}
    befunde = []
    for datei in _alle(familien):
        if datei not in gemessen:
            befunde.append(f"{datei}: Datei nicht auffindbar")
        elif gemessen[datei] > DATEI_GRENZE:
            befunde.append(f"{datei}: {gemessen[datei]} Zeilen > {DATEI_GRENZE}")
    return befunde


def funktions_befunde(wurzel: Path, familien: dict) -> list[str]:
    dateien = set(_alle(familien))
    return [
        f"{b.datei}:{b.zeile} {b.was}: {b.zusatz} Zeilen > {FUNKTION_GRENZE}"
        for b in regeln.funktions_groessen(wurzel, ".")
        if b.datei in dateien and int(b.zusatz) > FUNKTION_GRENZE
    ]


def _begruendungen(knoten, pfad: str):
    if isinstance(knoten, dict):
        for schluessel, wert in knoten.items():
            if schluessel == "begruendung":
                yield pfad, str(wert)
            else:
                yield from _begruendungen(wert, f"{pfad}.{schluessel}")


def vertrags_befunde(vertrag: dict, familien: dict) -> list[str]:
    teil = vertrag["groessen"]
    dateien = set(_alle(familien))
    befunde = [
        f"{abschnitt}: Ausnahme für {datei}"
        for abschnitt in ("ausnahmen_dateien", "ausnahmen_funktionen")
        for datei in teil.get(abschnitt, {})
        if datei in dateien
    ]
    befunde += [
        f"{wo}: Begründung nennt ein Hotspot-Issue: {text}"
        for wo, text in _begruendungen(teil, "groessen")
        if HOTSPOT_ISSUES.search(text)
    ]
    return befunde


def messpunkte(text: str) -> list[dict]:
    """Je Messpunkt Datum, Anlass und {Datei: Zeilen}, in der Reihenfolge des Dokuments."""
    punkte: list[dict] = []
    for zeile in text.splitlines():
        if m := _MESSPUNKT.match(zeile):
            punkte.append({"datum": m.group(1), "anlass": "", "zeilen": {}})
        elif punkte and (m := _ANLASS.search(zeile)):
            punkte[-1]["anlass"] = m.group(1)
        elif punkte and (m := _ZEILE.match(zeile)):
            punkte[-1]["zeilen"][m.group(1)] = int(m.group(2))
    return punkte


def kennzahlen_befunde(text: str, familien: dict) -> list[str]:
    punkte = messpunkte(text)
    vorher = [p for p in punkte if p["datum"] == AUSGANGS_MESSPUNKT]
    nachher = [p for p in punkte if NACHHER_ANLASS in p["anlass"]]
    if not vorher:
        return [f"kein Ausgangs-Messpunkt vom {AUSGANGS_MESSPUNKT}"]
    if not nachher:
        return [f"kein Nachher-Messpunkt mit Anlass {NACHHER_ANLASS}"]
    befunde = [
        f"Ausgangswert ohne Zeile für {d}"
        for d in familien
        if d not in vorher[0]["zeilen"]
    ]
    zeilen = nachher[-1]["zeilen"]
    for datei in _alle(familien):
        if datei not in zeilen:
            befunde.append(f"Nachher-Messpunkt ohne Zeile für {datei}")
        elif zeilen[datei] > DATEI_GRENZE:
            befunde.append(f"Nachher-Messpunkt: {datei} {zeilen[datei]} Zeilen")
    return befunde


# --- Szenario 6 ---------------------------------------------------------------


@given(
    "die fünf Hotspots aus Epic §8.2 mit ihren Themen-Modulen aus den Schnitt-Entscheidungen H-1 bis H-5",
    target_fixture="familien",
)
def schritt_1():
    familien = _familien()
    assert len(familien) == 5, sorted(familien)
    return familien


@when("die Hotspot-Abnahme des Epics läuft", target_fixture="befunde")
def schritt_2(familien):
    return {
        "dateien": datei_befunde(regeln.PAKET, familien),
        "funktionen": funktions_befunde(regeln.PAKET, familien),
        "vertrag": vertrags_befunde(regeln.lade_vertrag(), familien),
        "kennzahlen": kennzahlen_befunde(
            KENNZAHLEN.read_text(encoding="utf-8"), familien
        ),
    }


@then("liegt jede dieser Dateien bei höchstens 800 Zeilen")
def schritt_3(befunde):
    assert befunde["dateien"] == [], "\n".join(befunde["dateien"])


@then("liegt jede Funktion darin bei höchstens 150 Zeilen")
def schritt_4(befunde):
    assert befunde["funktionen"] == [], "\n".join(befunde["funktionen"])


@then(
    "trägt der Vertrag für keine dieser Dateien eine Ausnahme und keine Begründung aus H-1 bis H-5, G-8b oder G-8c"
)
def schritt_5(befunde):
    assert befunde["vertrag"] == [], "\n".join(befunde["vertrag"])


@then(
    "trägt `docs/3738-arc-e6-gestalt/HOTSPOT_KENNZAHLEN.md` den Ausgangswert vom 06.10.2026 und einen Nachher-Messpunkt für jede dieser Dateien"
)
def schritt_6(befunde):
    assert befunde["kennzahlen"] == [], "\n".join(befunde["kennzahlen"])


# --- Gegenproben: jede Prüfung schlägt an, mit genau einem Befund -------------

PROBE = {"core/a.py": ("core/a.py", "core/a_thema.py")}


def _baue(wurzel: Path, dateien: dict[str, str]) -> Path:
    for rel, quelle in dateien.items():
        pfad = wurzel / rel
        pfad.parent.mkdir(parents=True, exist_ok=True)
        pfad.write_text(quelle, encoding="utf-8")
    return wurzel


def _funktion(name: str, zeilen: int) -> str:
    """``def`` plus Rumpf, zusammen genau ``zeilen`` Zeilen."""
    return f"def {name}():\n" + "    x = 1\n" * (zeilen - 1)


def _kennzahlen(nachher: dict[str, int]) -> str:
    def punkt(datum: str, anlass: str, zeilen: dict[str, int]) -> str:
        tabelle = "".join(f"| `{d}` | {n} | 1.0k |\n" for d, n in zeilen.items())
        return f"## Messpunkt {datum} — x\n\nFenster: 60 Tage · Anlass: {anlass}\n\n{tabelle}\n"

    return punkt(
        AUSGANGS_MESSPUNKT, "Ausgangswert H-0 (#4182)", {"core/a.py": 900}
    ) + punkt("2026-10-09", "Nachher H-A (#4190)", nachher)


def test_gegenprobe_datei_801(tmp_path):
    wurzel = _baue(tmp_path, {"core/a.py": "x = 1\n" * 801, "core/a_thema.py": ""})
    assert datei_befunde(wurzel, PROBE) == ["core/a.py: 801 Zeilen > 800"]


def test_gegenprobe_funktion_151(tmp_path):
    wurzel = _baue(
        tmp_path, {"core/a.py": "", "core/a_thema.py": _funktion("lang", 151)}
    )
    befunde = funktions_befunde(wurzel, PROBE)
    assert befunde == ["core/a_thema.py:1 lang: 151 Zeilen > 150"]
    assert (
        funktions_befunde(
            _baue(tmp_path, {"core/a_thema.py": _funktion("lang", 150)}), PROBE
        )
        == []
    )


def test_gegenprobe_ausnahme_schluessel():
    vertrag = {"groessen": {"ausnahmen_dateien": {"core/a_thema.py": 900}}}
    assert vertrags_befunde(vertrag, PROBE) == [
        "ausnahmen_dateien: Ausnahme für core/a_thema.py"
    ]
    fremd = {
        "groessen": {
            "ausnahmen_dateien": {
                "core/kalt.py": {
                    "zeilen": 900,
                    "begruendung": "VORLÄUFIG bis H-3 (#4185)",
                }
            }
        }
    }
    (befund,) = vertrags_befunde(fremd, PROBE)
    assert "groessen.ausnahmen_dateien.core/kalt.py" in befund and "#4185" in befund


def test_gegenprobe_kennzahlen_ohne_nachher_zeile():
    vollstaendig = _kennzahlen({"core/a.py": 400, "core/a_thema.py": 300})
    assert kennzahlen_befunde(vollstaendig, PROBE) == []
    luecke = _kennzahlen({"core/a.py": 400})
    assert kennzahlen_befunde(luecke, PROBE) == [
        "Nachher-Messpunkt ohne Zeile für core/a_thema.py"
    ]


def test_gegenprobe_datei_unauffindbar(tmp_path):
    wurzel = _baue(tmp_path, {"core/a.py": "x = 1\n"})
    assert datei_befunde(wurzel, PROBE) == ["core/a_thema.py: Datei nicht auffindbar"]
