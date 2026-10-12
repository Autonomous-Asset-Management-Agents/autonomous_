"""Sim-Day offline contract for the Stock Specialist (activation runbook, addition 2).

In a Sim-Day run (SIM_MODE) the specialist must NOT hit the network (offline/deterministic
contract) and must not read anachronistic live alt-data over a historical day. The 9 live-only
sources are skipped and honestly marked ``sources_unavailable`` (coverage card shows "—", never a
fabricated "checked, found nothing"); only ``ml_prediction`` (offline, as-of) runs.
"""

import asyncio

from core.stock_specialist import StockSpecialistAgent

_NETWORK_METHODS = [
    "_fetch_edgar_form4",
    "_fetch_edgar_8k",
    "_fetch_edgar_13d",
    "_fetch_congressional_trades",
    "_fetch_polygon_news",
    "_fetch_wiki_pageviews",
    "_fetch_reddit_mentions",
    "_fetch_finra_short_interest",
    "_fetch_google_trends",
]
_NETWORK_SOURCES = [
    "edgar_form4",
    "edgar_8k",
    "edgar_13d",
    "congressional_trades",
    "polygon_news",
    "wiki_pageviews",
    "reddit_mentions",
    "finra_short_interest",
    "google_trends",
]


def test_research_is_offline_and_honest_under_sim_mode(monkeypatch):
    import core.stock_specialist as ss

    agent = StockSpecialistAgent("AAPL", "dummy-key")

    # SIM_MODE on; every other flag falls to its getattr default (False/absent).
    monkeypatch.setattr(ss, "get_config", lambda: type("Cfg", (), {"SIM_MODE": True})())

    # No CIK refresh / no LLM synthesis in the test.
    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(ss, "maybe_refresh", _noop)
    monkeypatch.setattr(agent, "_gemini_synthesize", lambda *a, **k: _mk({}))
    monkeypatch.setattr(agent, "_fetch_ml_prediction", lambda *a, **k: _mk(None))

    # Spy on the network fetchers: they must NEVER be invoked under the sim offline contract.
    called = []

    def _spy_factory(name):
        return lambda *a, **k: _spy(called, name)

    for m in _NETWORK_METHODS:
        monkeypatch.setattr(agent, m, _spy_factory(m))

    report = asyncio.run(agent.research())

    assert called == [], f"network fetchers invoked under SIM_MODE: {called}"
    assert set(_NETWORK_SOURCES) <= set(
        report.sources_unavailable
    ), f"missing honest unavailable marks: {set(_NETWORK_SOURCES) - set(report.sources_unavailable)}"


async def _mk(value):
    return value


async def _spy(sink, name):
    sink.append(name)
    return None
