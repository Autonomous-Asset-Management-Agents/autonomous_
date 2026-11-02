"""#4630 (STW-4) — ``log_strategy_assignment_event`` und der Nonce-Scan fuer Zuweisungen.

Plan: ``docs/4630-*/implementation_plan.md`` §2 Punkt 2, TDD-Reihenfolge Schritt 2.

Der Scan-Test laeuft gegen eine echte JSONL-Kette (``LocalJSONAuditLogger`` in ``tmp_path``),
nicht gegen einen gemockten Scan: geprueft wird, was auf der Platte steht.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from core import hitl_gate
from core.round_table.senate_log import LocalJSONAuditLogger, StrategyAssignmentEvent

pytestmark = [pytest.mark.unit, pytest.mark.vc0]


def _kw(**ueber):
    kw = {
        "strategie": "B",
        "strategie_hash": "sha256:" + "a" * 64,
        "changes": [{"key": "STOP_LOSS_PCT", "from": "7.0", "to": "5.0"}],
        "acknowledgment": "bestaetigt",
        "nonce": "n-assign-1",
    }
    kw.update(ueber)
    return kw


@pytest.fixture
def sink(monkeypatch):
    s = MagicMock()
    s.log_hitl_event = AsyncMock()
    monkeypatch.setattr(hitl_gate, "_resolve_audit_logger", lambda: s)
    return s


@pytest.fixture
def kette(tmp_path, monkeypatch):
    monkeypatch.setenv("SENATE_LOG_DIR", str(tmp_path))
    echter = LocalJSONAuditLogger()
    monkeypatch.setattr(hitl_gate, "_resolve_audit_logger", lambda: echter)
    return tmp_path


def _zeilen(log_dir):
    return [
        json.loads(z)
        for f in sorted(log_dir.glob("audit_log_*.jsonl"))
        for z in f.read_text(encoding="utf-8").splitlines()
        if z.strip()
    ]


async def test_log_strategy_assignment_schreibt_das_event(sink):
    await hitl_gate.log_strategy_assignment_event(**_kw())

    (event,), _ = sink.log_hitl_event.await_args
    assert isinstance(event, StrategyAssignmentEvent)
    assert (event.strategie, event.konto, event.nonce) == ("B", "live", "n-assign-1")
    assert event.switch_id == "n-assign-1", "switch_id faellt auf die Nonce"
    assert event.actor == "operator"
    assert json.loads(event.changes) == _kw()["changes"]


async def test_log_strategy_assignment_leerer_diff_wird_versiegelt(sink):
    """Die Zuweisung selbst ist der Revisionsakt — auch ohne Wertaenderung (Plan §2.3.7)."""
    await hitl_gate.log_strategy_assignment_event(**_kw(changes=[]))

    (event,), _ = sink.log_hitl_event.await_args
    assert event.changes == "[]"


async def test_log_strategy_assignment_strict_wirft_weiter(sink):
    sink.log_hitl_event.side_effect = OSError("Platte voll")

    with pytest.raises(OSError):
        await hitl_gate.log_strategy_assignment_event(**_kw())
    # strict=False schluckt — die Route ruft nie so, der Vertrag bleibt aber derselbe.
    await hitl_gate.log_strategy_assignment_event(**_kw(), strict=False)


async def test_log_strategy_assignment_event_gibt_timestamp(sink):
    """#4891: Der Spiegel ``zuweisungen.json`` traegt denselben Zeitpunkt wie die Kette."""
    zeitpunkt = await hitl_gate.log_strategy_assignment_event(**_kw())

    (event,), _ = sink.log_hitl_event.await_args
    assert zeitpunkt is not None and zeitpunkt == event.timestamp
    sink.log_hitl_event.side_effect = OSError("Platte voll")
    assert await hitl_gate.log_strategy_assignment_event(**_kw(), strict=False) is None


@pytest.mark.parametrize("feld", ["strategie", "strategie_hash"])
async def test_assignment_ohne_namen_ist_valueerror(feld, sink):
    with pytest.raises(ValueError):
        await hitl_gate.log_strategy_assignment_event(**_kw(**{feld: ""}))
    sink.log_hitl_event.assert_not_awaited()


async def test_collect_nonces_scannt_strategy_assignment_auf_jsonl_kette(kette):
    await hitl_gate.log_strategy_assignment_event(**_kw(nonce="N_ASSIGN"))

    assert [z["event_type"] for z in _zeilen(kette)] == ["strategy_assignment"]
    assert "N_ASSIGN" in await hitl_gate.collect_live_enablement_nonces()
