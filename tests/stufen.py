"""#3396 (ARC-E3) — Stufen und Uebergaben der Value Chain als Testmarker.

Die **pytest-Marker sind die Quelle**: ``@pytest.mark.vc3`` sagt, welche Stufe ein Test
absichert, ``@pytest.mark.h2`` welche Uebergabe. Das Allure-Label ``feature`` wird daraus
abgeleitet (``tests/conftest.py``), nicht mehr von Hand gesetzt.

Stufen und Uebergaben: ``docs/0_strategy_and_roadmap/value_chain/index.md`` und die
Stufenbeschreibungen ``vc1_research.md`` … ``vc6_reporting.md`` (H1 … H5). ``vc0`` ist der
Querschnitt (Plattform), den die Testsuite seit langem als eigene Stufe fuehrt.

Plan: ``docs/3396-testmarker-je-stufe-und-uebergabe/implementation_plan.md``.
"""

from __future__ import annotations

import ast
import tomllib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pytest

PAKET = Path(__file__).resolve().parents[1]  # ai_trading_bot/

# Marker -> Allure-Feature. Die Texte sind genau die, die bisher von Hand gesetzt wurden;
# test_stufen_marker.py prueft, dass die Ableitung dieselbe Marke erzeugt.
STUFEN = {
    "vc0": "VC-0 Platform Infrastructure",
    "vc1": "VC-1 Research & Analysis",
    "vc2": "VC-2 Portfolio Construction",
    "vc3": "VC-3 Trading & Execution",
    "vc4": "VC-4 Risk Management & Compliance",
    "vc5": "VC-5 Administration & Back-Office",
    "vc6": "VC-6 Reporting & Client Servicing",
}

# Uebergabe -> (liefernde Stufe, empfangende Stufe).
UEBERGABEN = {
    "h1": ("vc1", "vc2"),
    "h2": ("vc2", "vc4"),
    "h3": ("vc4", "vc3"),
    "h4": ("vc3", "vc5"),
    "h5": ("vc5", "vc6"),
}

ZUORDNUNG = frozenset(STUFEN) | frozenset(UEBERGABEN)

# Ratsche: so viele Test-Funktionen unter den testpaths tragen weder Stufe noch
# Uebergabe. Die Zahl darf nur sinken — ein neuer Test traegt seine Stufe.
# Gezaehlt mit tests_ohne_zuordnung(PAKET, TESTPFADE), Stand 2026-09-19 (nach Merge von
# #3494–#3499 und Zuordnung ihrer Tests). Vor der
# Migration der handgesetzten VC-Labels waren es 6206 Test-Funktionen.
OBERGRENZE_OHNE_ZUORDNUNG = (
    4592  # #3632: drei Smart-Exit-Testdateien entfernt, neue Tests tragen Stufen
)


def _testpfade() -> list[str]:
    with open(PAKET / "pyproject.toml", "rb") as f:
        return list(tomllib.load(f)["tool"]["pytest"]["ini_options"]["testpaths"])


TESTPFADE = _testpfade()


# ── Ableitung ────────────────────────────────────────────────────────────────


def abgeleitete_allure_marken(marker_namen) -> list:
    """Die Allure-Marken, die sich aus den Stufen-Markern eines Tests ergeben — dieselbe
    Form, die ``@allure.feature(...)`` unter allure-pytest erzeugt."""
    return [
        pytest.mark.allure_label(STUFEN[n], label_type="feature").mark
        for n in marker_namen
        if n in STUFEN
    ]


# ── Ratsche (statisch, unabhaengig von Sammlung und xdist) ───────────────────


def _marker_name(ausdruck) -> str | None:
    """``pytest.mark.vc3`` oder ``pytest.mark.vc3(...)`` -> ``"vc3"``."""
    if isinstance(ausdruck, ast.Call):
        ausdruck = ausdruck.func
    if (
        isinstance(ausdruck, ast.Attribute)
        and isinstance(ausdruck.value, ast.Attribute)
        and ausdruck.value.attr == "mark"
    ):
        return ausdruck.attr
    return None


def _namen(ausdruecke) -> set[str]:
    return {n for a in ausdruecke if (n := _marker_name(a))}


