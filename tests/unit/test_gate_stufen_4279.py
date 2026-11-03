"""Die Gate-Stufen liegen in ``core/round_table/gate_stufen.py``.

#4279 (H-4f), Teil von ARC-E6 (#3738). Reiner Umzug des Phase-3-Blocks (GAP9-Drossel,
Strict-ML-Riegel, Strict-ML-Gate, Agent-Veto, Meta-Label-Gate, Gatekeeper-Auflösung) aus
``core/round_table/runner.py`` (Schnitt-Entscheidung #4186 §2, §3, §5 „H-4f“). Der Kern
importiert die vier Phase-3-Funktionen zurück (Weg (a)) und führt
``_warn_gatekeeper_missing_context`` wieder aus; Drossel und Riegel leben nur im Zielmodul
(Weg (c)). Plan: ``docs/4279-*/implementation_plan.md`` §5.
"""

from __future__ import annotations

import ast
import logging
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tests.unit import _round_table_quelle as rq

pytestmark = [pytest.mark.unit, pytest.mark.vc2]

PAKET = rq.PAKET
KERN = rq.KERN
ZIEL = PAKET / "core" / "round_table" / "gate_stufen.py"
ZIEL_MODUL = "core.round_table.gate_stufen"

RUECKIMPORT = (
    "_strict_ml_gate_blocks",
    "_apply_agent_veto",
    "_apply_meta_label",
    "_resolve_gatekeeper_decision",
    "_warn_gatekeeper_missing_context",
)
FUNKTIONEN = RUECKIMPORT + (
    "_warn_strict_ml_rl_not_required",
    "_warn_strict_ml_lstm_disabled",
)
VARIABLEN = (
    "_LAST_MISSING_CONTEXT_WARN_TS",
    "_MISSING_CONTEXT_WARN_INTERVAL_S",
    "_MISSING_CONTEXT_WARN_LOCK",
    "_STRICT_ML_RL_WAIVED_WARNED",
    "_STRICT_ML_RL_WAIVED_LOCK",
    "_STRICT_ML_LSTM_DISABLED_WARNED",
    "_STRICT_ML_LSTM_DISABLED_LOCK",
)
SYMBOLE = FUNKTIONEN + VARIABLEN


def _baum(datei: Path) -> ast.Module:
    return ast.parse(datei.read_text(encoding="utf-8"), filename=str(datei))


def _definiert(baum: ast.Module) -> set[str]:
    namen: set[str] = set()
    for knoten in ast.walk(baum):
        if isinstance(knoten, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            namen.add(knoten.name)
        elif isinstance(knoten, (ast.Assign, ast.AnnAssign)):
            ziele = (
                knoten.targets if isinstance(knoten, ast.Assign) else [knoten.target]
            )
            namen |= {
                n.id for z in ziele for n in ast.walk(z) if isinstance(n, ast.Name)
            }
    return namen


def test_ein_ort_nicht_zwei():
    from core.round_table import gate_stufen, runner

    for name in RUECKIMPORT:
        assert getattr(runner, name) is getattr(gate_stufen, name), name


def test_kern_definiert_die_gate_stufen_nicht():
    baum = _baum(KERN)
    assert not _definiert(baum) & set(SYMBOLE)
    assert set(SYMBOLE) <= _definiert(_baum(ZIEL))

    importiert = {
        a.name
        for knoten in baum.body
        if isinstance(knoten, ast.ImportFrom) and knoten.module == ZIEL_MODUL
        for a in knoten.names
    }
    assert set(RUECKIMPORT) <= importiert


def test_riegel_zuruecksetzen_wirkt_im_zielmodul(caplog):
    from core.round_table import gate_stufen, runner

    cfg = SimpleNamespace(GATEKEEPER_STRICT_ML_REQUIRES_RL=False)
    gate_stufen._STRICT_ML_RL_WAIVED_WARNED = False
    try:
        with caplog.at_level(logging.WARNING, logger="core.round_table.runner"), patch(
            "config.get_config", return_value=cfg
        ):
            assert runner._strict_ml_gate_blocks(True, False) is False
            assert runner._strict_ml_gate_blocks(True, False) is False
    finally:
        gate_stufen._STRICT_ML_RL_WAIVED_WARNED = False

    waiver = [
        r
        for r in caplog.records
        if r.name == "core.round_table.runner" and "RL not required" in r.message
    ]
    assert len(waiver) == 1


def test_drossel_lock_ist_der_des_zielmoduls():
    from core.round_table import gate_stufen, runner

    assert hasattr(gate_stufen._MISSING_CONTEXT_WARN_LOCK, "acquire")
    assert not hasattr(runner, "_MISSING_CONTEXT_WARN_LOCK")


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
