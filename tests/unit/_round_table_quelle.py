"""#4275 (H-4b) — Quelltext des Round Table: Kern plus Zielmodule, ohne Import gelesen.

Plan: ``docs/4275-*/implementation_plan.md`` §2.1. Entscheidung:
``docs/3738-arc-e6-gestalt/H4_SCHNITT_round_table_runner.md`` §2 und §3.

Die Umzüge H-4c bis H-4g ziehen Themen aus ``core/round_table/runner.py`` in vier Module
flach daneben. Wer den Text von ``runner.py`` als Datei liest, sieht den umgezogenen Text
danach nicht mehr — ein positiver Leser wird rot, ein negativer bleibt grün und prüft nichts.
Dieser Helfer liefert deshalb den **Kern plus jedes vorhandene Zielmodul**:
``text_round_table`` verbunden (nur für Teilstrings), ``texte_round_table`` je Datei (für
``ast``; verbundene Modultexte mit ``from __future__`` sind kein gültiges Python).

Dazu die Erhebung der Patch-Ziele auf ``core.round_table.runner`` (vormals
``test_h4_schnitt_round_table_runner.py::_patch_namen``, wortgleich), damit die Messung der
Entscheidung und der Patch-Ziel-Wächter dieselbe Erhebung benutzen.

Fehlt der Kern, **erhebt** der Helfer ``LookupError`` — leerer Text machte jeden Leser
stillschweigend wahr. Er importiert kein ``core``: ein Import von ``runner`` lädt ``config``
und die Agenten.

Helfermodul (wie ``_schleifen_quelle.py``) — pytest sammelt es nicht. Eigentests:
``tests/unit/test_round_table_quelle.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
TESTS = PAKET / "tests"
KERN = PAKET / "core/round_table/runner.py"
#: Die Zielmodule aus der Schnitt-Entscheidung #4186 §2, ohne den Kern, flach in
#: ``core/round_table/`` — in der Reihenfolge, in der ``texte_round_table`` sie anhängt.
ZIELMODULE = (
    "entscheidungs_zaehler.py",
    "signal_bau.py",
    "gate_stufen.py",
    "aufzeichnung.py",
)
#: Dateien, deren synthetische Beispiel-Quellen keine Patches sind.
BEISPIELE = (
    Path(__file__).resolve().parent / "test_round_table_quelle.py",
    Path(__file__).resolve().parent / "test_round_table_patch_ziele.py",
)

# Patch-Ziele am Modulobjekt: als Pfad-String, über einen Modul-Alias (patch.object/setattr)
# oder als direkte Zuweisung ``alias.NAME = …`` (so setzen Tests die Drosseln zurück).
_PATCH_PFAD = re.compile(r"core[.]round_table[.]runner[.]([A-Za-z_]\w*)")
_ALIAS = re.compile(
    r"^\s*(?:import\s+core[.]round_table[.]runner\s+as\s+(\w+)"
    r"|from\s+core[.]round_table\s+import\s+runner\b(?:\s+as\s+(\w+))?)",
    re.M,
)


def _kern(paket: Path) -> Path:
    return paket / "core" / "round_table" / "runner.py"


def texte_round_table(paket: Path = PAKET) -> dict[Path, str]:
    """Kern plus jedes vorhandene Modul aus ``ZIELMODULE``, je Datei — für ``ast``-Prüfer."""
    kern = _kern(paket)
    if not kern.is_file():
        raise LookupError(
            f"{kern} fehlt — ohne Kern würde jeder Leser stillschweigend wahr"
        )
    texte = {kern: kern.read_text(encoding="utf-8")}
    for name in ZIELMODULE:
        datei = kern.parent / name
        if datei.is_file():
            texte[datei] = datei.read_text(encoding="utf-8")
    return texte


def text_round_table(paket: Path = PAKET) -> str:
    """Die Texte aus ``texte_round_table``, verbunden — nur für die Suche nach Teilstrings."""
    return "\n".join(texte_round_table(paket).values())


def gepatchte_namen(
    tests: Path = TESTS, ausser: tuple[Path, ...] = BEISPIELE
) -> dict[str, set[str]]:
    """Name → Testdateien, die ihn über das Modulobjekt ``core.round_table.runner`` patchen
    oder zuweisen."""
    ausgenommen = {p.resolve() for p in ausser}
    namen: dict[str, set[str]] = {}
    for datei in tests.rglob("*.py"):
        if datei.resolve() in ausgenommen:
            continue
        text = datei.read_text(encoding="utf-8", errors="replace")
        treffer = set(_PATCH_PFAD.findall(text))
        for alias in {a or b or "runner" for a, b in _ALIAS.findall(text)}:
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
