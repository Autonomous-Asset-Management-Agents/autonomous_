"""#4629 (STW-3) — ``StrategyDefinitionEvent`` auf der Kette, neben ``settings_change``.

Plan: ``docs/4629-*/implementation_plan.md`` §2 Punkt 2, TDD-Reihenfolge Schritt 2.
"""

from __future__ import annotations

import json
from dataclasses import fields

import pytest

from core.round_table.senate_log import StrategyDefinitionEvent, _hitl_event_to_dict

pytestmark = [pytest.mark.unit, pytest.mark.vc0]


def _event(**ueber) -> StrategyDefinitionEvent:
    felder = {
        "timestamp": "2026-10-27T10:00:00+00:00",
        "actor": "operator",
        "acknowledgment": "bestaetigt",
        "nonce": "n-stw-1",
        "aktion": "create",
        "name": "A",
        "werte": '{"STOP_LOSS_PCT":"5.0"}',
        "switch_id": "n-stw-1",
    }
    felder.update(ueber)
    return StrategyDefinitionEvent(**felder)


def test_strategy_definition_diskriminator():
    d = _hitl_event_to_dict(_event())
    assert d["event_type"] == "strategy_definition"
    assert set(d) == {
        "event_type",
        "timestamp",
        "actor",
        "acknowledgment",
        "nonce",
        "aktion",
        "name",
        "werte",
        "switch_id",
    }


def test_strategy_definition_nur_str_felder():
    assert all(
        f.type in ("str", "Optional[str]") for f in fields(StrategyDefinitionEvent)
    )
    d = _hitl_event_to_dict(_event())
    assert all(isinstance(v, str) for v in d.values()), "str-only fuer Byte-Paritaet"
    json.loads(d["werte"])


@pytest.mark.asyncio
async def test_strategy_definition_landet_auf_der_kette(tmp_path, monkeypatch):
    from core.round_table.senate_log import LocalJSONAuditLogger

    monkeypatch.setenv("SENATE_LOG_DIR", str(tmp_path))
    await LocalJSONAuditLogger().log_hitl_event(_event())
    zeilen = [
        json.loads(z)
        for f in tmp_path.glob("audit_log_*.jsonl")
        for z in f.read_text(encoding="utf-8").splitlines()
        if z.strip()
    ]
    assert [z["event_type"] for z in zeilen] == ["strategy_definition"]
    assert len(zeilen[0]["hash"]) == 64
