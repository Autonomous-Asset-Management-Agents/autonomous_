"""#4184 (H-2) — die Schnitt-Entscheidung für ``trading_loop.py`` deckt die Datei vollständig.

Plan: ``docs/4184-*/implementation_plan.md`` §5/§6. Das Dokument
``docs/3738-arc-e6-gestalt/H2_SCHNITT_trading_loop.md`` ordnet jede Methode von
``TradingLoopMixin`` und jede Funktion/Klasse auf Modulebene **genau einem** Zielmodul zu,
nennt je Zielmodul eine Planzahl (≤ 800) und je Umzug eine Größenklasse (S/M) samt
``mess_zuschnitt``-Aufruf.

Gelesen wird ohne Import (``ast``, Muster ``_schleifen_quelle.py``): ein Import von
``core.engine`` bootet die Engine im Testprozess. Ein Symbol gilt als gefunden, wenn es in
``trading_loop.py`` **oder** in seinem Zielmodul steht — so bleibt der Test über die Umzüge
H-2c ff. hinweg wahr und prüft zugleich, dass ein Symbol nur dorthin wandert, wo das
Dokument es hinschickt.
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
QUELLE = "core/engine/trading_loop.py"
KLASSE = "TradingLoopMixin"
DOKUMENT = WURZEL / "docs" / "3738-arc-e6-gestalt" / "H2_SCHNITT_trading_loop.md"
VERTRAG = PAKET / "tests" / "architecture" / "vertrag.toml"

DATEI_GRENZE = 800
FUNKTION_GRENZE = 150

# Vor dem Umzug ``trading_loop.py::SYMBOL``, danach ``<zielmodul>.py::SYMBOL`` — der
# Doku-Anker (scripts/check_doc_anchors.py, NAME_RE) bricht genau dann, wenn der Name wandert.
_ZUORDNUNG = re.compile(
    r"^\|\s*`(\w+\.py)::(\w+)`\s*\|\s*(\d+)\s*\|\s*`(core/engine/\w+\.py)`\s*\|",
    re.M,
)
_ZIELMODUL = re.compile(
    r"^\|\s*`(core/engine/\w+\.py)`\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|([^|]*)\|([^|]*)\|\s*$",
    re.M,
)
_UMZUG = re.compile(r"^\|\s*(H-2[a-z])\s*\|([^|]*)\|\s*([SML])\s*\|", re.M)


def _dokument() -> str:
    if not DOKUMENT.exists():
        pytest.fail(f"{DOKUMENT.relative_to(WURZEL)} fehlt — die Schnitt-Entscheidung")
    return DOKUMENT.read_text(encoding="utf-8")


def _spannen(datei: Path) -> dict[str, int]:
    """Funktionen/Klassen auf Modulebene und Methoden von ``TradingLoopMixin`` bzw. der
    Themen-Mixins → Zeilen (ab Dekorator bis ``end_lineno``, wie ``regeln.py``)."""
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
            if knoten.name.endswith("Mixin"):
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
        if datei not in {"trading_loop.py", Path(ziel).name}
    ]
    assert (
        not falsch
    ), f"Anker zeigt weder auf trading_loop.py noch aufs Zielmodul: {falsch}"
    return [(n, int(z), ziel) for _, n, z, ziel in zeilen]


def _symbole_im_code() -> set[str]:
    """Was ``trading_loop.py`` heute an Symbolen trägt (ohne die Mixin-Klasse selbst)."""
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


def test_jedes_symbol_hat_genau_ein_thema():
    zuordnung = _zuordnung(_dokument())
    assert zuordnung, "keine Zeile `trading_loop.py::SYMBOL` | Zeilen | `Zielmodul` |"

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
    assert zielmodule, "keine Zielmodul-Zeile `core/engine/x.py` | Planzahl | Kopf |"

    unbekannt = sorted({ziel for _, _, ziel in zuordnung} - set(zielmodule))
    assert not unbekannt, f"zugeordnet, aber ohne Planzahl: {unbekannt}"

    gemessen = {**_spannen(PAKET / QUELLE)}
    for ziel in zielmodule:
        if ziel != QUELLE:
            gemessen.update(_spannen(PAKET / ziel))

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
        for n in symbole:
            if gemessen.get(n, 0) > FUNKTION_GRENZE:
                befunde.append(f"{ziel}: {n} hat {gemessen[n]} > {FUNKTION_GRENZE}")
    assert not befunde, befunde


def test_umzuege_tragen_klasse():
    text = _dokument()
    umzuege = _UMZUG.findall(text)
    assert umzuege, "keine Umzugszeile | H-2x | … | S/M |"
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


def test_vertragsbegruendung_verweist_auf_schnitt():
    vertrag = tomllib.loads(VERTRAG.read_text(encoding="utf-8"))
    eintrag = vertrag["groessen"]["ausnahmen_dateien"].get(QUELLE)
    if eintrag is None:
        # #4251 (H-2j): Eintrag gestrichen — nur zulässig, solange der Kern unter der
        # allgemeinen Schwelle liegt (sonst bräche regeln.pruefe_groessen ohnehin).
        zeilen = len((PAKET / QUELLE).read_text(encoding="utf-8").splitlines())
        assert zeilen < vertrag["groessen"]["datei_schwelle"], zeilen
        return
    assert eintrag["zeilen"] <= 3008, "Ratsche: Umzüge senken die Zahl, erhöhen sie nie"
    assert "H2_SCHNITT_trading_loop.md" in eintrag["begruendung"]
