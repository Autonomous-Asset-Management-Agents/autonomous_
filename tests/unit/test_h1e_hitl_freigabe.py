"""#4234 (H-1e) — HITL-Freigabe und Markt-Tor wohnen im komponierten ``hitl_freigabe.py``.

Plan: ``docs/4234-*/implementation_plan.md`` §4/§5. Entscheidung:
``docs/3738-arc-e6-gestalt/H1_SCHNITT_order_executor.md``, Abschnitt H-1e.

Drei Methoden von ``OrderExecutorMixin`` ziehen nach ``HitlFreigabeMixin`` um; der
Materialitaets-Riegel aus ``execute_approved_order`` wird der Schritt
``_freigabe_unter_materialitaet`` (Entscheidung §2, „Funktionen ueber 150“). Disjunktheit
der Methodennamen und die Zirkelregel prueft ``test_h1d_mandanten_zugang.py`` zentral.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

MODUL = Path(__file__).resolve().parents[2] / "core" / "engine" / "hitl_freigabe.py"
VIER = (
    "execute_approved_order",
    "_market_closed_blocks_order",
    "_hitl_holds_order",
    "_freigabe_unter_materialitaet",
)


def test_drei_methoden_und_schritt_wohnen_in_hitl_freigabe():
    from core.engine.hitl_freigabe import HitlFreigabeMixin
    from core.engine.order_executor import OrderExecutorMixin

    for name in VIER:
        assert name in HitlFreigabeMixin.__dict__, name
        assert name not in OrderExecutorMixin.__dict__, name


def test_ausfuehrung_traegt_hitl_freigabe():
    from core.engine.ausfuehrung import AusfuehrungMixin
    from core.engine.base import BotEngine
    from core.engine.hitl_freigabe import HitlFreigabeMixin

    assert AusfuehrungMixin.__bases__[3] is HitlFreigabeMixin
    assert HitlFreigabeMixin in BotEngine.__mro__
    assert HitlFreigabeMixin not in BotEngine.__bases__


def test_freigabe_unter_150():
    from core.engine.hitl_freigabe import (  # noqa: F401 — rot ohne Modul
        HitlFreigabeMixin,
    )

    baum = ast.parse(MODUL.read_text(encoding="utf-8"))
    laengen = {
        k.name: k.end_lineno - k.lineno + 1
        for k in ast.walk(baum)
        if isinstance(k, ast.AsyncFunctionDef | ast.FunctionDef)
    }
    for name in ("execute_approved_order", "_freigabe_unter_materialitaet"):
        assert laengen[name] <= 150, (name, laengen[name])