def _pytestmark(koerper) -> set[str]:
    namen: set[str] = set()
    for knoten in koerper:
        if isinstance(knoten, ast.Assign) and any(
            isinstance(z, ast.Name) and z.id == "pytestmark" for z in knoten.targets
        ):
            wert = knoten.value
            elemente = wert.elts if isinstance(wert, (ast.List, ast.Tuple)) else [wert]
            namen |= _namen(elemente)
    return namen


def _ist_test(knoten) -> bool:
    return isinstance(
        knoten, (ast.FunctionDef, ast.AsyncFunctionDef)
    ) and knoten.name.startswith("test")


def tests_ohne_zuordnung(wurzel: Path, pfade) -> list[str]:
    """Test-Funktionen ohne Stufen- und ohne Uebergabe-Marker, als ``datei::name``.

    Beruecksichtigt Marker an der Funktion, an der Klasse und ``pytestmark`` auf Modul-
    und Klassenebene — so, wie pytest sie vererbt."""
    ohne = []
    for pfad_text in pfade:
        for datei in sorted((wurzel / pfad_text).rglob("test_*.py")):
            rel = datei.relative_to(wurzel).as_posix()
            baum = ast.parse(datei.read_text(encoding="utf-8-sig"))
            modul = _pytestmark(baum.body)
            for knoten in baum.body:
                if _ist_test(knoten):
                    if not (modul | _namen(knoten.decorator_list)) & ZUORDNUNG:
                        ohne.append(f"{rel}::{knoten.name}")
                elif isinstance(knoten, ast.ClassDef) and knoten.name.startswith(
                    "Test"
                ):
                    klasse = (
                        modul | _namen(knoten.decorator_list) | _pytestmark(knoten.body)
                    )
                    for methode in knoten.body:
                        if (
                            _ist_test(methode)
                            and not (klasse | _namen(methode.decorator_list))
                            & ZUORDNUNG
                        ):
                            ohne.append(f"{rel}::{knoten.name}::{methode.name}")
    return ohne


# ── Auswertung je Stufe und Uebergabe ────────────────────────────────────────


@dataclass
class Auswertung:
    je_stufe: Counter = field(default_factory=Counter)
    je_uebergabe: Counter = field(default_factory=Counter)
    ohne_zuordnung: int = 0
    gesamt: int = 0

    @property
    def luecken_stufen(self) -> list[str]:
        return [s for s in STUFEN if not self.je_stufe[s]]

    @property
    def luecken_uebergaben(self) -> list[str]:
        # Eine Uebergabe ist nur durch Tests der Uebergabe selbst abgesichert — gruene
        # Tests auf beiden Seiten beweisen nicht, dass der Pfad zwischen ihnen existiert.
        return [h for h in UEBERGABEN if not self.je_uebergabe[h]]

    def als_text(self) -> str:
        zeilen = [f"Stufenbericht (#3396) — {self.gesamt} gesammelte Tests"]
        for s, name in STUFEN.items():
            n = self.je_stufe[s]
            zeilen.append(f"  {name:<40} {n:>6}{'   LUECKE' if not n else ''}")
        for h, (von, nach) in UEBERGABEN.items():
            n = self.je_uebergabe[h]
            titel = f"{h.upper()} {STUFEN[von][:4]} -> {STUFEN[nach][:4]}"
            zeilen.append(f"  {titel:<40} {n:>6}{'   LUECKE' if not n else ''}")
        zeilen.append(
            f"  {'ohne Stufe und ohne Uebergabe':<40} {self.ohne_zuordnung:>6}"
        )
        return "\n".join(zeilen)


def auswertung(marker_je_test) -> Auswertung:
    """``marker_je_test``: je gesammeltem Test die Namen seiner Marker."""
    bericht = Auswertung()
    for namen in marker_je_test:
        namen = set(namen)
        bericht.gesamt += 1
        for s in namen & set(STUFEN):
            bericht.je_stufe[s] += 1
        for h in namen & set(UEBERGABEN):
            bericht.je_uebergabe[h] += 1
        if not namen & ZUORDNUNG:
            bericht.ohne_zuordnung += 1
    return bericht
