"""#4631 (STW-5) — Alle vier Leser des Audit-Verzeichnisses folgen ``SENATE_LOG_DIR``.

Die WORM-Kette wird fuer mehrere Profile **nicht** umgebaut (Plan §2.2): ``SENATE_LOG_DIR`` ist
schon heute der Schalter, und der Launcher (#4632) setzt ihn je Profil. Das traegt nur, solange
**jeder** Leser die Variable ehrt — ein Leser mit eigenem Vorgabewert schriebe zwei Profile in
dieselbe Kette oder suchte die Nonces im falschen Verzeichnis. Dieser Test haelt den Ist-Zustand
fest, gegen eine spaetere Abweichung.

Die vier Leser (Plan §1): ``LocalJSONAuditLogger.__init__``,
``hitl_gate.collect_live_enablement_nonces``, ``hitl_gate.assert_worm_writable`` und
``JsonlSenateLogReader.__init__``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc4]


@pytest.fixture
def senate_dir(monkeypatch, tmp_path):
    ziel = tmp_path / "profil2" / "cloud_fallback_logs"
    monkeypatch.setenv("SENATE_LOG_DIR", str(ziel))
    return ziel


@pytest.fixture
def ohne_audit_logger(monkeypatch):
    """Kein konfigurierter Logger mit ``_log_dir`` — dann greift der Rueckfall auf die
    Variable, und genau den prueft dieser Test."""
    from core import hitl_gate

    monkeypatch.setattr(hitl_gate, "_resolve_audit_logger", lambda: object())


def test_jsonl_audit_logger_schreibt_nach_senate_log_dir(senate_dir):
    from core.round_table.senate_log import LocalJSONAuditLogger

    logger = LocalJSONAuditLogger()

    assert Path(logger._log_dir) == senate_dir
    assert senate_dir.is_dir()


def test_jsonl_leser_liest_aus_senate_log_dir(senate_dir):
    from core.xai.trading_history import JsonlSenateLogReader

    assert Path(JsonlSenateLogReader()._log_dir) == senate_dir


def test_worm_vorpruefung_prueft_senate_log_dir(senate_dir, ohne_audit_logger):
    from core import hitl_gate

    hitl_gate.assert_worm_writable()

    assert senate_dir.is_dir()


async def test_nonce_sammlung_liest_aus_senate_log_dir(
    senate_dir, ohne_audit_logger, monkeypatch
):
    from core import hitl_gate

    senate_dir.mkdir(parents=True)
    (senate_dir / hitl_gate.SIEGEL_DATEI).write_text("kein json", encoding="utf-8")
    # Spion statt caplog: unabhaengig davon, was andere Tests am Logging umgebaut haben.
    warnung = MagicMock()
    monkeypatch.setattr(hitl_gate.logger, "warning", warnung)

    await hitl_gate.collect_live_enablement_nonces()

    # Das unlesbare Siegel liegt NUR unter SENATE_LOG_DIR — die Warnung belegt, dass dort
    # gelesen wurde.
    assert any("Siegel nicht lesbar" in str(c.args[0]) for c in warnung.call_args_list)
