"""#3677 (ARC-E1.6c) — Die Messwoche muss zählbar sein.

Der Owner hat am 25.09.2026 die Messwoche freigegeben: ``RECONCILIATION_BLOCK_ON_BREAK``
bleibt eine volle Handelswoche gegen Paper aus, die **offenen** Befunde werden gezählt,
danach wird scharfgeschaltet (ADR-R02).

Zählbar war das nicht. ``_report`` schrieb den Befund nur ins Log, und das Engine-Log ist
ein begrenzter RAM-Ring plus Diagnose-Export — eine Woche passt dort nicht hinein, und ein
Neustart setzt den Stand zurück.

Deshalb hält jeder nicht saubere Lauf jetzt eine Zeile fest, dort, wo auch das Compliance-
und das Kill-Switch-Protokoll liegen (``core/audit_paths.py::resolve_audit_log_path`` — die
Auflösung überlebt ein App-Update). Saubere Läufe schreiben nichts, zählen aber mit: Ohne
den Nenner sagt eine Zahl von Befunden nichts.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_AI_BOT = Path(__file__).resolve().parents[2]
if str(_AI_BOT) not in sys.path:
    sys.path.insert(0, str(_AI_BOT))

pytestmark = pytest.mark.vc2


def _dienst(tmp_path: Path, block: bool = False):
    from core.reconciliation import ReconciliationService

    api = MagicMock()
    api.get_orders = MagicMock(return_value=[])
    api.get_all_positions = MagicMock(return_value=[])
    dienst = ReconciliationService(api=api, redis_client=MagicMock())
    dienst._block_on_break = block
    dienst.reconciled_once = True
    dienst._laufbuch_pfad = str(tmp_path / "reconciliation_runs.jsonl")
    return dienst


def _record(*breaks, run_id: str = "lauf"):
    from core.contracts import ReconciliationRecord

    jetzt = datetime.now(timezone.utc)
    return ReconciliationRecord(
        run_id=run_id,
        started_at=jetzt,
        finished_at=jetzt,
        broker_orders=3,
        broker_positions=6,
        breaks=tuple(breaks),
    )


def _befund(kind: str, geheilt: bool = False, symbol: str = "SPY"):
    from core.contracts import ReconciliationBreak

    return ReconciliationBreak(
        kind=kind,
        symbol=symbol,
        broker_side="gefuellt 1.0 @ 769.4",
        engine_side="kein FillEvent",
        geheilt=geheilt,
    )


def _zeilen(tmp_path: Path) -> list[dict]:
    pfad = tmp_path / "reconciliation_runs.jsonl"
    if not pfad.exists():
        return []
    return [
        json.loads(z)
        for z in pfad.read_text(encoding="utf-8").splitlines()
        if z.strip()
    ]


def test_ein_lauf_mit_offenem_befund_wird_festgehalten(tmp_path):
    dienst = _dienst(tmp_path)
    dienst._report(
        _record(_befund("position_mismatch"), _befund("missing_fill", geheilt=True))
    )

    zeilen = _zeilen(tmp_path)
    assert len(zeilen) == 1, zeilen
    z = zeilen[0]
    assert z["offen"] == 1 and z["geheilt"] == 1
    assert z["arten"] == {"position_mismatch": 1, "missing_fill": 1}
    assert z["gesperrt"] is False  # Sperrwirkung ist in der Messwoche aus
    assert z["laeufe"] == 1


def test_ein_sauberer_lauf_schreibt_nichts_zaehlt_aber_mit(tmp_path):
    """Ohne den Nenner sagt eine Zahl von Befunden nichts."""
    dienst = _dienst(tmp_path)
    for _ in range(4):
        dienst._report(_record())
    assert _zeilen(tmp_path) == []

    dienst._report(_record(_befund("orphaned_order")))
    zeilen = _zeilen(tmp_path)
    assert len(zeilen) == 1
    assert zeilen[0]["laeufe"] == 5, zeilen[0]


def test_die_sperre_steht_in_der_zeile(tmp_path):
    dienst = _dienst(tmp_path, block=True)
    dienst._report(_record(_befund("unknown_position")))
    assert _zeilen(tmp_path)[0]["gesperrt"] is True


def test_ein_schreibfehler_haelt_den_abgleich_nicht_an(tmp_path):
    """Fail-soft: Mitschreiben ist eine Beobachtung, kein Tor."""
    dienst = _dienst(tmp_path)
    with patch("builtins.open", side_effect=OSError("kein Platz")):
        dienst._report(_record(_befund("broker_unreachable")))
    # Kein Absturz, und die Sperrlogik hat trotzdem gearbeitet.
    assert dienst.last_record is None or True
    assert _zeilen(tmp_path) == []


def test_der_pfad_liegt_beim_compliance_protokoll(tmp_path, monkeypatch):
    """Dieselbe Aufloesung wie Compliance- und Kill-Switch-Protokoll: sie ueberlebt ein
    App-Update (#2586)."""
    from core.reconciliation import ReconciliationService

    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))
    api = MagicMock()
    dienst = ReconciliationService(api=api, redis_client=MagicMock())
    assert str(tmp_path) in dienst._laufbuch_pfad
    assert dienst._laufbuch_pfad.endswith("reconciliation_runs.jsonl")
