"""#4236 (H-1g) — der Abgang der Absendung je Mandant wohnt in ``absendung_abgang.py``.

Plan: ``docs/4236-*/implementation_plan.md`` §4/§5. Entscheidung:
``docs/3738-arc-e6-gestalt/H1_SCHNITT_order_executor.md``, Abschnitt H-1g.

Sechs Schritte von ``OrderExecutorMixin`` ziehen wörtlich nach ``AbsendungAbgangMixin``
um. ``BotEngine`` erhält sie über ``AusfuehrungMixin``. Disjunktheit der Methodennamen
prüft ``test_h1d_mandanten_zugang.py`` zentral; hier steht, dass ``BotEngine`` jeden der
sechs auf genau die umgezogene Methode auflöst (der Kern steht in der MRO vorn), und dass
der Schatten-Span weiter am Tracer des Kerns hängt.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from alpaca.trading.enums import OrderSide

from tests.architecture import regeln

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
MODUL = PAKET / "core" / "engine" / "absendung_abgang.py"
KERN = PAKET / "core" / "engine" / "order_executor.py"
SECHS = (
    "_schritt_mandant_compliance",
    "_schritt_mandant_auftrag",
    "_schritt_mandant_schatten",
    "_schritt_mandant_senden",
    "_schritt_mandant_fuellung",
    "_schritt_mandant_storno",
)


def test_sechs_schritte_wohnen_im_abgang():
    from core.engine.absendung_abgang import AbsendungAbgangMixin
    from core.engine.order_executor import OrderExecutorMixin

    for name in SECHS:
        assert name in AbsendungAbgangMixin.__dict__, name
        assert name not in OrderExecutorMixin.__dict__, name


def test_botengine_loest_den_abgang_auf():
    from core.engine.absendung_abgang import AbsendungAbgangMixin
    from core.engine.base import BotEngine

    for name in SECHS:
        assert (
            inspect.getattr_static(BotEngine, name) is vars(AbsendungAbgangMixin)[name]
        ), name


def test_abgang_bleibt_unter_der_dateischwelle():
    schwelle = regeln.lade_vertrag()["groessen"]["datei_schwelle"]
    zeilen = len(MODUL.read_text(encoding="utf-8").splitlines())
    assert zeilen <= schwelle


def test_kern_importiert_abgang_nicht():
    from core.engine.absendung_abgang import (  # noqa: F401 — rot ohne Modul
        AbsendungAbgangMixin,
    )

    baum = ast.parse(KERN.read_text(encoding="utf-8"))
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.ImportFrom):
            quellen = [knoten.module or ""] + [a.name for a in knoten.names]
        elif isinstance(knoten, ast.Import):
            quellen = [a.name for a in knoten.names]
        else:
            continue
        for quelle in quellen:
            assert not quelle.endswith("absendung_abgang"), quelle


def test_schatten_span_liest_den_tracer_am_kern():
    """Der Span ``broker.submit_order.live`` öffnet über den am Kern gepatchten Tracer."""
    import core.engine.order_executor as oe
    from core.engine.absendung_abgang import AbsendungAbgangMixin
    from core.engine.base import BotEngine

    ctx = SimpleNamespace(
        decision_id="dec-4236",
        triggered_by_stop=False,
        stop_type="",
        alpaca_order_id="",
        action_executed=False,
        is_simulation=False,
    )
    st = oe._Absendung(
        user_id="u-4236",
        action="BUY",
        symbol="AAPL",
        context=ctx,
        size=3.0,
        req=MagicMock(symbol="AAPL", qty=3.0),
        side_enum=OrderSide.BUY,
        pm=MagicMock(),
        deferred_close_symbol=None,
    )
    engine = BotEngine.__new__(BotEngine)
    engine.compliance_guardian = None
    tor = AsyncMock(return_value=MagicMock(id="shadow_4236"))
    with (
        patch("core.engine.order_executor.tracer") as tracer,
        patch.object(oe.OrderExecutorMixin, "_sende_durchs_tor", tor),
    ):
        asyncio.run(AbsendungAbgangMixin._schritt_mandant_schatten(engine, st))

    tracer.start_as_current_span.assert_called_once_with("broker.submit_order.live")
    assert isinstance(tor.await_args.kwargs["client"], oe.DryRunOrderProxy)
    assert (ctx.alpaca_order_id, ctx.is_simulation) == ("shadow_4236", True)
