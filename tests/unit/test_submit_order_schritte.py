"""#4089 (ARC-E6 G-7c) — der Dirigent ``_submit_order_safe`` und seine Schritte.

Reihenfolge, Sentinel, ein fruehes ``False`` stoppt den Dirigenten, eine Ausnahme endet wie
bisher in ``False`` mit demselben Protokoll, alle Schritte sind Koroutinen.

Plan: ``docs/4089-g7c-submit-order-schritte/implementation_plan.md``.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc1]

SCHRITTE = (
    "_schritt_client",
    "_schritt_compliance",
    "_schritt_markt_geschlossen",
    "_schritt_dedup",
    "_schritt_kaufkraft_pdt",
    "_schritt_menge",
    "_schritt_absenden",
    "_schritt_nachbuchen",
)


def _strategie():
    from core.strategies.base import BaseStrategy

    class Konkret(BaseStrategy):
        async def run_for_symbol(self, *args, **kwargs):
            pass

        async def evaluate_for_symbol(self, *args, **kwargs):
            return {}

    s = Konkret(
        client=MagicMock(),
        symbols=["GATE"],
        running_event=None,
        total_capital=10_000.0,
        risk_manager=MagicMock(),
        data_provider=MagicMock(),
        thought_callback=MagicMock(),
    )
    return s


def _aufzeichnen(strategie, ergebnisse: dict):
    """Ersetzt jeden Schritt; gibt ``_WEITER`` zurueck, ausser ``ergebnisse`` nennt ihn."""
    from core.strategies.base import _WEITER

    gerufen = []

    def _schritt(name):
        def _lauf(z, **_kw):
            gerufen.append((name, z))
            ergebnis = ergebnisse.get(name, _WEITER)
            if isinstance(ergebnis, BaseException):
                raise ergebnis
            return ergebnis

        return AsyncMock(side_effect=_lauf)

    for name in SCHRITTE:
        setattr(strategie, name, _schritt(name))
    return gerufen


def _lauf(strategie):
    return asyncio.run(
        strategie._submit_order_safe(
            "GATE",
            3.0,
            "sell",
            expected_cost=30.0,
            current_price=10.0,
            held_qty=5.0,
            is_protective_exit=True,
        )
    )


def test_alle_schritte_sind_koroutinen_der_basisklasse():
    from core.strategies.base import BaseStrategy

    for name in SCHRITTE:
        assert inspect.iscoroutinefunction(getattr(BaseStrategy, name)), name
    # Die Fabrik ist synchron: Ihr Ergebnis laeuft ohne Argumente im Executor.
    assert not inspect.iscoroutinefunction(BaseStrategy._einreicher)


def test_der_dirigent_ruft_die_schritte_in_plan_reihenfolge_mit_einem_zustand():
    from core.strategies.base import OrderEinreichung

    s = _strategie()
    gerufen = _aufzeichnen(s, {"_schritt_nachbuchen": True})

    assert _lauf(s) is True
    assert [n for n, _ in gerufen] == list(SCHRITTE)
    (z,) = {id(z): z for _, z in gerufen}.values()
    assert isinstance(z, OrderEinreichung)
    assert (
        z.symbol,
        z.qty,
        z.side,
        z.expected_cost,
        z.current_price,
        z.held_qty,
        z.is_protective_exit,
    ) == ("GATE", 3.0, "sell", 30.0, 10.0, 5.0, True)
    assert (
        z.is_simulation,
        z.compliance_order,
        z.time_in_force,
        z.use_fractional,
        z.order_qty,
    ) == (False, None, "day", True, 0.0)


@pytest.mark.parametrize("name", SCHRITTE[:-1])
def test_ein_fruehes_false_stoppt_den_dirigenten(name):
    s = _strategie()
    gerufen = _aufzeichnen(s, {name: False})

    assert _lauf(s) is False
    assert [n for n, _ in gerufen] == list(SCHRITTE[: SCHRITTE.index(name) + 1])


def test_eine_ausnahme_in_einem_schritt_endet_wie_bisher(caplog):
    """Gherkin „Ein Fehler endet wie bisher": ``False``, dieselbe Meldung, derselbe Log."""
    s = _strategie()
    gerufen = _aufzeichnen(s, {"_schritt_dedup": RuntimeError("boom")})

    with caplog.at_level(logging.ERROR):
        assert _lauf(s) is False
    assert [n for n, _ in gerufen][-1] == "_schritt_dedup"
    s.thought_callback.assert_called_once_with("[GATE] ❌ Order failed: boom")
    assert "Order submission failed for GATE: boom" in caplog.text


def test_eine_pdt_ausnahme_meldet_den_gtc_hinweis():
    s = _strategie()
    _aufzeichnen(
        s, {"_schritt_absenden": RuntimeError("Insufficient day trading buying power")}
    )

    assert _lauf(s) is False
    s.thought_callback.assert_called_once_with(
        "[GATE] ❌ Order failed (PDT): Insufficient day trading buying power. "
        "Using GTC next cycle."
    )


def test_der_dirigent_ist_kurz():
    from core.strategies.base import BaseStrategy

    zeilen, _ = inspect.getsourcelines(BaseStrategy._submit_order_safe)
    assert len(zeilen) <= 70  # davon 25 Docstring und Signatur
