"""#4086 (ARC-E6 G-6d) — News-, Fundamentals- und Valuation-Agent in je einer Datei.

Die drei Klassen liegen unter ``core/round_table/agenten/``; die Fundamentaldaten-Helfer, die
Fundamentals und Valuation teilen, in ``agenten/_fundamentaldaten.py``. ``agents.py`` importiert
alles neuladetreu zurueck (``_frisch``, G-6b). Festgehalten sind die Fallen aus Plan §1:

1. Ein Patch auf den Fundamentaldaten-Leser muss **beide** Agenten erreichen. Sie lesen ihn
   deshalb ueber das Modulobjekt ``_fd``, nie per ``from … import``.
2. Das News-Gewicht steht im Klassenrumpf; nach dem Neuladen von ``agents`` zeigt der
   Re-Export auf die neu gebaute Klasse mit dem neu gelesenen Gewicht.
3. ``_LOCAL_SENTIMENT_CACHE`` ist veraenderlicher Zustand: ``agents._LOCAL_SENTIMENT_CACHE``
   ist dasselbe Dict, das der Agent beschreibt.

Plan: ``docs/4086-*/implementation_plan.md``.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import inspect
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import config

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

VERSCHOBEN = {
    "FundamentalsAgent": "core.round_table.agenten.fundamentals",
    "NewsSentimentAgent": "core.round_table.agenten.news_sentiment",
    "ValuationAgent": "core.round_table.agenten.valuation",
}

HELFER = {
    "_debt_to_equity": "core.round_table.agenten._fundamentaldaten",
    "_news_sentiment_cfg": "core.round_table.agenten.news_sentiment",
    "_news_sentiment_model_dir": "core.round_table.agenten.news_sentiment",
    "_pct": "core.round_table.agenten._fundamentaldaten",
    "_read_pit_fundamentals": "core.round_table.agenten._fundamentaldaten",
    "_valuation_multimetric_enabled": "core.round_table.agenten.valuation",
}

_FUND = {
    "revenue_yoy": 0.655,
    "net_margin": 0.556,
    "eps_yoy": 0.60,
    "pe": 40.5,
    "total_liabilities": 31_000_000_000.0,
    "total_equity": 100_000_000_000.0,
}


@pytest.mark.parametrize("name,modulname", sorted(VERSCHOBEN.items()))
def test_die_klasse_liegt_im_agenten_modul_und_der_reexport_ist_identisch(
    name, modulname
):
    from core.round_table import agents

    modul = importlib.import_module(modulname)
    assert getattr(modul, name).__module__ == modulname
    assert getattr(agents, name) is getattr(modul, name)


@pytest.mark.parametrize("name,modulname", sorted(HELFER.items()))
def test_die_helfer_liegen_im_agenten_modul_und_der_reexport_ist_identisch(
    name, modulname
):
    from core.round_table import agents

    modul = importlib.import_module(modulname)
    assert getattr(modul, name).__module__ == modulname
    assert getattr(agents, name) is getattr(modul, name)


def test_agents_py_definiert_keine_der_drei_klassen_mehr():
    from core.round_table import agents

    baum = ast.parse(inspect.getsource(agents))
    klassen = {k.name for k in baum.body if isinstance(k, ast.ClassDef)}
    assert klassen.isdisjoint(VERSCHOBEN)


def test_ein_patch_auf_den_fundamentaldaten_leser_erreicht_beide_agenten():
    from core.round_table import agents

    aufrufe = []

    def _fake(symbol, close=None, as_of=None):
        aufrufe.append(symbol)
        return _FUND

    state = {"symbol": "G6D", "ohlc": {"close": 180.0}}
    with patch(
        "core.round_table.agenten._fundamentaldaten._read_pit_fundamentals", _fake
    ):
        fund = asyncio.run(agents.FundamentalsAgent().vote(state))
        val = asyncio.run(agents.ValuationAgent().vote(state))

    assert aufrufe == ["G6D", "G6D"]
    assert "revenue +65.5%" in fund.reasoning
    assert "PEG " in val.reasoning and "debt-to-equity 0.31" in val.reasoning


def test_der_sentiment_cache_ist_dasselbe_dict():
    from core.round_table import agents
    from core.round_table.agenten import news_sentiment

    assert agents._LOCAL_SENTIMENT_CACHE is news_sentiment._LOCAL_SENTIMENT_CACHE
    assert agents._LOCAL_SENTIMENT_CACHE_MAXSIZE == 512


def test_neuladen_liest_das_gewicht_neu(monkeypatch):
    from core.round_table import agents

    monkeypatch.setattr(
        config,
        "get_config",
        lambda: SimpleNamespace(
            NEWS_SENTIMENT_WEIGHT=0.4,
            FUNDAMENTALS_AGENT_WEIGHT=0.3,
            VALUATION_AGENT_WEIGHT=0.2,
        ),
    )
    importlib.reload(agents)
    try:
        from core.round_table.agenten import fundamentals, news_sentiment, valuation

        assert agents.NewsSentimentAgent.default_weight == 0.4
        assert agents.FundamentalsAgent.default_weight == 0.3
        assert agents.ValuationAgent.default_weight == 0.2
        assert agents.NewsSentimentAgent is news_sentiment.NewsSentimentAgent
        assert agents.FundamentalsAgent is fundamentals.FundamentalsAgent
        assert agents.ValuationAgent is valuation.ValuationAgent
    finally:
        monkeypatch.undo()
        importlib.reload(agents)
