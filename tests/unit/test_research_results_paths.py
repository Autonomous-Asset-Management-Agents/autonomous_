"""Drift-Wächter für das Versuchsregister — Ergebnisse dürfen nicht in der Schublade landen.

Am 20.08. zeigte eine Prüfung, dass **alle vier** im Register verlinkten Ergebnispfade im Repo
fehlten. Kein Versäumnis, sondern `.gitignore`: `ai_trading_bot/research_results/` ist ignoriert
(zu Recht — dort liegen 545-MB-Logs), und damit waren auch Specs und Auswertungen ignoriert. Zwei
Sweeps aus der Vorwoche sind so faktisch nicht mehr nachlesbar; ihr Versuchszähler zählt trotzdem
gegen die Deflated-Sharpe-Korrektur weiter.

Dieser Test prüft **nur** eines: eine Registerzeile, deren Urteil nicht mehr „läuft" ist, muss auf
ein existierendes Ergebnisdokument im Repo zeigen. Keine Inhalte, keine Vollständigkeit — dieselbe
enge Zusicherung wie in ``scripts/test_docs_index_paths.py`` (#2930), aus demselben Grund: ein
Wegweiser, der ins Leere zeigt, erzeugt falsche Sicherheit.

Bewusst NICHT geprüft: die zwei Altbestandszeilen vom 13./14.08. Sie sind im Register ausdrücklich
als „nur lokal, nicht mehr auffindbar" markiert — sichtbare Schuld statt stiller Lücke. Sie tragen
deshalb keinen Markdown-Link und fallen aus der Prüfung; wer sie nachträglich dokumentiert, hängt
einen Link an und wird ab dann mitgeprüft.
"""

from __future__ import annotations

import re
from pathlib import Path

# parents[3] = Repo-Wurzel (tests/unit → tests → ai_trading_bot → repo), Muster wie
# test_cloudbuild_deploy_spec.py. Der Test liegt bewusst HIER und nicht in scripts/:
# die CI faehrt ausschliesslich `ai_trading_bot/tests/unit/` (ci.yml:229) — ein Waechter
# unter scripts/ wuerde nie laufen und waere damit dekorativ.
REPO = Path(__file__).resolve().parents[3]
REGISTER = REPO / "docs" / "6_runbooks" / "RESEARCH_EXPERIMENT_LOG.md"

# Eine Tabellenzeile des Registers: | Datum | Issue | Frage | Konfig | Hash | Zeitraum | Urteil | Ergebnis |
_ROW = re.compile(r"^\|(?P<cells>.+)\|\s*$", re.MULTILINE)
# Markdown-Link in der Ergebnis-Spalte, relativ zu docs/6_runbooks/
_MD_LINK = re.compile(r"\[[^\]]*\]\(([^)#\s]+)\)")
# Urteil "läuft" / "vorab registriert" ⇒ noch kein Ergebnis faellig
_PENDING = re.compile(r"\*l[äa]uft|vorab registriert|startet nach", re.IGNORECASE)
# Zulaessige Kennzeichnungen, wenn es KEIN Ergebnisdokument gibt — sichtbare Schuld statt
# stiller Luecke. Bewusst kurz: jede weitere Formulierung waere ein Schlupfloch.
_EXPLICIT_NO_ARTEFACT = ("nicht mehr auffindbar", "kein Artefakt")


def _result_rows() -> list[tuple[str, str]]:
    """(Urteil, Ergebnis-Zelle) je Datenzeile der Register-Tabelle."""
    rows = []
    for m in _ROW.finditer(REGISTER.read_text(encoding="utf-8")):
        cells = [c.strip() for c in m.group("cells").split("|")]
        if len(cells) < 8:
            continue
        if cells[0].startswith(("---", "Datum")):  # Kopf- und Trennzeile
            continue
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", cells[0]):  # nur echte Versuchszeilen
            continue
        rows.append((cells[6], cells[7]))
    return rows


def test_finished_experiments_link_to_an_existing_result_document():
    missing = []
    for verdict, result in _result_rows():
        if _PENDING.search(verdict):
            continue  # laeuft noch — Ergebnis darf fehlen
        link = _MD_LINK.search(result)
        if not link:
            continue  # kein Link (z. B. bewusst als "nur lokal" markierter Altbestand)
        target = (REGISTER.parent / link.group(1)).resolve()
        if not target.exists():
            missing.append(f"{link.group(1)} (Urteil: {verdict[:40]}…)")
    assert not missing, (
        "Versuchsregister verweist auf nicht existierende Ergebnisdokumente: "
        + ", ".join(missing)
        + " — ein abgeschlossener Versuch ohne auffindbares Ergebnis ist eine Schublade."
    )


