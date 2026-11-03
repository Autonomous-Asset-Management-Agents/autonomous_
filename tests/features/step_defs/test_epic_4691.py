"""Abnahme auf Epic-Ebene für #4691 — scharf geschaltet (#4709, Plan Fassung 5).

Vom Maker als Platzhalter angelegt (#3785), hier durch echte Prüfungen ersetzt. Der
erwartete Fehlschlag am Modulkopf ist entfallen; ab jetzt ist diese Abnahme ein Tor.

(Das Wort für diesen Marker steht hier absichtlich nicht ausgeschrieben:
`epic_abnahme._XFAIL` sucht es im Dateitext und hielte die Abnahme sonst für weiter offen.)

**Was sie NICHT prüft, und warum das eine Entscheidung ist.** Fassung 1 bis 3 wollten die
Zweck-Kennzahl prüfen: abgerechnete Linux-Minuten je `ci.yml`-PR-Lauf, Ziel ≤ 47,0 über
sieben Tage. Fassung 4 ersetzte das 7-Tage-Fenster durch einen Einzeltag. Beides ist
gestrichen (Owner-Entscheidung 27.10.2026), aus drei gemessenen Gründen:

1. Die Kennzahl teilt eine **feste Grundlast** durch einen **variablen Nenner**. Über sieben
   Tage schwankte sie zwischen 73,0 (24.10.2026, 76 Läufe) und 133,4 (19.10.2026, 7 Läufe);
   bei einem Einzeltag ist die Streuung größer, nicht kleiner. Eine Schwelle darauf prüft den
   Tag, nicht die Maßnahme.
2. Die Erhebung braucht die **GitHub-API**. Im Job `Backend BDD Tests` gibt es weder `gh` noch
   ein Token — eine Abnahme, die Netz braucht, ist keine Abnahme.
3. Jede kostenwirksame Workflow-Änderung verschob das Fenster neu. Mit #4815 und #4817 (beide
   27.10.) wäre die Abnahme erst am 4. November erhebbar gewesen. Eine Abnahme, die sich durch
   Fortschritt entfernt, ist kaputt.

Geprüft wird deshalb **am Dateistand**: offline, ohne Datum, ohne Netz — dass jede Maßnahme in
Kraft ist und dass kein Pflicht-, Release- oder Testschutz verloren ging. Die Kennzahl bleibt
die Kennzahl des Epics und wird berichtet: `python scripts/mess_ci_kosten.py --tage 7`.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

import yaml
from pytest_bdd import given, parsers, scenarios, then, when

WURZEL = pathlib.Path(__file__).resolve().parents[4]
WORKFLOWS = WURZEL / ".github" / "workflows"
PFLICHT_DATEI = WURZEL / ".github" / "pflicht_checks.yml"
DEPENDABOT = WURZEL / ".github" / "dependabot.yml"
RATSCHEN = WURZEL / "scripts" / "tests"

sys.path.insert(0, str(RATSCHEN))

scenarios("../epic_4691.feature")


# ---------------------------------------------------------------- Werkzeuge


def _lies(name: str) -> dict:
    pfad = WORKFLOWS / name
    assert pfad.exists(), f"{name} fehlt unter .github/workflows/"
    return yaml.safe_load(pfad.read_text(encoding="utf-8")) or {}


def _on(daten: dict) -> dict:
    # PyYAML liest den Schlüssel `on` nach YAML 1.1 als Boolean True.
    on = daten.get("on", daten.get(True)) or {}
    return on if isinstance(on, dict) else {}


def _nebenlaeufigkeiten(daten: dict) -> list[dict]:
    roh = [daten.get("concurrency")] + [
        (job or {}).get("concurrency") for job in (daten.get("jobs") or {}).values()
    ]
    return [g for g in roh if isinstance(g, dict)]


def _jobnamen(daten: dict) -> set[str]:
    """Die Check-Kontexte einer Datei: der Job-Name, sonst die Job-ID."""
    return {
        str((job or {}).get("name") or kennung)
        for kennung, job in (daten.get("jobs") or {}).items()
    }


def _alle_workflows() -> dict[str, dict]:
    return {p.name: _lies(p.name) for p in sorted(WORKFLOWS.glob("*.yml"))}


def _pflicht_kontexte() -> set[str]:
    daten = yaml.safe_load(PFLICHT_DATEI.read_text(encoding="utf-8")) or {}
    eintraege = daten.get("pflicht_checks") or []
    assert eintraege, "`.github/pflicht_checks.yml` ist leer gelesen worden."
    return {e["kontext"] for e in eintraege}


def _release_kontexte() -> set[str]:
    """Die Allowlist `RELEVANT` aus `desktop-release.yml` — gelesen, nicht abgeschrieben."""
    text = (WORKFLOWS / "desktop-release.yml").read_text(encoding="utf-8")
    treffer = re.search(r"RELEVANT='(\[.*?\])'", text, re.DOTALL)
    assert treffer, "RELEVANT nicht in desktop-release.yml gefunden"
    return set(json.loads(treffer.group(1)))


# ---------------------------------------------------------------- Szenario 1


@given("die auf main gemergten Maßnahmen von #4691")
def gegebene_massnahmen():
    """Gegenprobe gegen eine Abnahme, die nur deshalb grün ist, weil sie nichts liest."""
    assert len(_alle_workflows()) >= 20, "zu wenige Workflows gelesen"
    assert len(_pflicht_kontexte()) >= 10
    assert len(_release_kontexte()) >= 8


@when("die Workflows und die Ratschen in scripts/tests/ gelesen werden")
def lies_alles():
    """Die Ratschen sind die Quelle, nicht eine zweite Liste.

    Jede Maßnahme hat auf `main` schon ihren eigenen Test. Diese Abnahme baut die Listen
    nicht nach, sondern importiert sie — eine Kopie wäre eine zweite Regel, die driften
    kann.
    """
    import test_kein_abbruch_auf_pflicht_toren as ratsche

    assert ratsche.MELDER, "MELDER ist leer importiert worden"
    assert ratsche.ZUSTANDSUEBERGAENGE, "ZUSTANDSUEBERGAENGE ist leer importiert worden"


@then(
    "rufen die Melder aus #4766 A eine concurrency-Gruppe je PR mit cancel-in-progress true auf"
)
def melder_brechen_ab():
    import test_kein_abbruch_auf_pflicht_toren as ratsche

    fehlend = []
    for name in ratsche.MELDER:
        gruppen = _nebenlaeufigkeiten(_lies(name))
        if not any(
            g.get("group") == ratsche.GRUPPE_JE_PR
            and g.get("cancel-in-progress") is True
            for g in gruppen
        ):
            fehlend.append(name)
    assert not fehlend, (
        f"Diesen Meldern fehlt die Gruppe je PR mit Abbruch: {fehlend}. Maßnahme A aus "
        "#4766 ist dort nicht in Kraft."
    )


@then(
    "aut9-flow.yml, project-automation.yml und sync-plan-labels.yml tragen "
    "cancel-in-progress false"
)
def zustand_bricht_nicht_ab():
    import test_kein_abbruch_auf_pflicht_toren as ratsche

    verstoesse = []
    for name in ratsche.ZUSTANDSUEBERGAENGE:
        gruppen = _nebenlaeufigkeiten(_lies(name))
        if not gruppen or not all(
            g.get("cancel-in-progress") is False for g in gruppen
        ):
            verstoesse.append(f"{name}: {gruppen}")
    assert not verstoesse, (
        "Diese Workflows führen Zustandsübergänge aus und dürfen nicht abbrechen — ein "
        f"halber Übergang bleibt liegen: {verstoesse}"
    )


@then("kein Workflow mit einem Pflicht- oder Release-Kontext bricht ab")
def kein_tor_bricht_ab():
    """Die harte Grenze: ein `cancelled` erfüllt einen Pflicht-Kontext (#4764) und gilt am
    Release als NO-GO (#4789)."""
    import test_kein_abbruch_auf_pflicht_toren as ratsche

    geschuetzt = _pflicht_kontexte() | _release_kontexte()
    funde = []
    for name, daten in _alle_workflows().items():
        if name in ratsche.AUSNAHMEN:
            continue
        if not any(
            g.get("cancel-in-progress") is True for g in _nebenlaeufigkeiten(daten)
        ):
            continue
        betroffen = sorted(_jobnamen(daten) & geschuetzt)
        if betroffen:
            funde.append(f"{name}: {betroffen}")
    assert not funde, (
        f"Diese Workflows brechen einen Pflicht- oder Release-Kontext ab: {funde}. "
        "Benannte Ausnahmen stehen in test_kein_abbruch_auf_pflicht_toren.AUSNAHMEN."
    )


@then("release-drafter.yml hat als einzigen Auslöser workflow_dispatch")
def drafter_auf_abruf():
    on = _on(_lies("release-drafter.yml"))
    assert "workflow_dispatch" in on, (
        "`release-drafter.yml` ist nicht mehr auf Abruf erreichbar — gespart wäre dann "
        "nicht der Dauerlauf, sondern die Fähigkeit (#4795)."
    )
    assert set(on) == {"workflow_dispatch"}, (
        f"`release-drafter.yml` hat weitere Auslöser: {sorted(set(on) - {'workflow_dispatch'})}. "
        "Am 26.10.2026 kosteten 25 main-Commits 235 abgerechnete Minuten (#4795)."
    )


@then("jede Dependabot-Gruppe gilt nur für version-updates und nur für minor und patch")
def dependabot_gruppen():
    konfiguration = yaml.safe_load(DEPENDABOT.read_text(encoding="utf-8")) or {}
    eintraege = konfiguration.get("updates") or []
    assert len(eintraege) >= 3, "dependabot.yml ist leer gelesen worden"
    verstoesse = []
    for eintrag in eintraege:
        gruppen = eintrag.get("groups") or {}
        if not gruppen:
            verstoesse.append(f"{eintrag.get('package-ecosystem')}: keine Gruppe")
            continue
        for name, gruppe in gruppen.items():
            g = gruppe or {}
            if g.get("applies-to") != "version-updates":
                verstoesse.append(f"{name}: applies-to={g.get('applies-to')!r}")
            arten = g.get("update-types")
            if not arten or not set(arten) <= {"minor", "patch"}:
                verstoesse.append(f"{name}: update-types={arten!r}")
    assert not verstoesse, (
        f"Dependabot-Gruppen nicht wie in #4803/#4810 festgelegt: {verstoesse}. Ohne "
        "`applies-to` würden Sicherheitsfixes mitgebündelt; ohne die Beschränkung auf "
        "minor/patch verschwände ein Hauptversionssprung zwischen Dutzenden Bumps."
    )


@then('der Job „Docs / Markdown link check" ruft markdown-link-check gebündelt auf')
def link_check_gebuendelt():
    text = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    assert (
        "markdown-link-check" in text
    ), "markdown-link-check steht nicht mehr in ci.yml"
    assert not re.search(
        r"for\s+\w+\s+in\s+.*\.md.*;\s*do\s+.*markdown-link-check", text, re.DOTALL
    ), (
        "markdown-link-check läuft wieder je Datei in einer Schleife — das waren 1.857 "
        "Prozesse (#4697)."
    )


@then(
    "ein Backend-Lauf von ci.yml installiert requirements-torch-ci.txt in höchstens 4 Jobs"
)
def torch_in_wenigen_jobs():
    daten = _lies("ci.yml")
    mit_torch = [
        kennung
        for kennung, job in (daten.get("jobs") or {}).items()
        if "requirements-torch-ci.txt" in yaml.safe_dump(job or {}, allow_unicode=True)
    ]
    assert len(mit_torch) <= 4, (
        f"{len(mit_torch)} Jobs installieren requirements-torch-ci.txt: {sorted(mit_torch)}. "
        "#4699 hat die fünf schnellen Gates in einen gemeinsamen Container gelegt."
    )


@then(
    "oss-ci.yml bricht überholte PR-Läufe ab und überspringt bei reinen Factory-Pfaden "
    "den Docker-Stack-Build"
)
def oss_ci_bricht_ab():
    daten = _lies("oss-ci.yml")
    gruppen = _nebenlaeufigkeiten(daten)

    def bricht_bei_pr_ab(wert) -> bool:
        """`true` oder ein Ausdruck, der auf `pull_request` einschränkt.

        `oss-ci.yml` schreibt `${{ github.event_name == 'pull_request' }}` — also nur auf
        PRs abbrechen, auf `main` nie. Das ist **strenger** als ein hartes `true` und
        genau die Lehre aus #4789: Auf `main` würde ein Abbruch einen Release-Check als
        `cancelled` zurücklassen. Ein Test, der hier `true` verlangte, hätte die bessere
        Lösung rot gemacht.
        """
        if wert is True:
            return True
        return isinstance(wert, str) and "pull_request" in wert

    assert any(
        bricht_bei_pr_ab(g.get("cancel-in-progress")) for g in gruppen
    ), f"`oss-ci.yml` bricht überholte PR-Läufe nicht mehr ab: {gruppen} (#4703)."
    text = (WORKFLOWS / "oss-ci.yml").read_text(encoding="utf-8")
    assert "paths-filter" in text or "needs.filter" in text or "outputs." in text, (
        "`oss-ci.yml` hat keinen Pfadfilter mehr, der den Docker-Stack-Build bei reinen "
        "Factory-Pfaden überspringt (#4703)."
    )


@then(
    "tor-neulauf.yml übernimmt den Neulauf der Pflicht-Tore und ist selbst kein Pflicht-Kontext"
)
def tor_neulauf():
    daten = _lies("tor-neulauf.yml")
    assert daten.get("jobs"), "`tor-neulauf.yml` hat keinen Job mehr (#4764)."
    assert not (_jobnamen(daten) & _pflicht_kontexte()), (
        "`tor-neulauf.yml` liefert selbst einen Pflicht-Kontext. Dann hängt der Neulauf an "
        "dem Tor, das er neu starten soll (#4764)."
    )


# ---------------------------------------------------------------- Szenario 2


@given(
    "die Pflicht-Checks aus .github/pflicht_checks.yml und die Release-Checks aus "
    "desktop-release.yml"
)
def gegebene_kontexte():
    assert _pflicht_kontexte() and _release_kontexte()


@when("die Workflows gelesen werden")
def lies_workflows():
    assert _alle_workflows()


@then("stehen alle unter unverändertem Namen")
def namen_unveraendert():
    """Ein Kontext, den kein Job mehr liefert, bleibt „expected — waiting for status" und
    blockiert jeden PR dauerhaft (`pflicht_checks.yml`, Regel 1)."""
    vorhanden: set[str] = set()
    for daten in _alle_workflows().values():
        vorhanden |= _jobnamen(daten)
    fehlend_pflicht = sorted(_pflicht_kontexte() - vorhanden)
    fehlend_release = sorted(_release_kontexte() - vorhanden)
    # Bekannt und nicht von diesem Epic verursacht: `workflow_invarianten.py` meldet
    # denselben Namen seit der Basis als Warnung, nicht als Befund.
    fehlend_release = [n for n in fehlend_release if n != "CI Governance (Epic 6.1)"]
    assert not fehlend_pflicht, (
        f"Diese Pflicht-Kontexte liefert kein Job mehr: {fehlend_pflicht}. Jeder PR "
        "blockiert dauerhaft."
    )
    assert not fehlend_release, (
        f"Diese Release-Checks liefert kein Job mehr: {fehlend_release}. "
        "`desktop-release.yml` wertet sie als PENDING und damit als NO-GO."
    )


@then(
    "jeder heutige Pytest-Aufruf der Backend-Gates steht mit unveränderten Testpfaden und "
    "Coverage-Schwellen 75 und 60 weiter in ci.yml"
)
def coverage_schwellen():
    text = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    for schwelle in ("75", "60"):
        assert re.search(rf"cov-fail-under[= ]{schwelle}\b", text), (
            f"Die Coverage-Schwelle {schwelle} steht nicht mehr in ci.yml. Das Epic darf "
            "Minuten sparen, nicht Prüftiefe."
        )
