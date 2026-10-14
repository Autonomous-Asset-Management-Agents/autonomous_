"""#4085 (ARC-E6 G-6c) — Drawdown-Guard-, Regime-, VIX-Aware- und Momentum-Agent in je einer Datei.

Die vier Klassen liegen unter ``core/round_table/agenten/``; ``agents.py`` importiert sie
neuladetreu zurueck (``_frisch``, G-6b). Festgehalten sind die Fallen aus Plan §1:

1. Das Gewicht steht im Klassenrumpf. Tests laden ``agents`` neu; der Re-Export muss danach
   auf die *neu* gebaute Klasse mit dem neu gelesenen Gewicht zeigen.
2. ``_MOMENTUM_ABSTAIN_WARNED`` bleibt in ``agents``. Tests leeren
   ``agents._MOMENTUM_ABSTAIN_WARNED`` — der verschobene ``MomentumAgent`` muss genau diese
   Menge lesen, sonst wirkt das Leeren nicht.
3. Das Veto des Drawdown-Guards bleibt gleich.

Plan: ``docs/4085-*/implementation_plan.md``.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import inspect
import logging
from types import SimpleNamespace

import pytest

import config

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

VERSCHOBEN = {
    "DrawdownGuardAgent": "core.round_table.agenten.drawdown_guard",
    "MomentumAgent": "core.round_table.agenten.momentum",
    "RegimeDetectionAgent": "core.round_table.agenten.regime",
    "VIXAwareRiskAgent": "core.round_table.agenten.vix_aware",
}


@pytest.mark.parametrize("name,modulname", sorted(VERSCHOBEN.items()))
def test_die_klasse_liegt_im_agenten_modul_und_der_reexport_ist_identisch(
    name, modulname
):
    from core.round_table import agents

    modul = importlib.import_module(modulname)
    assert getattr(modul, name).__module__ == modulname
    assert getattr(agents, name) is getattr(modul, name)


def test_agents_py_definiert_keine_der_vier_klassen_mehr():
    from core.round_table import agents

    baum = ast.parse(inspect.getsource(agents))
    klassen = {k.name for k in baum.body if isinstance(k, ast.ClassDef)}
    assert klassen.isdisjoint(VERSCHOBEN)


def test_die_helfer_kommen_ueber_den_reexport_aus_den_agenten_modulen():
    from core.round_table import agents
    from core.round_table.agenten import drawdown_guard, momentum, regime, vix_aware

    assert agents.DRAWDOWN_WINDOW_TRADING_DAYS == 30
    assert agents._drawdown_conditioner is drawdown_guard._drawdown_conditioner
    assert agents._regime_conditioner_cfg is regime._regime_conditioner_cfg
    assert agents._momentum_score_smooth is momentum._momentum_score_smooth
    assert agents._iv_percentile is vix_aware._iv_percentile


def _momentum_ohne_daten(monkeypatch):
    import core.agent_registry as agent_registry
    import core.round_table.base_agent as base_agent

    monkeypatch.setattr(base_agent, "RedisClient", None)
    monkeypatch.setattr(agent_registry, "get_global_registry", lambda: None)
    monkeypatch.setattr(
        config,
        "get_config",
        lambda: SimpleNamespace(MOMENTUM_FALLBACK_ABSTAIN_ENABLED=True),
    )


def test_die_warn_drossel_wirkt_ueber_agents(monkeypatch, caplog):
    from core.round_table import agents

    _momentum_ohne_daten(monkeypatch)
    agents._MOMENTUM_ABSTAIN_WARNED.clear()

    def warnungen():
        caplog.clear()
        with caplog.at_level(logging.DEBUG, logger="core.round_table.agents"):
            asyncio.run(agents.MomentumAgent().vote({"symbol": "G6C", "ohlc": {}}))
        return [
            r
            for r in caplog.records
            if r.name == "core.round_table.agents"
            and r.levelno == logging.WARNING
            and "MomentumAgent" in r.getMessage()
        ]

    assert len(warnungen()) == 1
    assert "G6C" in agents._MOMENTUM_ABSTAIN_WARNED
    assert warnungen() == []  # gedrosselt
    agents._MOMENTUM_ABSTAIN_WARNED.clear()
    assert len(warnungen()) == 1  # Leeren ueber agents wirkt


def test_das_veto_bleibt_gleich(monkeypatch):
    """Ein-Bar-Pfad: (H-L)/H = 20 % > 0.05 → Veto, wie vor dem Umzug."""
    import core.agent_registry as agent_registry
    import core.round_table.base_agent as base_agent
    from core.round_table import agents

    monkeypatch.setattr(base_agent, "RedisClient", None)
    monkeypatch.setattr(agent_registry, "get_global_registry", lambda: None)
    ergebnis = asyncio.run(
        agents.DrawdownGuardAgent().vote(
            {"symbol": "G6C", "ohlc": {"high": 100.0, "low": 80.0, "close": 80.0}}
        )
    )
    assert ergebnis.vetoed is True
    assert ergebnis.score == pytest.approx(0.0)


def test_neuladen_liest_das_gewicht_neu(monkeypatch):
    from core.round_table import agents

    monkeypatch.setattr(
        config,
        "get_config",
        lambda: SimpleNamespace(DRAWDOWN_GUARD_WEIGHT=0.3, MOMENTUM_AGENT_WEIGHT=0.2),
    )
    importlib.reload(agents)
    try:
        from core.round_table.agenten import drawdown_guard, momentum

        assert agents.DrawdownGuardAgent.default_weight == 0.3
        assert agents.MomentumAgent.default_weight == 0.2
        assert agents.DrawdownGuardAgent is drawdown_guard.DrawdownGuardAgent
        assert agents.MomentumAgent is momentum.MomentumAgent
    finally:
        monkeypatch.undo()
        importlib.reload(agents)
