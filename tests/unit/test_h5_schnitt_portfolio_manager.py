"""#4187 (H-5) — die Schnitt-Entscheidung für ``portfolio_manager.py`` deckt die Datei vollständig.

Plan: ``docs/4187-*/implementation_plan.md`` §5/§6. Das Dokument
``docs/3738-arc-e6-gestalt/H5_SCHNITT_portfolio_manager.md`` ordnet jede Methode von
``PortfolioManager``, jede Funktion, Klasse und Konstante auf Modulebene **genau einem**
Zielmodul zu, nennt je Zielmodul eine Planzahl (≤ 800) und ein Netz, je Patch-Ziel und je
Pfad-Leser der Tests den Weg und je Umzug eine Größenklasse (S/M) samt
``mess_zuschnitt``-Aufruf.

Gelesen wird ohne Import (``ast``), Muster ``test_h3_schnitt_risk_manager.py``. Ein Symbol
gilt als gefunden, wenn es in ``portfolio_manager.py`` **oder** in seinem Zielmodul steht — so
bleibt der Test über die Umzüge hinweg wahr und prüft zugleich, dass ein Symbol nur dorthin
wandert, wo das Dokument es hinschickt.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
WURZEL = PAKET.parent
QUELLE = "core/portfolio_manager.py"
KLASSE = "PortfolioManager"
DOKUMENT = WURZEL / "docs" / "3738-arc-e6-gestalt" / "H5_SCHNITT_portfolio_manager.md"
VERTRAG = PAKET / "tests" / "architecture" / "vertrag.toml"

DATEI_GRENZE = 800
FUNKTION_GRENZE = 150

# Vor dem Umzug ``portfolio_manager.py::SYMBOL``, danach ``<zielmodul>.py::SYMBOL`` — der
# Doku-Anker (scripts/check_doc_anchors.py, NAME_RE) bricht genau dann, wenn der Name wandert.
_ZUORDNUNG = re.compile(
    r"^\|\s*`(\w+\.py)::(\w+)`\s*\|\s*(\d+)\s*\|\s*`(core/\w+\.py)`\s*\|",
    re.M,
)
_ZIELMODUL = re.compile(
    r"^\|\s*`(core/\w+\.py)`\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|([^|]*)\|([^|]*)\|\s*$",
    re.M,
)
_UMZUG = re.compile(r"^\|\s*(H-5[a-z])\s*\|([^|]*)\|\s*([SML])\s*\|", re.M)
_PATCH_ZEILE = re.compile(
    r"^\|\s*`(\w+)`\s*\|\s*(\d+)[^|]*\|([^|]*)\|([^|]*)\|\s*$", re.M
)
_LESER_ZEILE = re.compile(
    r"^\|\s*`(\w+\.py)`\s*\|\s*(\d+)[^|]*\|([^|]*)\|([^|]*)\|\s*$", re.M
)

# Patch-Ziele am Modulobjekt: als Pfad-String, über einen Modul-Alias (patch.object/setattr)
# oder als direkte Zuweisung ``alias.NAME = …``.
_PATCH_PFAD = re.compile(r"core[.]portfolio_manager[.]([A-Za-z_]\w*)")
_ALIAS = re.compile(
    r"^\s*(?:import\s+core[.]portfolio_manager\s+as\s+(\w+)"
    r"|from\s+core\s+import\s+portfolio_manager\b(?:\s+as\s+(\w+))?)",
    re.M,
)
# Wer die Datei als Text liest, folgt keinem Umzug: Pfad-Literal plus read_text/open.
_PFAD_LITERAL = re.compile(r"[\"'](?:core/)?portfolio_manager[.]py[\"']")
_LESEN = re.compile(r"[.]read_text\(|\bopen\(")


def _dokument() -> str:
    if not DOKUMENT.exists():
        pytest.fail(f"{DOKUMENT.relative_to(WURZEL)} fehlt — die Schnitt-Entscheidung")
    return DOKUMENT.read_text(encoding="utf-8")


def _abschnitt(text: str, nummer: int) -> str:
    treffer = re.search(rf"^## {nummer}\.(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert treffer, f"kein Abschnitt '## {nummer}.' im Dokument"
    return treffer.group(1)


def _zeilen(k: ast.AST) -> int:
    start = min([k.lineno, *(d.lineno for d in getattr(k, "decorator_list", []))])
    return k.end_lineno - start + 1


def _modul_namen(knoten: ast.stmt) -> list[str]:
    if isinstance(knoten, ast.Assign):
        return [z.id for z in knoten.targets if isinstance(z, ast.Name)]
    if isinstance(knoten, ast.AnnAssign) and isinstance(knoten.target, ast.Name):
        return [knoten.target.id]
    return []


def _spannen(datei: Path) -> dict[str, int]:
    """Funktionen, Klassen und Konstanten auf Modulebene sowie die Methoden von
    ``PortfolioManager`` bzw. der Themen-Mixins → Zeilen (ab Dekorator bis ``end_lineno``,
    wie ``regeln.py``). Importe und ``try``/``if`` zählen nicht: sie sind Kopf, kein Thema.
    """
    if not datei.exists():
        return {}
    baum = ast.parse(datei.read_text(encoding="utf-8"))
    ergebnis: dict[str, int] = {}
    for knoten in baum.body:
        if isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef)):
            ergebnis[knoten.name] = _zeilen(knoten)
        elif isinstance(knoten, ast.ClassDef):
            if knoten.name == KLASSE or knoten.name.endswith("Mixin"):
                for m in knoten.body:
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        ergebnis[m.name] = _zeilen(m)
            if knoten.name != KLASSE:
                ergebnis[knoten.name] = _zeilen(knoten)
        else:
            for name in _modul_namen(knoten):
                ergebnis[name] = _zeilen(knoten)
    return ergebnis


def _symbole_im_code() -> set[str]:
    """Was ``portfolio_manager.py`` heute an Symbolen trägt (ohne die Klasse selbst)."""
    return set(_spannen(PAKET / QUELLE)) - {KLASSE}


def _zuordnung(text: str) -> list[tuple[str, int, str]]:
    zeilen = _ZUORDNUNG.findall(text)
    falsch = [
        f"{datei}::{n}"
        for datei, n, _, ziel in zeilen
        if datei not in {"portfolio_manager.py", Path(ziel).name}
    ]
    assert (
        not falsch
    ), f"Anker zeigt weder auf portfolio_manager.py noch aufs Zielmodul: {falsch}"
    return [(n, int(z), ziel) for _, n, z, ziel in zeilen]


def _gemessen(zielmodule) -> dict[str, int]:
    gemessen = {**_spannen(PAKET / QUELLE)}
    for ziel in zielmodule:
        if ziel != QUELLE:
            gemessen.update(_spannen(PAKET / ziel))
    return gemessen


def _testdateien():
    eigene = Path(__file__).resolve()
    for datei in (PAKET / "tests").rglob("*.py"):
        if datei.resolve() != eigene:
            yield datei, datei.read_text(encoding="utf-8", errors="replace")


def _patch_namen() -> dict[str, set[str]]:
    """Name → Testdateien, die ihn über das Modulobjekt ``core.portfolio_manager`` patchen
    oder zuweisen."""
    namen: dict[str, set[str]] = {}
    for datei, text in _testdateien():
        treffer = set(_PATCH_PFAD.findall(text))
        for alias in {a or b or "portfolio_manager" for a, b in _ALIAS.findall(text)}:
            a = re.escape(alias)
            treffer |= set(
                re.findall(
                    rf"(?:patch[.]object|setattr)\(\s*{a}\s*,\s*[\"'](\w+)", text
                )
            )
            treffer |= set(re.findall(rf"\b{a}[.](\w+)\s*=(?!=)", text))
        for name in treffer:
            namen.setdefault(name, set()).add(datei.name)
    return namen


def _pfad_leser() -> set[str]:
    """Testdateien, die ``portfolio_manager.py`` über einen Dateipfad als Text lesen."""
    return {
        datei.name
        for datei, text in _testdateien()
        if _PFAD_LITERAL.search(text) and _LESEN.search(text)
    }


def test_jedes_symbol_hat_genau_ein_thema():
    zuordnung = _zuordnung(_dokument())
    assert (
        zuordnung
    ), "keine Zeile `portfolio_manager.py::SYMBOL` | Zeilen | `Zielmodul` |"

    namen = [n for n, _, _ in zuordnung]
    doppelt = sorted({n for n in namen if namen.count(n) > 1})
    assert not doppelt, f"mehr als einem Zielmodul zugeordnet: {doppelt}"

    im_code = _symbole_im_code()
    fehlt_im_dokument = sorted(im_code - set(namen))
    assert (
        not fehlt_im_dokument
    ), f"in {QUELLE}, aber ohne Thema im Dokument: {fehlt_im_dokument}"

    verloren = []
    for name, _, ziel in zuordnung:
        if name in im_code:
            continue
        if name not in _spannen(PAKET / ziel):
            verloren.append(f"{name} (weder in {QUELLE} noch in {ziel})")
    assert not verloren, f"im Dokument, aber nicht im Code: {verloren}"


def test_zielmodule_unter_800():
    text = _dokument()
    zuordnung = _zuordnung(text)
    zielmodule = {
        ziel: (int(plan), int(kopf))
        for ziel, plan, kopf, _, _ in _ZIELMODUL.findall(text)
    }
    assert zielmodule, "keine Zielmodul-Zeile `core/x.py` | Planzahl | Kopf |"

    unbekannt = sorted({ziel for _, _, ziel in zuordnung} - set(zielmodule))
    assert not unbekannt, f"zugeordnet, aber ohne Planzahl: {unbekannt}"

    gemessen = _gemessen(zielmodule)
    umzugs_inhalte = " ".join(inhalt for _, inhalt, _ in _UMZUG.findall(text))

    befunde = []
    for ziel, (plan, kopf) in sorted(zielmodule.items()):
        symbole = [n for n, _, z in zuordnung if z == ziel]
        summe = sum(gemessen[n] for n in symbole if n in gemessen)
        # eine Leerzeile je Symbol trennt es vom nächsten
        untergrenze = kopf + summe + len(symbole)
        if plan > DATEI_GRENZE:
            befunde.append(f"{ziel}: Planzahl {plan} > {DATEI_GRENZE}")
        if plan < untergrenze:
            befunde.append(
                f"{ziel}: Planzahl {plan} < Kopf {kopf} + Spannen {summe} "
                f"+ {len(symbole)} Trennzeilen = {untergrenze}"
            )
        # Über 150 bleibt eine Funktion nur mit eigenem Umzugsposten (Zerlegung in Schritte).
        for n in symbole:
            if gemessen.get(n, 0) > FUNKTION_GRENZE and f"`{n}`" not in umzugs_inhalte:
                befunde.append(
                    f"{ziel}: {n} hat {gemessen[n]} > {FUNKTION_GRENZE} "
                    "und keinen eigenen Umzugsposten"
                )
    assert not befunde, befunde


def test_jeder_patch_name_hat_eine_regel():
    gemessen = _patch_namen()
    # #4285 (H-5d): der eine Patch (§3) zog mit seinem Leser nach core.portfolio_bericht;
    # leer ist seither der erwartete Stand. Ob die Erhebung trägt, zeigt eine Probe — der
    # Modulname ist zusammengesetzt, damit die Probe selbst kein Patch-Ziel wird.
    probe = 'patch("core.' + 'portfolio_manager.probe_name")'
    assert _PATCH_PFAD.findall(probe) == ["probe_name"], "Erhebung kaputt?"
    # #4288 (H-5g): der letzte Pfad-Leser (G-0b-Schlüssel in test_architektur_fitness.py)
    # fiel mit dem Vertragseintrag; leer ist seither der erwartete Stand. Ob die Erhebung
    # trägt, zeigt eine Probe — das Literal ist zusammengesetzt, damit sie selbst keiner wird.
    probe = 'Path("core/' + 'portfolio_manager.py").read_text()'
    assert _PFAD_LITERAL.search(probe) and _LESEN.search(probe), "Erhebung kaputt?"
    leser = _pfad_leser()

    abschnitt = _abschnitt(_dokument(), 3)
    regeln = {name: weg for name, _, _, weg in _PATCH_ZEILE.findall(abschnitt)}
    regeln |= {datei: weg for datei, _, _, weg in _LESER_ZEILE.findall(abschnitt)}

    fehlt = {n: sorted(gemessen[n]) for n in sorted(set(gemessen) - set(regeln))}
    assert not fehlt, f"Patch-Ziele ohne Regel im Dokument (§3): {fehlt}"
    fehlt_leser = sorted(leser - set(regeln))
    assert not fehlt_leser, f"Pfad-Leser ohne Regel im Dokument (§3): {fehlt_leser}"

    ohne_weg = sorted(
        n
        for n in gemessen
        if not re.search(r"Modulobjekt|umschreiben", regeln[n], re.I)
    )
    assert not ohne_weg, f"Regel ohne Weg (Modulobjekt/umschreiben): {ohne_weg}"
    # Ein Pfad-Leser darf auch bleiben — wenn er die Datei selbst meint (Ratsche), nicht
    # den Inhalt, der umzieht. Das Dokument sagt dann, warum.
    ohne_weg = sorted(
        n for n in leser if not re.search(r"umschreiben|bleibt", regeln[n], re.I)
    )
    assert not ohne_weg, f"Pfad-Leser ohne Weg (umschreiben/bleibt): {ohne_weg}"


def test_umzuege_tragen_klasse_und_netz():
    text = _dokument()
    umzuege = _UMZUG.findall(text)
    assert umzuege, "keine Umzugszeile | H-5x | … | S/M |"
    for kennung, _, klasse in umzuege:
        assert klasse in {"S", "M"}, f"{kennung}: Klasse {klasse} — L wird geteilt"
        abschnitt = re.search(
            rf"^### {re.escape(kennung)}\b(.*?)(?=^### |^## |\Z)", text, re.M | re.S
        )
        assert abschnitt, f"{kennung}: kein Abschnitt '### {kennung}'"
        inhalt = abschnitt.group(1)
        assert "python scripts/mess_zuschnitt.py" in inhalt, f"{kennung}: kein Aufruf"
        assert (
            f"Groessenklasse: **{klasse}**" in inhalt
        ), f"{kennung}: Ausgabe fehlt oder nennt eine andere Klasse als {klasse}"

    # Jedes Zielmodul nennt ein Netz oder den Umzug, der es baut.
    for ziel, _, _, netz, _ in _ZIELMODUL.findall(text):
        assert netz.strip() and netz.strip() != "—", f"{ziel}: kein Netz genannt"


def test_die_ratsche_bleibt_stehen():
    """#4288 (H-5g): Unter der Schwelle fällt der Eintrag weg (Vorbild H-1k, #4240). Ab da
    prüft die Regel ``groessen`` (regeln.py) die Datei gegen ``datei_schwelle`` — und dieser
    Test, dass der Eintrag nicht zurückkommt und die Datei nicht still darüber wächst.
    """
    vertrag = tomllib.loads(VERTRAG.read_text(encoding="utf-8"))
    assert (
        QUELLE not in vertrag["groessen"]["ausnahmen_dateien"]
    ), f"{QUELLE} liegt unter der Schwelle; der Eintrag ist gestrichen (#4288)"
    zeilen = len((PAKET / QUELLE).read_text(encoding="utf-8").splitlines())
    schwelle = vertrag["groessen"]["datei_schwelle"]
    assert zeilen < schwelle, f"{QUELLE}: {zeilen} Zeilen, nicht unter {schwelle}"
