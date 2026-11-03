"""#4656 (Epic #4638) — der Iron-Dome-Code je Symbol aus dem Entscheidungsspeicher.

Plan: ``docs/4656-qa-trichter-daten-endstatus-und-grund-je-llm-vor/implementation_plan.md``.

Der Order-Pfad legt seinen Ausfuehrungscode ueber ``order_executor._rec_outcome`` in
``execution_outcomes`` ab; ``_attach_execution_outcomes`` haengt ihn beim Lesen an die
Entscheidung. ``iron_dome_ergebnisse`` reicht genau diesen maschinellen Code je Symbol an den
QA-Trichter weiter. Der Speicher bleibt fail-safe.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc1]

_TS = "2026-10-22T14:05:00+00:00"
_EPOCH = datetime(2026, 10, 22, 14, 5, tzinfo=timezone.utc).timestamp()


@pytest.fixture
def rd():
    from core.round_table import execution_outcomes
    from core.round_table import recent_decisions as rd

    rd.clear_recent_round_table_decisions()
    execution_outcomes.clear_execution_outcomes()
    yield rd
    rd.clear_recent_round_table_decisions()
    execution_outcomes.clear_execution_outcomes()


def _entscheidung(symbol, aktion):
    return {"symbol": symbol, "signal_action": aktion, "timestamp": _TS}


def test_iron_dome_code_wird_mitgefuehrt(rd):
    from core.round_table.execution_outcomes import record_execution_outcome

    rd.record_round_table_decision(_entscheidung("AAPL", "BUY"))
    rd.record_round_table_decision(_entscheidung("MSFT", "SELL"))
    rd.record_round_table_decision(_entscheidung("NVDA", "HOLD"))
    rd.record_round_table_decision(_entscheidung("AMD", "BUY"))
    record_execution_outcome("AAPL", "blocked:order_value", "Ordervolumen", ts=_EPOCH)
    record_execution_outcome("MSFT", "executed", ts=_EPOCH)

    ergebnisse = rd.iron_dome_ergebnisse()

    # Nur der maschinelle Code, nie der Freitext; HOLD erreicht den Order-Pfad nie.
    assert ergebnisse == {
        "AAPL": "blocked:order_value",
        "MSFT": "executed",
        "AMD": "pending",
    }


def test_record_wirft_weiter_nie(rd):
    class Kaputt:
        def __getattr__(self, name):
            raise RuntimeError("kaputt")

    rd.record_round_table_decision(Kaputt())
    rd.record_round_table_decision({"symbol": None})
    rd.record_round_table_decision(_entscheidung("AAPL", "BUY"))

    with patch(
        "core.round_table.recent_decisions.get_recent_round_table_decisions",
        side_effect=RuntimeError("kaputt"),
    ):
        assert rd.iron_dome_ergebnisse() == {}
