"""#4084 (ARC-E6 G-6b) — LSTM-, RL-Confidence- und Specialist-Alpha-Agent in je einer Datei.

Die drei Klassen liegen unter ``core/round_table/agenten/``; ``agents.py`` importiert sie
zurueck. Drei gemessene Fallen (Plan §1) sind hier festgehalten:

1. Das Gewicht steht im Klassenrumpf. Tests laden ``agents`` neu, um ein anderes Gewicht zu
   pruefen; der Re-Export muss danach auf die *neu* gebaute Klasse zeigen.
2. ``set_specialist_registry`` setzt den Zustand von ``agents`` — der verschobene Agent muss
   ihn sehen.
3. Die Warnungen kommen weiter vom Logger ``core.round_table.agents``.

Plan: ``docs/4084-*/implementation_plan.md``.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import inspect
import logging
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import config

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

VERSCHOBEN = {
    "LSTMSignalAgent": "core.round_table.agenten.lstm_signal",
    "RLConfidenceAgent": "core.round_table.agenten.rl_confidence",
    "SpecialistAlphaAgent": "core.round_table.agenten.specialist_alpha",
}


@pytest.mark.parametrize("name,modulname", sorted(VERSCHOBEN.items()))
def test_die_klasse_liegt_im_agenten_modul_und_der_reexport_ist_identisch(
    name, modulname
):
    from core.round_table import agents

    modul = importlib.import_module(modulname)
    assert getattr(modul, name).__module__ == modulname
    assert getattr(agents, name) is getattr(modul, name)


def test_ein_agenten_modul_zuerst_importiert_liefert_dieselbe_klasse():
    """Frischer Interpreter, das Agenten-Modul vor ``agents``: kein Doppelbau der Klasse."""
    probe = (
        "import core.round_table.agenten.specialist_alpha as sa\n"
        "from core.round_table import agents\n"
        "assert agents.SpecialistAlphaAgent is sa.SpecialistAlphaAgent\n"
    )
    ergebnis = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert ergebnis.returncode == 0, ergebnis.stderr[-2000:]


def test_agents_py_definiert_keine_der_drei_klassen_mehr():
    from core.round_table import agents

    baum = ast.parse(inspect.getsource(agents))
    klassen = {k.name for k in baum.body if isinstance(k, ast.ClassDef)}
    assert klassen.isdisjoint(VERSCHOBEN)


def test_das_sentinel_ist_ueberall_dasselbe_objekt():
    from core.round_table import agents
    from core.round_table.agenten import _basis, lstm_signal, rl_confidence

    assert (
        agents._SHARED_UNSET
        is _basis._SHARED_UNSET
        is lstm_signal._SHARED_UNSET
        is rl_confidence._SHARED_UNSET
    )


def test_neuladen_liest_die_gewichte_neu(monkeypatch):
    from core.round_table import agents

    monkeypatch.setattr(
        config,
        "get_config",
        lambda: SimpleNamespace(SPECIALIST_ALPHA_WEIGHT=0.55, RL_CONFIDENCE_WEIGHT=0.0),
    )
    importlib.reload(agents)
    try:
        from core.round_table.agenten import rl_confidence, specialist_alpha

        assert agents.SpecialistAlphaAgent.default_weight == 0.55
        assert agents.SpecialistAlphaAgent.max_weight == 2.0
        assert agents.RLConfidenceAgent.default_weight == 0.0
        assert agents.RLConfidenceAgent.max_weight == 0.0
        assert agents.SpecialistAlphaAgent is specialist_alpha.SpecialistAlphaAgent
        assert agents.RLConfidenceAgent is rl_confidence.RLConfidenceAgent
    finally:
        monkeypatch.undo()
        importlib.reload(agents)


def _registry_mit_report():
    report = SimpleNamespace(sentiment_score=80.0, recommendation="buy", escalate=False)
    return SimpleNamespace(get_report=lambda symbol: report)


def test_set_specialist_registry_erreicht_den_verschobenen_agenten(monkeypatch):
    import core.round_table.base_agent as base_agent
    from core.round_table import agents

    monkeypatch.setattr(base_agent, "RedisClient", None)
    monkeypatch.setattr(agents.SpecialistAlphaAgent, "default_weight", 0.55)
    monkeypatch.setattr(agents.SpecialistAlphaAgent, "max_weight", 2.0)
    agents.set_specialist_registry(_registry_mit_report())
    try:
        ergebnis = asyncio.run(agents.SpecialistAlphaAgent().vote({"symbol": "AAPL"}))
    finally:
        agents.set_specialist_registry(None)
    assert ergebnis.score == pytest.approx(0.85)
    assert "EXCLUDED" not in ergebnis.reasoning


def test_die_warnung_kommt_vom_bisherigen_logger(monkeypatch, caplog):
    import core.round_table.base_agent as base_agent
    from core.round_table import agents

    monkeypatch.setattr(base_agent, "RedisClient", None)
    monkeypatch.setattr(agents.SpecialistAlphaAgent, "default_weight", 0.55)
    monkeypatch.setattr(agents.SpecialistAlphaAgent, "max_weight", 2.0)
    monkeypatch.setattr(agents, "_warmup_warned_symbols", set())
    agents.set_specialist_registry(None)
    with caplog.at_level(logging.WARNING, logger="core.round_table.agents"):
        asyncio.run(agents.SpecialistAlphaAgent().vote({"symbol": "G6B"}))
    namen = {
        r.name
        for r in caplog.records
        if "SpecialistAlphaAgent" in r.getMessage() and r.levelno == logging.WARNING
    }
    assert namen == {"core.round_table.agents"}
    assert "G6B" in agents._warmup_warned_symbols
