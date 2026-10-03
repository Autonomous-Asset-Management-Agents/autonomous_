"""#3385 (ARC-E2.2) — je Konto darf nur eine Engine-Instanz laufen koennen.

Solange die Engine-Sperre aus #3390 nicht steht, ist ``--max-instances=1`` die
einzige Bremse gegen einen zweiten Schreiber. Dieser Test liest die Deploy-
Dateien als Text und haelt drei Zusagen fest:

1. Keine Deploy-Stelle erlaubt mehr als eine Instanz.
2. Ein Deploy, der die Strategie automatisch startet, legt seinen Handelsmodus
   in derselben Datei fest — sonst entscheidet ihn die Datenbank zur Laufzeit,
   und die Datei sagt nicht mehr, was sie ausrollt.
3. Kein Kommentar behauptet einen anderen Modus als den, den die Datei setzt.

Bewusst ein Textscanner und kein YAML-Parser: zwei der sechs Stellen stehen in
einem Shell-Block (``ai_trading_bot/cloudbuild.yaml``, ``setup_dev_project.sh``)  # noqa: E501
und waeren als YAML-Knoten gar nicht sichtbar. Die Fehlermeldungen nennen Datei
und Zeile, damit der Test auch als Regressionsmeldung taugt.

**Grenze der Aussage** (Plan §8): Das ist eine Plattform-Obergrenze, keine
Garantie. Sie verhindert, dass Cloud Run von sich aus eine zweite Instanz
startet — nicht, dass jemand einen zweiten Prozess von Hand startet, und auf dem  # noqa: E501
Desktop wirkt sie gar nicht. Mit #3390 darf die Zahl wieder steigen; dann
prueft dieser Test eine Bedingung statt einer Zahl.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

import pytest

pytestmark = pytest.mark.unit

_REPO = Path(__file__).resolve().parents[3]


class Deploy(NamedTuple):
    """Eine Deploy-Stelle, relativ zur Repo-Wurzel."""

    pfad: str
    #: Liegt diese Stelle im Kapitalpfad? Nur dann ist die Obergrenze 1 hart
    #: gefordert und es gibt keinen Rueckweg ueber eine Begruendung.
    kapitalpfad: bool
    #: Warum — je Stelle einzeln, weil die Spur nicht ueberall dieselbe ist.
    zweck: str


# Die sechs Fundstellen, gemessen am Ist-Zustand (Plan §3). Neue Deploy-Dateien
# gehoeren hier ergaenzt — ``test_keine_unbekannte_deploy_stelle`` erzwingt das.  # noqa: E501
#
# Die vier Stellen im Kapitalpfad sind NICHT gleichartig: drei rollen einen
# Engine-Dienst mit AUTO_START_STRATEGY=true aus, die vierte hebt nur die
# Instanzgrenze eines Dienstes, der die Einstellung bereits traegt. Der
# Sicherheitsgewinn ist dort mittelbar, aber real (Plan §8).
DEPLOYS: tuple[Deploy, ...] = (
    Deploy(
        "cloudbuild-backend-deploy.yaml",
        True,
        "Engine-Deploy (Produktion) mit AUTO_START_STRATEGY=true — eine zweite Instanz "  # noqa: E501
        "beginnt beim Hochfahren von selbst zu handeln",
    ),
    Deploy(
        "ai_trading_bot/cloudbuild-engine-only.yaml",
        True,
        "Engine-Deploy (nur Engine) mit AUTO_START_STRATEGY=true — dieselbe Spur",  # noqa: E501
    ),
    Deploy(
        "ai_trading_bot/cloudbuild.yaml",
        True,
        "Engine-Deploy (Staging) mit AUTO_START_STRATEGY=true — dieselbe Spur",
    ),
    Deploy(
        ".github/workflows/backend-test-run.yml",
        True,
        "ein `services update`, das die Instanzgrenze eines Engine-Dienstes hebt. Es setzt "  # noqa: E501
        "selbst keine Env-Variablen und startet darum keine Strategie; es erlaubt aber "  # noqa: E501
        "einem Dienst, der AUTO_START_STRATEGY bereits traegt, eine zweite Instanz",  # noqa: E501
    ),
    Deploy(
        "infra/enterprise-admin/setup_dev_project.sh",
        False,
        "Entwickler-Werkzeug (paperclip-dev), kein Engine-Dienst",
    ),
    Deploy(
        "cloudbuild-public-api.yaml",
        False,
        "Proxy vor der Engine (aaa-api-public), setzt keine Orders ab",
    ),
)

# Eine Stelle OHNE Kapitalpfad darf ueber 1 liegen — aber nur mit einer hier
# hinterlegten Begruendung. Das ist der ausdrueckliche Rueckweg aus Option B des  # noqa: E501
# Plans: nicht verboten, aber nicht stillschweigend. Fuer Stellen IM Kapitalpfad  # noqa: E501
# gibt es diesen Rueckweg nicht.
BEGRUENDETE_AUSNAHMEN: dict[str, str] = {}

# Verzeichnisse, die kein Repository-Inhalt sind: Werkzeug-Ablagen und
# Abhaengigkeiten. Sie enthalten Kopien der Deploy-Dateien (Agenten-Worktrees,
# gecachte Charts) und wuerden den Bestandsscan sonst mit Duplikaten fluten.
_IGNORIERT = frozenset(
    {
        ".git",
        ".claude",
        ".venv",
        "venv",
        "node_modules",
        "dist",
        "build",
        ".tox",
        "site-packages",
    }
)

_MAX_INSTANCES = re.compile(r"--max-instances=(\d+)")
_AUTO_START = re.compile(r"AUTO_START_STRATEGY=(\w+)")
_PAPER_GESETZT = re.compile(r"PAPER_TRADING=(\w+)")


def _zeilen(pfad: str) -> list[str]:
    datei = _REPO / pfad
    assert datei.is_file(), (
        f"{pfad} fehlt. Wurde die Datei umbenannt, gehoert DEPLOYS nachgezogen — "  # noqa: E501
        "ein stillschweigend uebersprungener Deploy ist schlimmer als ein roter Test."  # noqa: E501
    )
    return datei.read_text(encoding="utf-8").splitlines()


def _ist_kommentar(zeile: str) -> bool:
    return zeile.lstrip().startswith("#")


def test_keine_deploy_stelle_erlaubt_mehr_als_eine_instanz() -> None:
    """Szenario: Keine Deploy-Stelle erlaubt mehr als eine Instanz."""
    verstoesse: list[str] = []
    gefunden_in: set[str] = set()

    for deploy in DEPLOYS:
        for nr, zeile in enumerate(_zeilen(deploy.pfad), start=1):
            if _ist_kommentar(zeile):
                continue
            treffer = _MAX_INSTANCES.search(zeile)
            if not treffer:
                continue
            gefunden_in.add(deploy.pfad)
            wert = int(treffer.group(1))
            if wert == 1:
                continue
            if deploy.kapitalpfad:
                verstoesse.append(
                    f"{deploy.pfad}:{nr} — max-instances={wert}. Diese Stelle liegt im "  # noqa: E501
                    f"Kapitalpfad: {deploy.zweck}. Der einzige vorhandene Lock greift je "  # noqa: E501
                    "Nutzer und Symbol fuer 12 Sekunden (core/order_executor.py), nicht je "  # noqa: E501
                    "Konto — zwei Instanzen setzen fuer dieselbe Entscheidung je eine Order "  # noqa: E501
                    "ab (#3385; die Sperre je Konto kommt mit #3390)."
                )
            elif deploy.pfad not in BEGRUENDETE_AUSNAHMEN:
                verstoesse.append(
                    f"{deploy.pfad}:{nr} — max-instances={wert} ohne hinterlegte Begruendung. "  # noqa: E501
                    f"Dieser Dienst liegt nicht im Kapitalpfad ({deploy.zweck}); ein hoeherer "  # noqa: E501
                    "Wert ist zulaessig, aber er gehoert mit Grund in BEGRUENDETE_AUSNAHMEN "  # noqa: E501
                    f"in {Path(__file__).name}."
                )

    fehlend = {d.pfad for d in DEPLOYS} - gefunden_in
    assert not fehlend, (
        "In diesen Dateien steht kein max-instances mehr: "
        + ", ".join(sorted(fehlend))
        + ". Entweder ist die Grenze entfallen — dann gilt die Cloud-Run-Vorgabe und der "  # noqa: E501
        "Schutz aus #3385 ist weg — oder DEPLOYS ist veraltet."
    )
    assert not verstoesse, "\n".join(verstoesse)


def test_automatischer_strategiestart_legt_den_modus_in_derselben_datei_fest() -> (  # noqa: E501
    None
):  # noqa: E501
    """Szenario: Automatischer Strategiestart ohne festgelegten Modus ist ausgeschlossen.  # noqa: E501

    ``PAPER_TRADING`` hat in ``config.py`` den Default ``True``, wird aber zur
    Laufzeit aus der Datenbank nachgezogen (``alpaca_paper``). Eine Deploy-Datei,  # noqa: E501
    die die Strategie automatisch startet und den Modus NICHT setzt, sagt darum
    nicht, was sie ausrollt — die Antwort steht in einer Tabelle.
    """
    verstoesse: list[str] = []

    for deploy in DEPLOYS:
        zeilen = _zeilen(deploy.pfad)
        startet_automatisch = any(
            _AUTO_START.search(z)
            and _AUTO_START.search(z).group(1).lower() == "true"  # noqa: E501
            for z in zeilen
            if not _ist_kommentar(z)
        )
        if not startet_automatisch:
            continue

        modi = [
            (nr, m.group(1).lower())
            for nr, z in enumerate(zeilen, start=1)
            if not _ist_kommentar(z)
            for m in [_PAPER_GESETZT.search(z)]
            if m
        ]
        if not modi:
            verstoesse.append(
                f"{deploy.pfad} — AUTO_START_STRATEGY=true, aber PAPER_TRADING wird nicht "  # noqa: E501
                "gesetzt. Der Handelsmodus entscheidet sich dann erst zur Laufzeit aus der "  # noqa: E501
                "Datenbank; die Datei rollt aus, ohne zu sagen, in welchem Modus. Bei einem "  # noqa: E501
                "Dienst, der von selbst zu handeln beginnt, muss der Modus in der Datei "  # noqa: E501
                "stehen (#3385)."
            )
            continue
        for nr, wert in modi:
            if wert != "true":
                verstoesse.append(
                    f"{deploy.pfad}:{nr} — AUTO_START_STRATEGY=true zusammen mit "  # noqa: E501
                    f"PAPER_TRADING={wert}. Ein Dienst, der beim Hochfahren von selbst mit "  # noqa: E501
                    "echtem Geld handelt, braucht die menschliche Aufsicht aus EU AI Act "  # noqa: E501
                    "Art. 14 und die Engine-Sperre aus #3390 — beides ist hier nicht belegt."  # noqa: E501
                )

    assert not verstoesse, "\n".join(verstoesse)


def test_kein_kommentar_behauptet_einen_anderen_modus_als_die_datei_setzt() -> (  # noqa: E501
    None
):  # noqa: E501
    """Szenario: Der Kommentar beschreibt den tatsaechlich gesetzten Modus.

    Die Regel ist absichtlich eng: In einer Deploy-Datei, die ``PAPER_TRADING``
    setzt, darf in einem Kommentar kein abweichender ``PAPER_TRADING``-Literal
    stehen. Bedingungen ueber den Boot-Gate lassen sich ohne Literal formulieren  # noqa: E501
    („wenn Paper-Trading aus ist“) — ein Literal im Kommentar ist immer
    eine Aussage ueber DIESE Datei und muss darum stimmen.
    """
    verstoesse: list[str] = []

    for deploy in DEPLOYS:
        zeilen = _zeilen(deploy.pfad)
        gesetzt = [
            (nr, m.group(1).lower())
            for nr, z in enumerate(zeilen, start=1)
            if not _ist_kommentar(z)
            for m in [_PAPER_GESETZT.search(z)]
            if m
        ]
        if not gesetzt:
            continue
        wert_zeile, wert = gesetzt[0]

        for nr, zeile in enumerate(zeilen, start=1):
            if not _ist_kommentar(zeile):
                continue
            behauptung = _PAPER_GESETZT.search(zeile)
            if behauptung and behauptung.group(1).lower() != wert:
                verstoesse.append(
                    f"{deploy.pfad}:{nr} behauptet PAPER_TRADING={behauptung.group(1)}, "  # noqa: E501
                    f"waehrend {deploy.pfad}:{wert_zeile} PAPER_TRADING={wert} setzt. Wer den "  # noqa: E501
                    "Kommentar liest, haelt ein Paper-Deploy fuer ein Live-Deploy — oder beim "  # noqa: E501
                    "naechsten Umbau ein Live-Deploy fuer harmlos (#3385)."
                )

    assert not verstoesse, "\n".join(verstoesse)


def test_keine_unbekannte_deploy_stelle() -> None:
    """Eine neue Deploy-Datei darf sich nicht an dieser Pruefung vorbeischleichen.  # noqa: E501

    Ohne diesen Test waere die Liste oben eine Momentaufnahme: Wer morgen eine
    siebte Deploy-Datei anlegt, umgeht alle drei Zusagen, ohne dass etwas rot
    wird. Gesucht wird darum im gesamten Bestand nach ``--max-instances``.
    """
    bekannt = {d.pfad for d in DEPLOYS}
    unbekannt: list[str] = []

    for datei in _REPO.rglob("*"):
        if not datei.is_file() or datei.suffix not in {".yaml", ".yml", ".sh"}:
            continue
        rel = datei.relative_to(_REPO).as_posix()
        if rel in bekannt or _IGNORIERT.intersection(
            datei.relative_to(_REPO).parts[:-1]
        ):
            continue
        try:
            inhalt = datei.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if _MAX_INSTANCES.search(inhalt):
            unbekannt.append(rel)

    assert not unbekannt, (
        "Diese Dateien setzen max-instances, stehen aber nicht in DEPLOYS: "
        + ", ".join(sorted(unbekannt))
        + ". Jede Deploy-Stelle gehoert in die Liste, sonst prueft dieser Test eine "  # noqa: E501
        "Momentaufnahme statt den Bestand (#3385)."
    )
