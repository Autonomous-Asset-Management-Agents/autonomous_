"""BDD-Schritte holen sich keine geteilte Event-Loop (CI-Befund vom 05.10.2026, #4178).

``asyncio.get_event_loop()`` wirft unter Python 3.12 (Projektstandard, CI), sobald ein
frueher gesammelter Test ``asyncio.run`` gerufen hat. Genau so
brach ``test_golden_path.py`` Story 05, nachdem die Verhaltensabnahme von ARC-E6 vor ihm
lief. Ein Schritt nimmt deshalb ``asyncio.run(...)`` oder eine eigene ``new_event_loop()``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.vc0

STEP_DEFS = Path(__file__).resolve().parents[1] / "features" / "step_defs"


def geteilte_loop_aufrufe(verzeichnis: Path) -> list[str]:
    """``datei:zeile`` jedes Aufrufs ``asyncio.get_event_loop()`` - per ``ast``, damit
    Kommentare und Docstrings nicht zaehlen."""
    treffer = []
    for pfad in sorted(verzeichnis.rglob("*.py")):
        for knoten in ast.walk(ast.parse(pfad.read_text(encoding="utf-8"))):
            if (
                isinstance(knoten, ast.Call)
                and isinstance(knoten.func, ast.Attribute)
                and knoten.func.attr == "get_event_loop"
                and isinstance(knoten.func.value, ast.Name)
                and knoten.func.value.id == "asyncio"
            ):
                treffer.append(f"{pfad.name}:{knoten.lineno}")
    return treffer


def test_kein_schritt_holt_sich_die_geteilte_loop():
    assert geteilte_loop_aufrufe(STEP_DEFS) == []


def test_gegenprobe_ein_aufruf_wird_gefunden(tmp_path):
    (tmp_path / "test_x.py").write_text(
        '"""asyncio.get_event_loop() im Docstring zaehlt nicht."""\n'
        "import asyncio\n"
        "# asyncio.get_event_loop() im Kommentar zaehlt nicht\n"
        "loop = asyncio.get_event_loop()\n",
        encoding="utf-8",
    )
    assert geteilte_loop_aufrufe(tmp_path) == ["test_x.py:4"]
