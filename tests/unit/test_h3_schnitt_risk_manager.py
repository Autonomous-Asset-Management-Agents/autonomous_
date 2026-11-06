"""#4185 (H-3) — die Schnitt-Entscheidung für ``risk_manager.py`` deckt die Datei vollständig.

Plan: ``docs/4185-*/implementation_plan.md`` §5/§6. Das Dokument
``docs/3738-arc-e6-gestalt/H3_SCHNITT_risk_manager.md`` ordnet jede Methode von
``RiskManager`` und jede Funktion auf Modulebene **genau einem** Zielmodul zu, nennt je
Zielmodul eine Planzahl (≤ 800) und ein Netz, je Patch-Ziel der Tests den Weg und je Umzug
eine Größenklasse (S/M) samt ``mess_zuschnitt``-Aufruf.

Gelesen wird ohne Import (``ast``), Muster ``test_h2_schnitt_trading_loop.py``. Ein Symbol
gilt als gefunden, wenn es in ``risk_manager.py`` **oder** in seinem Zielmodul steht — so
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
QUELLE = "core/risk_manager.py"
KLASSE = "RiskManager"
DOKUMENT = WURZEL / "docs" / "3738-arc-e6-gestalt" / "H3_SCHNITT_risk_manager.md"
VERTRAG = PAKET / "tests" / "architecture" / "vertrag.toml"

DATEI_GRENZE = 800
FUNKTION_GRENZE = 150

# Vor dem Umzug ``risk_manager.py::SYMBOL``, danach ``<zielmodul>.py::SYMBOL`` — der
# Doku-Anker (scripts/check_doc_anchors.py, NAME_RE) bricht genau dann, wenn der Name wandert.
_ZUORDNUNG = re.compile(
    r"^\|\s*`(\w+\.py)::(\w+)`\s*\|\s*(\d+)\s*\|\s*`(core/\w+\.py)`\s*\|",
    re.M,
)
_ZIELMODUL = re.compile(
    r"^\|\s*`(core/\w+\.py)`\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|([^|]*)\|([^|]*)\|\s*$",
    re.M,
)
_UMZUG = re.compile(r"^\|\s*(H-3[a-z])\s*\|([^|]*)\|\s*([SML])\s*\|", re.M)
_PATCH_ZEILE = re.compile(
    r"^\|\s*`(\w+)`\s*\|\s*(\d+)[^|]*\|([^|]*)\|([^|]*)\|\s*$", re.M
)

# Patch-Ziele am Modulobjekt: als Pfad-String oder über einen Modul-Alias.
_PATCH_PFAD = re.compile(r"core[.]risk_manager[.]([A-Za-z_]\w*)")
_ALIAS = re.compile(
    r"^\s*(?:import\s+core[.]risk_manager\s+as\s+(\w+)"
    r"|from\s+core\s+import\s+risk_manager\s+as\s+(\w+))",
    re.M,
)


def _dokument() -> str:
    if not DOKUMENT.exists():
        pytest.fail(f"{DOKUMENT.relative_to(WURZEL)} fehlt — die Schnitt-Entscheidung")
    return DOKUMENT.read_text(encoding="utf-8")


def _abschnitt(text: str, nummer: int) -> str:
    treffer = re.search(rf"^## {nummer}\.(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert treffer, f"kein Abschnitt '## {nummer}.' im Dokument"
    return treffer.group(1)


def _spannen(datei: Path) -> dict[str, int]:
    """Funktionen auf Modulebene und Methoden von ``RiskManager`` bzw. der Themen-Mixins
    → Zeilen (ab Dekorator bis ``end_lineno``, wie ``regeln.py``)."""
    if not datei.exists():
        return {}
    baum = ast.parse(datei.read_text(encoding="utf-8"))
    ergebnis: dict[str, int] = {}

    def zeilen(k: ast.AST) -> int:
        start = min([k.lineno, *(d.lineno for d in getattr(k, "decorator_list", []))])
        return k.end_lineno - start + 1

    for knoten in baum.body:
        if isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef)):
            ergebnis[knoten.name] = zeilen(knoten)
        elif isinstance(knoten, ast.ClassDef):
            if knoten.name == KLASSE or knoten.name.endswith("Mixin"):
                for m in knoten.body:
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        ergebnis[m.name] = zeilen(m)
            if knoten.name != KLASSE:
                ergebnis[knoten.name] = zeilen(knoten)
    return ergebnis


def _zuordnung(text: str) -> list[tuple[str, int, str]]:
    zeilen = _ZUORDNUNG.findall(text)
    falsch = [
        f"{datei}::{n}"
        for datei, n, _, ziel in zeilen
        if datei not in {"risk_manager.py", Path(ziel).name}
    ]
    assert (
        not falsch
    ), f"Anker zeigt weder auf risk_manager.py noch aufs Zielmodul: {falsch}"
    return [(n, int(z), ziel) for _, n, z, ziel in zeilen]


def _symbole_im_code() -> set[str]:
    """Was ``risk_manager.py`` heute an Symbolen trägt (ohne die Klasse selbst)."""
    baum = ast.parse((PAKET / QUELLE).read_text(encoding="utf-8"))
    namen: set[str] = set()
    for knoten in baum.body:
        if isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef)):
            namen.add(knoten.name)
        elif isinstance(knoten, ast.ClassDef):
            if knoten.name == KLASSE:
                namen |= {
                    m.name
                    for m in knoten.body
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
            else:
                namen.add(knoten.name)
    return namen


def _gemessen(zielmodule) -> dict[str, int]:
    gemessen = {**_spannen(PAKET / QUELLE)}
    for ziel in zielmodule:
        if ziel != QUELLE:
            gemessen.update(_spannen(PAKET / ziel))
    return gemessen


def _patch_namen() -> dict[str, set[str]]:
    """Name → Testdateien, die ihn über das Modulobjekt ``core.risk_manager`` patchen."""
    eigene = Path(__file__).resolve()
    namen: dict[str, set[str]] = {}
    for datei in (PAKET / "tests").rglob("*.py"):
        if datei.resolve() == eigene:
            continue
        text = datei.read_text(encoding="utf-8", errors="replace")
        treffer = set(_PATCH_PFAD.findall(text))
        for alias in {a or b for a, b in _ALIAS.findall(text)}:
            treffer |= set(
                re.findall(
                    rf"(?:patch[.]object|setattr)\(\s*{re.escape(alias)}\s*,\s*[\"'](\w+)",
                    text,
                )
            )
        for name in treffer:
            namen.setdefault(name, set()).add(datei.name)
    return namen


def test_jedes_symbol_hat_genau_ein_thema():
    zuordnung = _zuordnung(_dokument())
    assert zuordnung, "keine Zeile `risk_manager.py::SYMBOL` | Zeilen | `Zielmodul` |"

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
    assert gemessen, "keine Patches auf core.risk_manager gefunden — Erhebung kaputt?"

    regeln: dict[str, str] = {}
    for name, _, _, weg in _PATCH_ZEILE.findall(_abschnitt(_dokument(), 3)):
        regeln[name] = weg

    fehlt = {n: sorted(gemessen[n]) for n in sorted(set(gemessen) - set(regeln))}
    assert not fehlt, f"Patch-Ziele ohne Regel im Dokument (§3): {fehlt}"
    ohne_weg = sorted(
        n
        for n in gemessen
        if not re.search(r"Modulobjekt|umschreiben", regeln[n], re.I)
    )
    assert not ohne_weg, f"Regel ohne Weg (Modulobjekt/umschreiben): {ohne_weg}"


def test_umzuege_tragen_klasse():
    text = _dokument()
    umzuege = _UMZUG.findall(text)
    assert umzuege, "keine Umzugszeile | H-3x | … | S/M |"
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
    vertrag = tomllib.loads(VERTRAG.read_text(encoding="utf-8"))
    groessen = vertrag["groessen"]
    zeilen = VERTRAG.read_text(encoding="utf-8").splitlines()
    abschnitt = zeilen.index("[groessen.ausnahmen_dateien]")

    if QUELLE not in groessen["ausnahmen_dateien"]:
        # #4268 (H-3f): unter datei_schwelle fällt der Eintrag weg (Entscheidung §6). Dann muss
        # die Datei wirklich darunter liegen, und der Kommentar mit dem Verweis auf den Schnitt
        # bleibt im Abschnitt stehen, damit der Rest nach 800 (H-3g, H-3i) auffindbar bleibt.
        zahl = len((PAKET / QUELLE).read_text(encoding="utf-8").splitlines())
        schwelle = groessen["datei_schwelle"]
        assert (
            zahl < schwelle
        ), f"{QUELLE}: {zahl} Zeilen ohne Eintrag, Schwelle {schwelle}"
        rest = zeilen[abschnitt:]
        ende = next(
            (i for i, z in enumerate(rest[1:], 1) if z.startswith("[")), len(rest)
        )
        kommentar = "\n".join(z for z in rest[:ende] if z.startswith("#"))
        assert (
            "H3_SCHNITT_risk_manager.md" in kommentar and "#4268 (H-3f)" in kommentar
        ), f"kein Kommentar zu {QUELLE} im Abschnitt, der auf den Schnitt verweist"
        return

    zahl = groessen["ausnahmen_dateien"][QUELLE]
    assert zahl <= 1931, f"Ratsche gestiegen: {zahl} > 1931"

    eintrag = next(
        i
        for i, z in enumerate(zeilen)
        if i > abschnitt and z.startswith(f'"{QUELLE}" = ')
    )
    kommentar = []
    for z in reversed(zeilen[:eintrag]):
        if not z.startswith("#"):
            break
        kommentar.append(z)
    assert any(
        "H3_SCHNITT_risk_manager.md" in z for z in kommentar
    ), f"kein Kommentar über dem Eintrag {QUELLE}, der auf den Schnitt verweist"
