"""#4110 (ARC-E6 G-6e) — Upside-Skew-, Quality-, Trend- und Volume-Agent in je einer Datei.

Die vier Klassen liegen samt Enable-Gate und Gewicht unter ``core/round_table/agenten/``;
``agents.py`` importiert alles neuladetreu zurueck (``_frisch``, G-6b). Festgehalten ist:

1. Klasse, Gate und Gewicht liegen im Agenten-Modul, der Re-Export ist dasselbe Objekt.
2. ``agents.py`` definiert keine der vier Klassen mehr.
3. Ein Patch auf das Gate im neuen Modul wirkt — der Agent schlaegt es dort nach.
4. Nach dem Neuladen von ``agents`` zeigt der Re-Export auf die neu gebaute Klasse mit dem
   neu gelesenen Gewicht.

Plan: ``docs/4110-*/implementation_plan.md``.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import inspect
from types import SimpleNamespace

import pytest

import config

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

VERSCHOBEN = {
    "QualityAgent": "core.round_table.agenten.quality",
    "TrendAgent": "core.round_table.agenten.trend",
    "UpsideSkewAgent": "core.round_table.agenten.upside_skew",
    "VolumeConfirmationAgent": "core.round_table.agenten.volume_confirmation",
}

GATES = {
    "_quality_agent_enabled": "core.round_table.agenten.quality",
    "_trend_agent_enabled": "core.round_table.agenten.trend",
    "_upside_skew_enabled": "core.round_table.agenten.upside_skew",
    "_volume_confirm_agent_enabled": "core.round_table.agenten.volume_confirmation",
}

WERTE = {
    "_QUALITY_AGENT_WEIGHT": "core.round_table.agenten.quality",
    "_TREND_AGENT_WEIGHT": "core.round_table.agenten.trend",
    "_UPSIDE_SKEW_WEIGHT": "core.round_table.agenten.upside_skew",
    "_VOLUME_CONFIRM_AGENT_WEIGHT": "core.round_table.agenten.volume_confirmation",
    "_VOLUME_RATIO_SCALE": "core.round_table.agenten.volume_confirmation",
}

_STATES = {
    "QualityAgent": {
        "symbol": "G6E",
        "quality_score": 0.8,
        "quality_reference": [0.1, 0.5, 0.8],
    },
    "TrendAgent": {"symbol": "G6E", "features": {"trend_baz": 0.4}},
    "UpsideSkewAgent": {
        "symbol": "G6E",
        "risk_reversal": 0.05,
        "risk_reversal_reference": [-0.03, 0.0, 0.05],
    },
    "VolumeConfirmationAgent": {"symbol": "G6E", "features": {"vol_ratio_5_20": 1.5}},
}


@pytest.mark.parametrize("name,modulname", sorted(VERSCHOBEN.items()))
def test_die_klasse_liegt_im_agenten_modul_und_der_reexport_ist_identisch(
    name, modulname
):
    from core.round_table import agents

    modul = importlib.import_module(modulname)
    assert getattr(modul, name).__module__ == modulname
    assert getattr(agents, name) is getattr(modul, name)


@pytest.mark.parametrize("name,modulname", sorted(GATES.items()))
def test_das_gate_liegt_im_agenten_modul_und_der_reexport_ist_identisch(
    name, modulname
):
    from core.round_table import agents

    modul = importlib.import_module(modulname)
    assert getattr(modul, name).__module__ == modulname
    assert getattr(agents, name) is getattr(modul, name)


@pytest.mark.parametrize("name,modulname", sorted(WERTE.items()))
def test_die_modulwerte_liegen_im_agenten_modul(name, modulname):
    from core.round_table import agents

    modul = importlib.import_module(modulname)
    baum = ast.parse(inspect.getsource(modul))
    definiert = {
        z.id
        for k in baum.body
        if isinstance(k, ast.Assign)
        for z in k.targets
        if isinstance(z, ast.Name)
    }
    assert name in definiert
    assert getattr(agents, name) == getattr(modul, name)


def test_agents_py_definiert_keine_der_vier_klassen_mehr():
    from core.round_table import agents

    baum = ast.parse(inspect.getsource(agents))
    klassen = {k.name for k in baum.body if isinstance(k, ast.ClassDef)}
    funktionen = {k.name for k in baum.body if isinstance(k, ast.FunctionDef)}
    assert klassen.isdisjoint(VERSCHOBEN)
    assert funktionen.isdisjoint(GATES)


@pytest.mark.parametrize("name", sorted(VERSCHOBEN))
def test_ein_gate_patch_im_neuen_modul_wirkt(name, monkeypatch):
    from core.round_table import agents

    modul = importlib.import_module(VERSCHOBEN[name])
    gate = next(g for g, m in GATES.items() if m == VERSCHOBEN[name])
    agent = getattr(agents, name)()
    state = _STATES[name]

    monkeypatch.setattr(modul, gate, lambda: False)
    aus = asyncio.run(agent.vote(state))
    assert aus.weight == 0.0 and aus.score is None
    assert "dark (flag off)" in aus.reasoning

    monkeypatch.setattr(modul, gate, lambda: True)
    an = asyncio.run(agent.vote(state))
    assert an.score is not None
    assert "dark (flag off)" not in an.reasoning


def test_neuladen_liest_das_gewicht_neu(monkeypatch):
    from core.round_table import agents

    monkeypatch.setattr(
        config,
        "get_config",
        lambda: SimpleNamespace(
            UPSIDE_SKEW_WEIGHT=0.4,
            QUALITY_AGENT_WEIGHT=0.35,
            TREND_AGENT_WEIGHT=0.3,
            VOLUME_CONFIRM_AGENT_WEIGHT=0.2,
        ),
    )
    importlib.reload(agents)
    try:
        from core.round_table.agenten import (
            quality,
            trend,
            upside_skew,
            volume_confirmation,
        )

        assert agents.UpsideSkewAgent.default_weight == 0.4
        assert agents.QualityAgent.default_weight == 0.35
        assert agents.TrendAgent.default_weight == 0.3
        assert agents.VolumeConfirmationAgent.default_weight == 0.2
        assert agents.UpsideSkewAgent is upside_skew.UpsideSkewAgent
        assert agents.QualityAgent is quality.QualityAgent
        assert agents.TrendAgent is trend.TrendAgent
        assert (
            agents.VolumeConfirmationAgent
            is volume_confirmation.VolumeConfirmationAgent
        )
    finally:
        monkeypatch.undo()
        importlib.reload(agents)
