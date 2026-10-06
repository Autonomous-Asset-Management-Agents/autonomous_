"""Abnahme auf Epic-Ebene für #3863 — scharf geschaltet (#4047).

Jeder Schritt ruft die Module, die die Sub-Issues gebaut haben, und prüft sie gegen
den Stand von main (Plan #4047 §3, Option A). Ersetzt wird nur, was außerhalb des
Repos liegt: der `PATH` des Runners (Schritt 3) und die Actions-API (Schritt 6).

Die Werkzeuge liegen unter `scripts/`. Diese Abnahme liest sie nur; der Airlock
(CLAUDE.md 5.7) gilt der Finanzlogik unter `ai_trading_bot/core/`, nicht `tests/`.
Der `sys.path`-Eingriff steht deshalb nur in dieser Datei.

`WORKFLOW_VERZEICHNIS` biegt die Workflow-Prüfungen auf eine Kopie um — so ist die
Gegenprobe gelaufen (Walkthrough PR_4047).
"""

import os
import re
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml
from pytest_bdd import given, scenarios, then, when

scenarios("../epic_3863.feature")

pytestmark = [pytest.mark.vc0]

REPO = Path(__file__).resolve().parents[4]
WORKFLOWS = Path(
    os.environ.get("WORKFLOW_VERZEICHNIS") or REPO / ".github" / "workflows"
)

# Muster wie scripts/tests/test_workflow_kernkommando.py:32-33.
NACKTES_AGY = re.compile(r"^\s*agy\s", re.M)
MANTEL = re.compile(r"^\s*python scripts/agy_lauf\.py (?P<profil>\w+)\s*$", re.M)

# Ein Tor im selben Lauf: Schritt oder Job hängt an der Ausgabe eines anderen.
TOR_IM_LAUF = re.compile(r"(steps|needs)\.[\w-]+\.outputs\.")

# Läufer, die maker_tor.yml, implementer_tor.yml und antigravity_tor.yml anstoßen.
ARBEITER = {
    "maker.yml",
    "implementer.yml",
    "agy_plan_review.yml",
    "agy_code_review.yml",
    "agy_auto_merge.yml",
}


def _lade(pfad: Path) -> dict:
    return yaml.safe_load(pfad.read_text(encoding="utf-8")) or {}


def _ausloeser(daten: dict) -> dict:
    # PyYAML liest den Schlüssel `on` als Wahrheitswert.
    wert = daten.get("on", daten.get(True))
    return wert if isinstance(wert, dict) else {}


def _runner_labels(runs_on) -> set[str]:
    if isinstance(runs_on, str):
        return {runs_on}
    if isinstance(runs_on, list):
        return {str(x) for x in runs_on}
    return set()


def _ohne_kernkommando(agy_lauf) -> list[str]:
    """agy_*-Workflows, die nicht genau den Mantel mit ihrem Profil rufen."""
    befunde = []
    dateien = sorted(WORKFLOWS.glob("agy_*.yml"))
    if not dateien:
        return [f"keine agy_*.yml unter {WORKFLOWS} — die Prüfung wäre leer"]
    for pfad in dateien:
        text = pfad.read_text(encoding="utf-8")
        profil = pfad.stem[len("agy_") :]
        if NACKTES_AGY.search(text):
            befunde.append(f"{pfad.name}: ruft nacktes `agy`")
        if MANTEL.findall(text) != [profil] or profil not in agy_lauf.PROFILE:
            befunde.append(f"{pfad.name}: ruft nicht `agy_lauf.py {profil}`")

    def _kein_kommando(*_a, **_k):
        raise AssertionError("ohne Werkzeug darf der Mantel nichts ausführen")

    for profil in sorted(agy_lauf.PROFILE):
        code = agy_lauf.main([profil], run=_kein_kommando, which=lambda _: None)
        if code != 1:
            befunde.append(f"agy_lauf {profil}: ohne `agy` Exit {code}, erwartet 1")
    return befunde


def _leerlauf_als_erfolg(lebenszeichen) -> list[str]:
    """Getaktete Workflows auf Agenten-Runnern, die einen Lauf ohne Auftrag als
    `success` abschließen können (Plan #4047 §0): Arbeit hinter einem Tor im selben
    Lauf, oder der Mantel `agy_lauf.py <profil>` liefert ohne Kandidaten 0
    (`agy_lauf.py:285-292`).

    Torwächter auf ubuntu-latest zählen nicht — ein Torwächter *ist* der
    Leerlauf-Filter (Plan §7).
    """
    befunde = []
    for pfad in sorted(WORKFLOWS.glob("*.yml")):
        daten = _lade(pfad)
        if "schedule" not in _ausloeser(daten):
            continue
        for name, job in (daten.get("jobs") or {}).items():
            job = job or {}
            if not _runner_labels(job.get("runs-on")) & lebenszeichen.AGENTEN_RUNNER:
                continue
            schritte = [s for s in job.get("steps") or [] if isinstance(s, dict)]
            tore = [str(job.get("if", ""))] + [str(s.get("if", "")) for s in schritte]
            if any(TOR_IM_LAUF.search(t) for t in tore):
                befunde.append(f"{pfad.name} ({name}): Tor im selben Lauf")
            elif any(MANTEL.search(str(s.get("run", ""))) for s in schritte):
                befunde.append(f"{pfad.name} ({name}): Mantel ohne Kandidaten = 0")
    return befunde


