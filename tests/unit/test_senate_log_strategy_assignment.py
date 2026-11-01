"""#4630 (STW-4) — ``StrategyAssignmentEvent`` auf der Kette, neben ``strategy_definition``.

Plan: ``docs/4630-*/implementation_plan.md`` §2 Punkt 1, TDD-Reihenfolge Schritt 1.
"""

from __future__ import annotations

import json
from dataclasses import fields

import pytest

from core.round_table.senate_log import StrategyAssignmentEvent, _hitl_event_to_dict

pytestmark = [pytest.mark.unit, pytest.mark.vc0]


def _event(**ueber) -> StrategyAssignmentEvent:
    felder = {
        "timestamp": "2026-10-27T10:00:00+00:00",
        "actor": "operator",
        "acknowledgment": "bestaetigt",
        "nonce": "n-assign-1",
        "switch_id": "n-assign-1",
        "konto": "live",
        "strategie": "B",
        "strategie_hash": "sha256:" + "0" * 64,
        "changes": '[{"from":"7.0","key":"STOP_LOSS_PCT","to":"5.0"}]',
    }
    felder.update(ueber)
    return StrategyAssignmentEvent(**felder)


def test_strategy_assignment_diskriminator():
    d = _hitl_event_to_dict(_event())
    assert d["event_type"] == "strategy_assignment"
    assert set(d) == {
        "event_type",
        "timestamp",
        "actor",
        "acknowledgment",
        "nonce",
        "switch_id",
        "konto",
        "strategie",
        "strategie_hash",
        "changes",
    }


def test_strategy_assignment_nur_str_felder():
    assert all(
        f.type in ("str", "Optional[str]") for f in fields(StrategyAssignmentEvent)
    )
    d = _hitl_event_to_dict(_event())
    assert all(isinstance(v, str) for v in d.values()), "str-only fuer Byte-Paritaet"
    assert json.loads(d["changes"])[0]["key"] == "STOP_LOSS_PCT"
