"""#4088 (ARC-E6 G-7b) — der Dirigent ``_run_for_symbol_impl`` und seine Schritte.

Reihenfolge, Sentinel gegen ``None``, ein fruehes Ergebnis stoppt den Dirigenten, alle
Schritte sind Koroutinen.

Plan: ``docs/4088-g7b-rl-execution-schritte/implementation_plan.md``.
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc1]

SCHRITTE = (
    "_schritt_lage",
    "_schritt_ausstieg_halten",
    "_schritt_halten_abschliessen",
    "_schritt_kauf_freigabe",
    "_schritt_verkaufszaehler",
    "_schritt_risikofilter",
    "_schritt_tausch",
    "_schritt_groesse",
    "_schritt_anti_churn",
    "_schritt_kauf_absenden",
    "_schritt_protokoll",
)
ZEIT = datetime(2026, 10, 4, 15, 0, tzinfo=timezone.utc)


def _agent():
    from core.strategies.rl_execution import RLExecutionMixin

    return RLExecutionMixin()


def _aufzeichnen(agent, ergebnisse: dict):
    """Ersetzt jeden Schritt; gibt ``_WEITER`` zurueck, ausser ``ergebnisse`` nennt ihn."""
    from core.strategies.rl_entscheidung import _WEITER

    gerufen = []

    def _schritt(name):
        def _lauf(z, **_kw):
            gerufen.append((name, z))
            return ergebnisse.get(name, _WEITER)

        return AsyncMock(side_effect=_lauf)

    for name in SCHRITTE:
        setattr(agent, name, _schritt(name))
    return gerufen


def _lauf(agent):
    return asyncio.run(
        agent._run_for_symbol_impl("GATE", {"close": 10.0}, {"vix": 20}, ZEIT)
    )


def test_alle_schritte_sind_koroutinen_der_mixin_klasse():
    from core.strategies.rl_entscheidung import RLEntscheidungsSchritte
    from core.strategies.rl_execution import RLExecutionMixin

    assert issubclass(RLExecutionMixin, RLEntscheidungsSchritte)
    for name in SCHRITTE:
        assert inspect.iscoroutinefunction(getattr(RLEntscheidungsSchritte, name)), name


def test_der_dirigent_ruft_die_schritte_in_plan_reihenfolge_mit_einem_zustand():
    from core.strategies.rl_entscheidung import SymbolEntscheidung

    agent = _agent()
    trace = {"signal": "HOLD"}
    gerufen = _aufzeichnen(agent, {"_schritt_protokoll": trace})

    assert _lauf(agent) is trace
    assert [n for n, _ in gerufen] == list(SCHRITTE)
    (z,) = {id(z): z for _, z in gerufen}.values()
    assert isinstance(z, SymbolEntscheidung)
    assert (z.symbol, z.ohlc_data, z.market_data, z.current_time) == (
        "GATE",
        {"close": 10.0},
        {"vix": 20},
        ZEIT,
    )


def test_ein_fruehes_ergebnis_stoppt_den_dirigenten():
    agent = _agent()
    trace = {"signal": "HOLD"}
    gerufen = _aufzeichnen(agent, {"_schritt_risikofilter": trace})

    assert _lauf(agent) is trace
    assert [n for n, _ in gerufen] == list(
        SCHRITTE[: SCHRITTE.index("_schritt_risikofilter") + 1]
    )


def test_none_ist_ein_ergebnis_und_kein_weiter():
    agent = _agent()
    gerufen = _aufzeichnen(agent, {"_schritt_tausch": None})

    assert _lauf(agent) is None
    assert [n for n, _ in gerufen][-1] == "_schritt_tausch"


def test_fehlende_features_geben_none_und_kein_weiterer_schritt_laeuft():
    """Gherkin „None bleibt ein Ergebnis": echter erster Schritt, alle weiteren verboten."""
    agent = _agent()
    for name in SCHRITTE[1:]:
        setattr(agent, name, AsyncMock())
    agent._gather_market_inputs = AsyncMock(
        return_value={"state": None, "features": None, "pred": 0.0}
    )
    agent._generate_thought = MagicMock()

    assert _lauf(agent) is None
    for name in SCHRITTE[1:]:
        getattr(agent, name).assert_not_awaited()
    agent._generate_thought.assert_called_once_with("GATE", 0, None, 0.0, {"vix": 20})


def test_der_dirigent_ist_kurz():
    from core.strategies.rl_execution import RLExecutionMixin

    zeilen, _ = inspect.getsourcelines(RLExecutionMixin._run_for_symbol_impl)
    assert len(zeilen) <= 40
