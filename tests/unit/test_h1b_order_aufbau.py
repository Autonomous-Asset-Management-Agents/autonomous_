"""#4231 (H-1b) — Order-Aufbau und Ausstiegsart wohnen in ``order_aufbau.py``.

Plan: ``docs/4231-*/implementation_plan.md`` §4/§5. Entscheidung:
``docs/3738-arc-e6-gestalt/H1_SCHNITT_order_executor.md``, Abschnitt H-1b.

Acht freie Funktionen ziehen wörtlich aus ``order_executor.py`` um. Der Kern re-exportiert
sie, damit jeder Leser und jeder Patch auf ``order_executor.<name>`` dasselbe Objekt trifft
(Entscheidung §3, Weg b). Gelesen wird per ``ast`` ohne Import (Muster
``test_h1_schnitt_order_executor.py``); die Identität prüft ein echter Import.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

ENGINE = Path(__file__).resolve().parents[2] / "core" / "engine"
ZIEL = ENGINE / "order_aufbau.py"
KERN = ENGINE / "order_executor.py"
NAMEN = (
    "build_broker_order_request",
    "exit_failsafe_remaining_qty",
    "_zahl",
    "baue_trade_record",
    "erfasse_order_endzustand",
    "halt_exempt_protective_exit",
    "classify_exit_kind",
    "_dust_floor_skips_exit",
)
UEBERGABE_NAMEN = (
    "_dust_floor_skips_exit",
    "classify_exit_kind",
    "halt_exempt_protective_exit",
)


def _definiert(pfad: Path) -> set[str]:
    baum = ast.parse(pfad.read_text(encoding="utf-8"))
    return {
        k.name
        for k in baum.body
        if isinstance(k, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_acht_funktionen_wohnen_in_order_aufbau():
    assert ZIEL.exists(), "core/engine/order_aufbau.py fehlt"
    assert set(NAMEN) <= _definiert(ZIEL)


def test_kern_definiert_sie_nicht_mehr():
    assert not set(NAMEN) & _definiert(KERN)


def test_re_export_ist_dasselbe_objekt():
    from core.engine import order_aufbau, order_executor, signal_uebergabe

    for name in NAMEN:
        assert getattr(order_executor, name) is getattr(order_aufbau, name), name
    for name in UEBERGABE_NAMEN:
        assert getattr(signal_uebergabe, name) is getattr(order_aufbau, name), name


def test_order_aufbau_importiert_den_kern_nicht():
    assert ZIEL.exists(), "core/engine/order_aufbau.py fehlt"
    baum = ast.parse(ZIEL.read_text(encoding="utf-8"))
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Import):
            module = [a.name for a in knoten.names]
        elif isinstance(knoten, ast.ImportFrom):
            module = [knoten.module or ""] + [a.name for a in knoten.names]
        else:
            continue
        assert not any(
            m.rpartition(".")[2] == "order_executor" for m in module
        ), f"order_aufbau.py:{knoten.lineno} importiert den Kern"
