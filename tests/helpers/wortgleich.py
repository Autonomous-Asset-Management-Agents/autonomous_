"""AST-Vergleich fuer Zerlegungen in Schritte mit Zustandsobjekt (#4075, ARC-E6 G-4i).

Eine lange Funktion wird in Schritte ``async def _schritt_x(z)`` zerlegt. Jeder Schritt ist ein
Block des alten Rumpfs, wortgleich bis auf genau drei angemeldete Abbildungen:

1. ``z.<feld>`` statt ``<feld>`` — Werte, die zwischen Bloecken fliessen, liegen im Zustandsobjekt;
2. ``return <sentinel>`` am Blockende — der Block faellt durch, der Dirigent ruft den naechsten;
3. wiederholte lokale Importe am Anfang eines Schritts — ein lokaler Import des alten Rumpfs
   steht in jedem Schritt, der den Namen braucht.

``unterschiede`` hebt diese drei Abbildungen auf und vergleicht dann ``ast.dump`` beider Seiten.
Kommentare und Zeilenumbrueche zaehlen dabei nicht, jeder andere Unterschied schon.

Wiederverwendbar fuer G-7b und G-7c (gleiches Muster).
"""

from __future__ import annotations

import ast
import difflib
from typing import Iterable, Sequence


class _FeldZuName(ast.NodeTransformer):
    def __init__(self, objekt: str, felder: frozenset[str]) -> None:
        self.objekt = objekt
        self.felder = felder

    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
        if (
            isinstance(node.value, ast.Name)
            and node.value.id == self.objekt
            and node.attr in self.felder
        ):
            return ast.Name(id=node.attr, ctx=node.ctx)
        return self.generic_visit(node)


def import_dumps(quellen: Iterable[str]) -> frozenset[str]:
    """``ast.dump`` je Import-Zeile, z. B. ``"import json"``."""
    return frozenset(ast.dump(ast.parse(q).body[0]) for q in quellen)


def _einzeln(s: ast.Import | ast.ImportFrom) -> list[ast.Import | ast.ImportFrom]:
    """``from x import a, b`` → ``from x import a`` und ``from x import b``."""
    if isinstance(s, ast.Import):
        return [ast.Import(names=[n]) for n in s.names]
    return [ast.ImportFrom(module=s.module, names=[n], level=s.level) for n in s.names]


def ohne_importe(
    anweisungen: Sequence[ast.stmt], importe: frozenset[str]
) -> list[ast.stmt]:
    """Oberste Anweisungen ohne die angemeldeten Importe (verschachtelte bleiben).

    isort legt einen wiederholten Import mit einem urspruenglichen aus demselben Modul zusammen
    (``from x import a, b``). Deshalb zaehlt jeder Name einzeln; uebrig bleibt der Import mit
    den Namen, die nicht angemeldet sind.
    """
    ergebnis: list[ast.stmt] = []
    for s in anweisungen:
        if not isinstance(s, (ast.Import, ast.ImportFrom)):
            ergebnis.append(s)
            continue
        rest = [e for e in _einzeln(s) if ast.dump(e) not in importe]
        if len(rest) == len(s.names):
            ergebnis.append(s)
        elif rest:
            ergebnis.append(type(s)(**{**vars(s), "names": [e.names[0] for e in rest]}))
    return ergebnis


def schritt_rumpf(
    schritt: ast.AsyncFunctionDef | ast.FunctionDef,
    *,
    objekt: str,
    felder: frozenset[str],
    importe: frozenset[str],
    sentinel: str,
) -> list[ast.stmt]:
    """Rumpf eines Schritts mit aufgehobenen Abbildungen (ohne Docstring)."""
    rumpf = list(schritt.body)
    if (
        rumpf
        and isinstance(rumpf[0], ast.Expr)
        and isinstance(rumpf[0].value, ast.Constant)
        and isinstance(rumpf[0].value.value, str)
    ):
        rumpf = rumpf[1:]
    rumpf = ohne_importe(rumpf, importe)
    if (
        rumpf
        and isinstance(rumpf[-1], ast.Return)
        and isinstance(rumpf[-1].value, ast.Name)
        and rumpf[-1].value.id == sentinel
    ):
        rumpf = rumpf[:-1]
    umgesetzt = _FeldZuName(objekt, felder)
    return [umgesetzt.visit(ast.parse(ast.unparse(s)).body[0]) for s in rumpf]


def _dump(anweisungen: Sequence[ast.stmt]) -> str:
    return ast.dump(ast.Module(body=list(anweisungen), type_ignores=[]), indent=1)


def unterschiede(neu: Sequence[ast.stmt], alt: Sequence[ast.stmt]) -> str:
    """Leerer String, wenn gleich; sonst ein Unified-Diff der beiden ``ast.dump``."""
    a, n = _dump(alt), _dump(neu)
    if a == n:
        return ""
    return "\n".join(
        difflib.unified_diff(
            a.splitlines(), n.splitlines(), "alt", "neu", lineterm="", n=2
        )
    )


def ausdruck_gleich(
    neu: ast.expr, alt: ast.expr, *, objekt: str, felder: frozenset[str]
) -> bool:
    """Ein Ausdruck (z. B. eine Bedingung) ist gleich, bis auf ``z.<feld>``."""
    kopie = ast.parse(ast.unparse(neu), mode="eval").body
    return ast.dump(_FeldZuName(objekt, felder).visit(kopie)) == ast.dump(alt)
