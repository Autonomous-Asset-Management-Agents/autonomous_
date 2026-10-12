"""Die Entscheidungs-Zähler liegen in ``core/round_table/entscheidungs_zaehler.py``.

#4276 (H-4c), Teil von ARC-E6 (#3738). Reiner Umzug des ADR-OBS-01-Blocks aus
``core/round_table/runner.py`` (Schnitt-Entscheidung #4186 §2, §5 „H-4c“). Der Kern führt
die vier Funktionen per Rückimport wieder aus (Weg (a), §3), damit ``routes/diagnose.py``
und die Patches auf ``runner._bump_run`` / ``runner._bump_agent_failure`` weiter tragen.
Plan: ``docs/4276-*/implementation_plan.md`` §5.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tests.unit import _round_table_quelle as rq

pytestmark = [pytest.mark.unit, pytest.mark.vc2]

PAKET = rq.PAKET
KERN = rq.KERN
ZIEL = PAKET / "core" / "round_table" / "entscheidungs_zaehler.py"
ZIEL_MODUL = "core.round_table.entscheidungs_zaehler"

FUNKTIONEN = (
    "_bump_run",
    "_bump_agent_failure",
    "get_decision_counters",
    "reset_decision_counters",
)
SYMBOLE = FUNKTIONEN + (
    "_DECISION_COUNTERS",
    "_AGENT_VOTE_FAILURES",
    "_MAX_AGENT_FAILURE_KEYS",
)


def _baum(datei: Path) -> ast.Module:
    return ast.parse(datei.read_text(encoding="utf-8"), filename=str(datei))


def test_ein_zustand_nicht_zwei():
    from core.round_table import entscheidungs_zaehler as ez
    from core.round_table import runner

    for name in FUNKTIONEN:
        assert getattr(runner, name) is getattr(ez, name), name

    runner.reset_decision_counters()
    try:
        ez._bump_run()
        assert runner.get_decision_counters()["round_tables_run"] == 1
    finally:
        runner.reset_decision_counters()


def test_kern_definiert_die_zaehler_nicht():
    baum = _baum(KERN)
    definiert: set[str] = set()
    for knoten in ast.walk(baum):
        if isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            definiert.add(knoten.name)
        elif isinstance(knoten, (ast.Assign, ast.AnnAssign)):
            ziele = (
                knoten.targets if isinstance(knoten, ast.Assign) else [knoten.target]
            )
            definiert |= {
                n.id for z in ziele for n in ast.walk(z) if isinstance(n, ast.Name)
            }
    assert not definiert & set(SYMBOLE)

    importiert = {
        a.name
        for knoten in baum.body
        if isinstance(knoten, ast.ImportFrom) and knoten.module == ZIEL_MODUL
        for a in knoten.names
    }
    assert set(FUNKTIONEN) <= importiert


def test_zielmodul_ist_rueckimport_frei():
    """``core/round_table/__init__.py`` importiert selbst den Kern. Damit nur das Zielmodul
    gemessen wird, steht im frischen Prozess ein leeres Paket an seiner Stelle."""
    code = textwrap.dedent(
        f"""
        import sys
        import types
        paket = types.ModuleType("core.round_table")
        paket.__path__ = [{str(ZIEL.parent)!r}]
        sys.modules["core.round_table"] = paket
        import {ZIEL_MODUL}
        assert "core.round_table.runner" not in sys.modules, "Rückimport auf den Kern"
        """
    )
    lauf = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PAKET,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert lauf.returncode == 0, lauf.stderr


def test_zielmodul_bindet_den_logger_des_kerns():
    treffer = [
        knoten
        for knoten in _baum(ZIEL).body
        if isinstance(knoten, ast.Assign)
        and [getattr(z, "id", None) for z in knoten.targets] == ["logger"]
    ]
    assert len(treffer) == 1
    assert (
        ast.unparse(treffer[0].value) == "logging.getLogger('core.round_table.runner')"
    )


def test_diagnose_liest_den_zustand_des_dirigenten():
    from core.engine.routes import diagnose
    from core.round_table import entscheidungs_zaehler as ez
    from core.round_table import runner

    runner.reset_decision_counters()
    try:
        ez._bump_agent_failure("XAgent")
        assert diagnose._collect_decision()["agent_vote_failures"] == {"XAgent": 1}
    finally:
        runner.reset_decision_counters()
