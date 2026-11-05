"""#4648 (Epic #4638) — harte NFR: kein Draht vom LLM zum Broker.

Die LLM-Ranking-Quelle liefert nur eine Rangliste fuer den Funnel. Sie importiert weder
``core.engine.order_executor`` noch ``core.contracts.signal_candidate``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc1]

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
MODUL = PAKET / "core" / "engine" / "llm_ranking.py"
VERBOTEN = ("order_executor", "signal_candidate")


def _importe(quelle: str) -> list[str]:
    namen: list[str] = []
    for knoten in ast.walk(ast.parse(quelle)):
        if isinstance(knoten, ast.Import):
            namen += [a.name for a in knoten.names]
        elif isinstance(knoten, ast.ImportFrom):
            basis = knoten.module or ""
            namen.append(basis)
            namen += [f"{basis}.{a.name}" for a in knoten.names]
    return namen


def _verbotene(quelle: str) -> list[str]:
    return [n for n in _importe(quelle) if any(v in n.split(".") for v in VERBOTEN)]


def test_llm_ranking_importiert_keinen_orderpfad():
    assert MODUL.is_file(), f"fehlt: {MODUL}"
    assert _verbotene(MODUL.read_text(encoding="utf-8-sig")) == []


def test_der_pruefer_faengt_beide_importformen():
    quelle = (
        "import core.engine.order_executor\n"
        "from core.contracts import signal_candidate\n"
        "from core.engine import rank_funnel\n"
    )
    assert _verbotene(quelle) == [
        "core.engine.order_executor",
        "core.contracts.signal_candidate",
    ]
