"""#4629 (STW-3) — ``log_strategy_definition_event`` und der Nonce-Scan fuer Strategien.

Plan: ``docs/4629-*/implementation_plan.md`` §2 Punkt 3, TDD-Reihenfolge Schritt 3.

Der Scan-Test laeuft gegen eine echte JSONL-Kette (``LocalJSONAuditLogger`` in ``tmp_path``),
nicht gegen einen gemockten Scan: geprueft wird, was auf der Platte steht.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from core import hitl_gate
from core.round_table.senate_log import LocalJSONAuditLogger, StrategyDefinitionEvent

pytestmark = [pytest.mark.unit, pytest.mark.vc0]


def _kw(**ueber):
    kw = {
        "aktion": "create",
        "name": "A",
        "werte": '{"STOP_LOSS_PCT":"5.0"}',
        "acknowledgment": "bestaetigt",
        "nonce": "n-stw-1",
    }
    kw.update(ueber)
    return kw


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


async def test_log_strategy_definition_schreibt_das_event(monkeypatch):
    sink = MagicMock()
    sink.log_hitl_event = AsyncMock()
    monkeypatch.setattr(hitl_gate, "_resolve_audit_logger", lambda: sink)

    await hitl_gate.log_strategy_definition_event(**_kw())

    (event,), _ = sink.log_hitl_event.await_args
    assert isinstance(event, StrategyDefinitionEvent)
    assert (event.aktion, event.name, event.nonce) == ("create", "A", "n-stw-1")
    assert event.switch_id == "n-stw-1", "switch_id faellt auf die Nonce (D1)"
    assert event.actor == "operator"


async def test_log_strategy_definition_strict_wirft_weiter(monkeypatch):
    sink = MagicMock()
    sink.log_hitl_event = AsyncMock(side_effect=OSError("Platte voll"))
    monkeypatch.setattr(hitl_gate, "_resolve_audit_logger", lambda: sink)

    with pytest.raises(OSError):
        await hitl_gate.log_strategy_definition_event(**_kw())
    # strict=False schluckt — die Routen rufen nie so, der Vertrag bleibt aber derselbe.
    await hitl_gate.log_strategy_definition_event(**_kw(), strict=False)


@pytest.mark.parametrize("feld", ["name", "werte", "aktion"])
async def test_leerer_satz_ist_valueerror(feld, monkeypatch):
    sink = MagicMock()
    sink.log_hitl_event = AsyncMock()
    monkeypatch.setattr(hitl_gate, "_resolve_audit_logger", lambda: sink)

    with pytest.raises(ValueError):
        await hitl_gate.log_strategy_definition_event(**_kw(**{feld: ""}))
    sink.log_hitl_event.assert_not_awaited()


async def test_unbekannte_aktion_ist_valueerror(monkeypatch):
    sink = MagicMock()
    sink.log_hitl_event = AsyncMock()
    monkeypatch.setattr(hitl_gate, "_resolve_audit_logger", lambda: sink)

    with pytest.raises(ValueError):
        await hitl_gate.log_strategy_definition_event(**_kw(aktion="umbenennen"))


async def test_collect_nonces_scannt_strategy_definition_auf_jsonl_kette(kette):
    await hitl_gate.log_strategy_definition_event(**_kw(nonce="n-strategie"))

    assert [z["event_type"] for z in _zeilen(kette)] == ["strategy_definition"]
    assert "n-strategie" in await hitl_gate.collect_live_enablement_nonces()


async def test_settings_change_nonces_bleiben_aussen_vor(kette):
    """Der Lueckenschluss gilt ``strategy_definition`` — nicht jedem Event-Typ (Plan §2.3)."""
    await hitl_gate.log_settings_change_event(
        changes=[{"key": "STOP_LOSS_PCT", "from": "7.0", "to": "5.0"}],
        acknowledgment="bestaetigt",
        nonce="n-settings",
    )
    assert "n-settings" not in await hitl_gate.collect_live_enablement_nonces()


async def test_siegel_fassung_3_invalidiert_alte_siegel(kette):
    """Ein gueltiges Siegel der Fassung 2 kennt keine Strategie-Nonces — es wird verworfen.

    Pruefsumme und Anker stimmen; nur die Fassung ist alt. Gaelte es weiter, fehlte die
    Strategie-Nonce aus dem bereits gelesenen Abschnitt, und ein Replay ginge durch.
    """
    assert hitl_gate.SIEGEL_FASSUNG == 3

    await hitl_gate.log_strategy_definition_event(**_kw(nonce="n-alt"))
    datei = next(kette.glob("audit_log_*.jsonl"))
    zeile = _zeilen(kette)[-1]
    siegel = {
        "version": 2,
        "last_file": datei.name,
        "last_line_count": 1,
        "anchor_offset": 0,
        "last_hash": zeile["hash"],
        "nonces": [],
    }
    siegel["content_hash"] = hitl_gate._siegel_pruefsumme(siegel)
    (kette / hitl_gate.SIEGEL_DATEI).write_text(json.dumps(siegel), encoding="utf-8")

    gesehen = await hitl_gate.collect_live_enablement_nonces()

    assert "n-alt" in gesehen
    assert hitl_gate.letzte_lesestatistik()["siegel"] == "verworfen"
    neu = json.loads((kette / hitl_gate.SIEGEL_DATEI).read_text(encoding="utf-8"))
    assert neu["version"] == 3
    assert "n-alt" in neu["nonces"]