def _holen_attrappe(pfad: str) -> dict:
    """Ersetzt die Actions-API: `leer.yml` lief nur im Leerlauf, `arbeit.yml` arbeitete."""
    if "/workflows/" in pfad:
        workflow = pfad.split("/workflows/")[1].split("/")[0]
        lauf = {"id": workflow, "conclusion": "success", "created_at": "2026-10-02"}
        return {"workflow_runs": [lauf]}
    lauf_id = pfad.split("/runs/")[1].split("/")[0]
    schritt = "skipped" if lauf_id == "leer.yml" else "success"
    job = {"conclusion": "success", "steps": [{"conclusion": schritt}]}
    return {"jobs": [job]}


def _lebenszeichen(lebenszeichen) -> list[str]:
    befunde = []

    # (a) Der Wochenlauf ist da und ruft den Wächter — inline, es gibt kein Modul dafür.
    waechter = WORKFLOWS / "fabrik_waechter.yml"
    if not waechter.is_file():
        befunde.append("fabrik_waechter.yml fehlt")
    else:
        daten = _lade(waechter)
        if "schedule" not in _ausloeser(daten):
            befunde.append("fabrik_waechter.yml hat keinen Takt")
        runs = [
            str(s.get("run", ""))
            for job in (daten.get("jobs") or {}).values()
            for s in (job or {}).get("steps") or []
            if isinstance(s, dict)
        ]
        if not any("python scripts/lebenszeichen.py" in r for r in runs):
            befunde.append("fabrik_waechter.yml ruft scripts/lebenszeichen.py nicht")

    # (b) Kein Arbeiter fällt aus der Überwachung.
    ueberwacht = set(lebenszeichen.agenten_workflows(WORKFLOWS))
    for fehlt in sorted(ARBEITER - ueberwacht):
        befunde.append(f"{fehlt}: nicht unter agenten_workflows()")

    # (c) Der Wächter ist scharf: Leerlauf meldet er, Arbeit nicht.
    gemeldet = {
        b.workflow
        for b in lebenszeichen.pruefe(
            ["leer.yml", "arbeit.yml"],
            holen=_holen_attrappe,
            repo="o/r",
            jetzt=datetime(2026, 10, 3, tzinfo=timezone.utc),
        )
    }
    if gemeldet != {"leer.yml"}:
        befunde.append(
            f"lebenszeichen.pruefe meldet {sorted(gemeldet)}, erwartet leer.yml"
        )
    return befunde


@given(
    "die Sichtbarkeitspruefungen aus FAB-1 laufen ueber den Stand von main",
    target_fixture="module",
)
def schritt_1():
    assert WORKFLOWS.is_dir(), WORKFLOWS
    skripte = str(REPO / "scripts")
    if skripte not in sys.path:
        sys.path.insert(0, skripte)
    import agy_lauf
    import lebenszeichen
    import mess_testverzeichnisse

    return {
        "agy_lauf": agy_lauf,
        "lebenszeichen": lebenszeichen,
        "mess_testverzeichnisse": mess_testverzeichnisse,
    }


@when(
    "der Bericht ueber die Rueckmeldung der Fabrik erzeugt wird",
    target_fixture="bericht",
)
def schritt_2(module):
    mt = module["mess_testverzeichnisse"]
    # Abschnitt wie im Tor selbst, mess_testverzeichnisse.main (--pruefe).
    vertrag = tomllib.loads(mt.VERTRAG.read_text(encoding="utf-8"))["testverzeichnisse"]
    return {
        "ohne_kernkommando": _ohne_kernkommando(module["agy_lauf"]),
        # Ratschen-Lesart, bewusst: begründet eingefrorene Ausnahmen sind benannt und
        # zählen nicht als unsichtbar; jeder Befund der Ratsche zählt.
        "testverzeichnisse": mt.pruefe(REPO, vertrag),
        "cron_leerlauf": _leerlauf_als_erfolg(module["lebenszeichen"]),
        "lebenszeichen": _lebenszeichen(module["lebenszeichen"]),
    }


@then(
    "nennt er keinen Agenten-Workflow, der ohne sein Kernkommando success melden kann"
)
def schritt_3(bericht):
    assert bericht["ohne_kernkommando"] == []


@then("kein Testverzeichnis, das in keinem Workflow vorkommt")
def schritt_4(bericht):
    assert bericht["testverzeichnisse"] == []


@then("keinen Cron-Workflow, der einen Lauf ohne Auftrag als success abschliesst")
def schritt_5(bericht):
    assert bericht["cron_leerlauf"] == []


@then(
    "keinen Agenten-Workflow, dessen letztes Lebenszeichen aelter ist als die vereinbarte Frist"
)
def schritt_6(bericht):
    # Der live gemessene Stand der Läufe ist hier nicht geprüft — das tut
    # fabrik_waechter.yml wöchentlich. Geprüft ist: Wächter da, verdrahtet, scharf.
    assert bericht["lebenszeichen"] == []
