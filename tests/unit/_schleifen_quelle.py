"""#4006 — Quelltext der Handelsschleife: Dirigent + Schritte, in Aufrufreihenfolge.

Plan: ``docs/4006-vorarbeit-g2-quelltext-waechter-entkoppeln/implementation_plan.md`` §2.

Fünf Wächter lesen den Quelltext von ``TradingLoopMixin.live_trading_loop`` — vier Unit-Tests
und die Abnahme #3489. Schneidet G-2 (#3823) Blöcke aus der Schleife in benannte Schritte,
verlässt ihr Text die Funktion; die Wächter verlören ihren Gegenstand. Dieser Helfer liefert
deshalb den **Dirigenten plus seine Schritte**, in der Reihenfolge des ersten Aufrufs.

Ein Schritt ist ein Aufruf ``self._<name>(...)`` im Dirigenten, dessen ``<name>`` als Methode
**derselben Klasse in derselben Datei** definiert ist. Anders als ``_uebergabe_quelle.py``
löst dieser Helfer **ohne Import** auf: Er liest ``core/engine/trading_loop.py`` als Text und
parst ihn mit ``ast``. Ein Import von ``core.engine`` bootet die ganze Engine im Testprozess
(``core/lease.py`` erklärt, warum) — und die Abnahme #3489 vermeidet ihn ausdrücklich. Eine
Regel für beide Prüfer-Arten, damit Unit-Wächter und Abnahme nicht verschiedene Texte sehen.

Fehlen Datei, Klasse oder Dirigent, **erhebt** der Helfer ``LookupError`` — leerer Text machte
alle Wächter stillschweigend wahr.

Helfermodul (wie ``_uebergabe_quelle.py``) — pytest sammelt es nicht. Eigentests:
``tests/unit/test_schleifen_quelle.py``.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from pathlib import Path

PFAD = Path(__file__).resolve().parents[2] / "core" / "engine" / "trading_loop.py"
KLASSE = "TradingLoopMixin"
DIRIGENT = "live_trading_loop"

_Methode = ast.FunctionDef | ast.AsyncFunctionDef


def _lesen(pfad: Path) -> tuple[list[str], dict[str, _Methode], _Methode]:
    """Zeilen der Datei, Methoden der Klasse, Dirigent."""
    try:
        text = pfad.read_text(encoding="utf-8")
    except OSError as fehler:
        raise LookupError(f"{pfad} nicht lesbar: {fehler}") from fehler
    klasse = next(
        (
            k
            for k in ast.parse(text).body
            if isinstance(k, ast.ClassDef) and k.name == KLASSE
        ),
        None,
    )
    if klasse is None:
        raise LookupError(
            f"Klasse {KLASSE} nicht in {pfad} — ohne Quelle würde jeder Wächter "
            "stillschweigend wahr"
        )
    methoden = {
        m.name: m
        for m in klasse.body
        if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    if DIRIGENT not in methoden:
        raise LookupError(
            f"Dirigent {KLASSE}.{DIRIGENT} nicht in {pfad} — ohne Quelle würde jeder "
            "Wächter stillschweigend wahr"
        )
    return text.splitlines(keepends=True), methoden, methoden[DIRIGENT]


def _schritte(methoden: dict[str, _Methode], dirigent: _Methode) -> list[str]:
    aufrufe = sorted(
        (n.lineno, n.col_offset, n.func.attr)
        for n in ast.walk(dirigent)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "self"
        and n.func.attr.startswith("_")
        and n.func.attr in methoden
        and n.func.attr != DIRIGENT
    )
    return list(dict.fromkeys(name for _, _, name in aufrufe))


def _zeilen(zeilen: list[str], knoten: _Methode) -> str:
    """Wie ``inspect.getsource``: ab Dekorator, mit Einrückung, samt Kommentaren am Ende
    des Rumpfs — deshalb ``inspect.getblock`` statt ``end_lineno``."""
    start = min([knoten.lineno, *(d.lineno for d in knoten.decorator_list)])
    return "".join(inspect.getblock(zeilen[start - 1 :]))


def schritte(pfad: Path = PFAD) -> list[str]:
    """Die Schritte des Dirigenten, in der Reihenfolge ihres ersten Aufrufs."""
    _, methoden, dirigent = _lesen(pfad)
    return _schritte(methoden, dirigent)


def quelle(pfad: Path = PFAD) -> str:
    """Ersatz für ``inspect.getsource(live_trading_loop)``: Dirigent, dann seine Schritte."""
    zeilen, methoden, dirigent = _lesen(pfad)
    return _zeilen(zeilen, dirigent) + "".join(
        _zeilen(zeilen, methoden[name]) for name in _schritte(methoden, dirigent)
    )


def ast_quelle(pfad: Path = PFAD) -> tuple[ast.Module, str]:
    """Für Prüfer am AST (#3489): der geparste Text und der Text selbst."""
    text = quelle(pfad)
    return ast.parse(textwrap.dedent(text)), text