def test_finished_experiments_have_a_result_link_at_all():
    """Ein abgeschlossener Versuch ohne JEDEN Link ist nur zulaessig, wenn die Zelle das
    ausdruecklich als nicht mehr auffindbar ausweist — sichtbare Schuld statt stiller Luecke.
    """
    silent = [
        result[:60]
        for verdict, result in _result_rows()
        if not _PENDING.search(verdict)
        and not _MD_LINK.search(result)
        and not any(m in result for m in _EXPLICIT_NO_ARTEFACT)
    ]
    assert not silent, (
        "Abgeschlossene Versuche ohne Ergebnis-Link und ohne Kennzeichnung: "
        + "; ".join(silent)
        + " — entweder RESULTS.md verlinken oder als 'nicht mehr auffindbar' markieren."
    )


def test_guard_is_not_vacuous():
    """ANTI-VAKUITAET: die Pruefung muss ueberhaupt Zeilen und Links gefunden haben."""
    rows = _result_rows()
    assert (
        len(rows) >= 5
    ), f"nur {len(rows)} Versuchszeilen erkannt — Tabellenformat geaendert?"
    linked = sum(1 for _v, r in rows if _MD_LINK.search(r))
    assert (
        linked >= 3
    ), f"nur {linked} Ergebnis-Links erkannt — zeigt der Waechter ins Leere?"


# ---------------------------------------------------------------------------
# Pflichtabschnitte je Ergebnisdokument (#2954-Nachtrag)
#
# Owner-Frage am 20.08.: „ist auch dokumentiert, wie das Testziel war, dann die Vorgehensweise,
# in scope / out of scope?" — Antwort war: teilweise. Ohne Scope-Abschnitt faellt nicht auf, wenn
# ein Lauf die gestellte Frage gar nicht misst; genau das passierte bei #2940 (der
# Verdraengungspfad wurde im Replay nie erreicht, gemerkt erst beim Auszaehlen der Logs).
#
# Die Liste folgt der Vorlage docs/6_runbooks/RESULTS_TEMPLATE.md und dem Standard
# STRATEGY_VALIDATION §6. Bewusst knapp: nur Abschnitte, deren Fehlen ein Ergebnis
# missverstaendlich macht.
_REQUIRED_SECTIONS = (
    "## Testziel",
    "## Testaufbau",
    "## In Scope",
    "## Out of Scope",
    "## Durchführung",
    "## Ergebnis",
    "## Urteil gegen das vorab registrierte Kriterium",
    "## Einschränkungen",
    "## Reproduktion",
    "## Folge",
)


def _result_documents() -> list[Path]:
    """Alle Ergebnisdokumente — ohne die Vorlage selbst."""
    return sorted(
        p
        for p in (REPO / "docs").glob("*/results/RESULTS.md")
        if p.name != "RESULTS_TEMPLATE.md"
    )


def test_every_result_document_has_the_required_sections():
    problems = []
    for doc in _result_documents():
        text = doc.read_text(encoding="utf-8")
        missing = [h for h in _REQUIRED_SECTIONS if h not in text]
        if missing:
            problems.append(f"{doc.relative_to(REPO)}: {', '.join(missing)}")
    assert not problems, (
        "Ergebnisdokumente ohne Pflichtabschnitte: "
        + " | ".join(problems)
        + " — Vorlage: docs/6_runbooks/RESULTS_TEMPLATE.md"
    )


def test_every_result_document_references_the_general_limits():
    """STRATEGY_VALIDATION §3 sagt woertlich: „Diese Liste ist Teil des Verfahrens, nicht eine
    Fussnote. Jeder Bericht verweist darauf." Genau das war in der ersten Fassung versaeumt.
    """
    missing = [
        str(doc.relative_to(REPO))
        for doc in _result_documents()
        if "STRATEGY_VALIDATION" not in doc.read_text(encoding="utf-8")
    ]
    assert not missing, (
        "Ergebnisdokumente ohne Verweis auf die generellen Grenzen (STRATEGY_VALIDATION §3): "
        + ", ".join(missing)
    )


def test_every_result_document_has_a_machine_readable_twin():
    """§6 verlangt RESULTS.md (lesbar) UND RESULTS.json (maschinenlesbar, inkl. Korpus-Hash und
    Kostenannahme) — Letzteres macht den Versuchszaehler auswertbar statt nur lesbar."""
    import json

    problems = []
    for doc in _result_documents():
        twin = doc.with_suffix(".json")
        if not twin.exists():
            problems.append(f"{twin.relative_to(REPO)} fehlt")
            continue
        data = json.loads(twin.read_text(encoding="utf-8"))
        for key in ("issue", "urteil", "korpus_hash", "kostenannahme_bps"):
            if key not in data:
                problems.append(f"{twin.relative_to(REPO)}: Feld '{key}' fehlt")
    assert not problems, "; ".join(problems)


def test_section_guard_is_not_vacuous():
    docs = _result_documents()
    assert (
        len(docs) >= 3
    ), f"nur {len(docs)} Ergebnisdokumente gefunden — Ablagepfad geaendert?"
