"""#4006 — Quelltext der Handelsschleife: Dirigent + Schritte, in Aufrufreihenfolge.

Plan: ``docs/4006-vorarbeit-g2-quelltext-waechter-entkoppeln/implementation_plan.md`` §2.

Fünf Wächter lesen den Quelltext von ``TradingLoopMixin.live_trading_loop`` — vier Unit-Tests
und die Abnahme #3489. Schneidet G-2 (#3823) Blöcke aus der Schleife in benannte Schritte,
verlässt ihr Text die Funktion; die Wächter verlören ihren Gegenstand. Dieser Helfer liefert
deshalb den **Dirigenten plus seine Schritte**, in der Reihenfolge des ersten Aufrufs.

Ein Schritt ist ein Aufruf ``self._<name>(...)`` im Dirigenten. ``<name>`` ist als Methode von
``TradingLoopMixin`` definiert — im Kern oder, seit #4243 (H-2b), in einer seiner direkten
Mixin-Basen, die der Kern per ``from core.engine.<m> import <X>Mixin`` holt. Wie in der MRO
gewinnt der Kern vor den Basen, die Basen gelten in der Reihenfolge der Klassenbasen. Ruft der
Dirigent ``self._<name>()`` auf, ohne dass es dort definiert ist, **erhebt** der Helfer
``LookupError`` — sonst fiele ein umgezogener Schritt still aus dem Text, und jeder Wächter
prüfte weniger. Aufgelöst wird eine Ebene; nach der Schnitt-Entscheidung #4184 §2 sind alle
Themen-Mixins direkte Basen.

Anders als ``_uebergabe_quelle.py`` löst dieser Helfer **ohne Import** auf: Er liest
``core/engine/trading_loop.py`` und die Dateien seiner Basen als Text und parst sie mit
``ast``. Ein Import von ``core.engine`` bootet die ganze Engine im Testprozess
(``core/lease.py`` erklärt, warum) — und die Abnahme #3489 vermeidet ihn ausdrücklich. Eine
Regel für beide Prüfer-Arten, damit Unit-Wächter und Abnahme nicht verschiedene Texte sehen.

Für Quelltextleser, die nicht den Dirigenten, sondern „irgendwo in der Handelsschleife" suchen,
liefert ``text_handelsschleife`` den Kern plus jedes vorhandene Themen-Modul aus
``ZIELMODULE`` (Teilstrings), ``texte_handelsschleife`` dieselben Texte je Datei (für ``ast``).

Fehlen Datei, Klasse, Dirigent, Basis oder Schritt, **erhebt** der Helfer ``LookupError`` —
leerer Text machte alle Wächter stillschweigend wahr.

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
#: Die Zielmodule aus der Schnitt-Entscheidung #4184 §2, ohne den Kern — in der Reihenfolge,
#: in der ``texte_handelsschleife`` sie anhängt. Jede Basis des Kerns muss hier stehen
#: (``test_zielmodule_umfassen_jede_basis_des_kerns``).
ZIELMODULE = (
    "zyklus.py",
    "symbol_schluessel.py",
    "ausstieg_hebel.py",
    "positionsbuch.py",
    "schleifen_stops.py",
    "schleifen_start.py",
    "marktdaten.py",
    "zyklus_vorlauf.py",
    "zyklus_kontext.py",
    "zyklus_bewertung.py",
)

_Methode = ast.FunctionDef | ast.AsyncFunctionDef
#: Methodenname → (Zeilen der Herkunftsdatei, Knoten)
_Tabelle = dict[str, tuple[list[str], _Methode]]


def _parsen(pfad: Path) -> tuple[str, ast.Module]:
    try:
        text = pfad.read_text(encoding="utf-8")
    except OSError as fehler:
        raise LookupError(f"{pfad} nicht lesbar: {fehler}") from fehler
    return text, ast.parse(text)


def _klasse(baum: ast.Module, name: str) -> ast.ClassDef | None:
    return next(
        (k for k in baum.body if isinstance(k, ast.ClassDef) and k.name == name),
        None,
    )


def _methoden(klasse: ast.ClassDef) -> dict[str, _Methode]:
    return {
        m.name: m
        for m in klasse.body
        if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _basen(
    baum: ast.Module, klasse: ast.ClassDef, pfad: Path
) -> list[tuple[Path, ast.ClassDef, list[str]]]:
    """Die direkten Basen der Klasse: (Datei, Klassenknoten, Zeilen) — ohne Import."""
    ergebnis = []
    for basis in klasse.bases:
        if not isinstance(basis, ast.Name):
            raise LookupError(
                f"Basis {ast.unparse(basis)} von {KLASSE} ist kein einfacher Name"
            )
        herkunft = next(
            (
                (imp.module.rpartition(".")[2], alias.name)
                for imp in baum.body
                if isinstance(imp, ast.ImportFrom)
                and imp.level == 0
                and imp.module
                and imp.module.rpartition(".")[0] == "core.engine"
                for alias in imp.names
                if (alias.asname or alias.name) == basis.id
            ),
            None,
        )
        if herkunft is None:
            raise LookupError(
                f"Basis {basis.id} von {KLASSE} wird in {pfad} nicht per "
                "'from core.engine.<m> import ...' importiert"
            )
        modul, name = herkunft
        datei = pfad.parent / f"{modul}.py"
        text, basis_baum = _parsen(datei)
        basis_klasse = _klasse(basis_baum, name)
        if basis_klasse is None:
            raise LookupError(f"Klasse {name} (Basis von {KLASSE}) nicht in {datei}")
        ergebnis.append((datei, basis_klasse, text.splitlines(keepends=True)))
    return ergebnis


def _lesen(pfad: Path) -> tuple[_Tabelle, _Methode, list[Path]]:
    """Methodentabelle über Kern und Basen, Dirigent, Dateien der Basen."""
    text, baum = _parsen(pfad)
    klasse = _klasse(baum, KLASSE)
    if klasse is None:
        raise LookupError(
            f"Klasse {KLASSE} nicht in {pfad} — ohne Quelle würde jeder Wächter "
            "stillschweigend wahr"
        )
    zeilen = text.splitlines(keepends=True)
    tabelle: _Tabelle = {n: (zeilen, m) for n, m in _methoden(klasse).items()}
    if DIRIGENT not in tabelle:
        raise LookupError(
            f"Dirigent {KLASSE}.{DIRIGENT} nicht in {pfad} — ohne Quelle würde jeder "
            "Wächter stillschweigend wahr"
        )
    dirigent = tabelle[DIRIGENT][1]
    basen = _basen(baum, klasse, pfad)
    for _, basis_klasse, basis_zeilen in basen:
        for n, m in _methoden(basis_klasse).items():
            tabelle.setdefault(n, (basis_zeilen, m))  # der Kern gewinnt, wie in der MRO
    return tabelle, dirigent, [datei for datei, _, _ in basen]


def _schritte(tabelle: _Tabelle, dirigent: _Methode) -> list[str]:
    aufrufe = sorted(
        (n.lineno, n.col_offset, n.func.attr)
        for n in ast.walk(dirigent)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "self"
        and n.func.attr.startswith("_")
        and n.func.attr != DIRIGENT
    )
    namen = list(dict.fromkeys(name for _, _, name in aufrufe))
    fehlend = [name for name in namen if name not in tabelle]
    if fehlend:
        raise LookupError(
            f"Schritt ohne Definition in {KLASSE} und seinen Basen: {', '.join(fehlend)} "
            "— er fiele still aus dem Text der Schleife"
        )
    return namen


def _zeilen(zeilen: list[str], knoten: _Methode) -> str:
    """Wie ``inspect.getsource``: ab Dekorator, mit Einrückung, samt Kommentaren am Ende
    des Rumpfs — deshalb ``inspect.getblock`` statt ``end_lineno``."""
    start = min([knoten.lineno, *(d.lineno for d in knoten.decorator_list)])
    return "".join(inspect.getblock(zeilen[start - 1 :]))


def schritte(pfad: Path = PFAD) -> list[str]:
    """Die Schritte des Dirigenten, in der Reihenfolge ihres ersten Aufrufs."""
    tabelle, dirigent, _ = _lesen(pfad)
    return _schritte(tabelle, dirigent)


def basen(pfad: Path = PFAD) -> list[Path]:
    """Die Dateien der direkten Mixin-Basen von ``TradingLoopMixin``."""
    return _lesen(pfad)[2]


def quelle(pfad: Path = PFAD) -> str:
    """Ersatz für ``inspect.getsource(live_trading_loop)``: Dirigent, dann seine Schritte."""
    tabelle, dirigent, _ = _lesen(pfad)
    return _zeilen(tabelle[DIRIGENT][0], dirigent) + "".join(
        _zeilen(*tabelle[name]) for name in _schritte(tabelle, dirigent)
    )


def ast_quelle(pfad: Path = PFAD) -> tuple[ast.Module, str]:
    """Für Prüfer am AST (#3489): der geparste Text und der Text selbst."""
    text = quelle(pfad)
    return ast.parse(textwrap.dedent(text)), text


def texte_handelsschleife(pfad: Path = PFAD) -> dict[Path, str]:
    """Kern plus jedes vorhandene Modul aus ``ZIELMODULE``, je Datei — für ``ast``-Prüfer.

    Verbundene Modultexte sind kein gültiges Python (``from __future__`` steht nur am
    Dateianfang); wer parst, parst je Datei."""
    texte = {pfad: _parsen(pfad)[0]}
    for name in ZIELMODULE:
        datei = pfad.parent / name
        if datei.is_file():
            texte[datei] = datei.read_text(encoding="utf-8")
    return texte


def text_handelsschleife(pfad: Path = PFAD) -> str:
    """Die Texte aus ``texte_handelsschleife``, verbunden — nur für die Suche nach Teilstrings."""
    return "\n".join(texte_handelsschleife(pfad).values())
