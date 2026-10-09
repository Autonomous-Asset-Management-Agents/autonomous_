"""#4280 (H-4g) — Netz für ``_maybe_record_shadow_specialist_vote`` vor dem Umzug.

Plan: ``docs/4280-*/implementation_plan.md`` §5 Schritt 1. Entscheidung
``docs/3738-arc-e6-gestalt/H4_SCHNITT_round_table_runner.md`` §2/§4 nennt den Flag-an-Pfad
als Netzlücke (6/17 Anweisungen); die Charakterisierung aus H-4a setzt
``SHADOW_SPECIALIST_VOTE_ENABLED`` auf ``False``.

Aufgerufen wird über ``core.round_table.runner`` (Wiederausfuhr nach dem Umzug), damit der
Test vor und nach H-4g derselbe ist. Gepatcht werden nur die Quellen, die der Haken spät
liest: ``config.get_config``, ``core.round_table.agents._specialist_registry_instance`` und
``core.round_table.shadow_specialist_recorder.record_shadow_specialist_vote``.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.h1]

_RECORDER = "core.round_table.shadow_specialist_recorder.record_shadow_specialist_vote"
_REGISTRY = "core.round_table.agents._specialist_registry_instance"


def _cfg(enabled: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        SHADOW_SPECIALIST_VOTE_ENABLED=enabled,
        SHADOW_SPECIALIST_VOTE_CHAIN_PATH="kette.jsonl",
    )


def _registry(report) -> MagicMock:
    registry = MagicMock()
    registry.get_report.return_value = report
    return registry


def _bericht() -> SimpleNamespace:
    return SimpleNamespace(sentiment_score=0.42, recommendation="BUY", escalate=True)


def test_flag_aus_ist_noop():
    from core.round_table import runner

    registry = _registry(_bericht())
    with patch("config.get_config", return_value=_cfg(False)), patch(
        _REGISTRY, registry
    ), patch(_RECORDER) as recorder:
        runner._maybe_record_shadow_specialist_vote("AAPL", 0.7, "BUY")

    registry.get_report.assert_not_called()
    recorder.assert_not_called()


def test_ohne_registry_ist_noop():
    from core.round_table import runner

    with patch("config.get_config", return_value=_cfg()), patch(_REGISTRY, None), patch(
        _RECORDER
    ) as recorder:
        runner._maybe_record_shadow_specialist_vote("AAPL", 0.7, "BUY")

    recorder.assert_not_called()


def test_ohne_bericht_ist_noop():
    from core.round_table import runner

    registry = _registry(None)
    with patch("config.get_config", return_value=_cfg()), patch(
        _REGISTRY, registry
    ), patch(_RECORDER) as recorder:
        runner._maybe_record_shadow_specialist_vote("AAPL", 0.7, "BUY")

    registry.get_report.assert_called_once_with("AAPL")
    recorder.assert_not_called()


def test_flag_an_reicht_berichtsfelder_an_recorder():
    from core.round_table import runner

    with patch("config.get_config", return_value=_cfg()), patch(
        _REGISTRY, _registry(_bericht())
    ), patch(_RECORDER) as recorder:
        ergebnis = runner._maybe_record_shadow_specialist_vote("AAPL", 0.7, "BUY")

    assert ergebnis is None
    recorder.assert_called_once_with(
        symbol="AAPL",
        sentiment_score=0.42,
        recommendation="BUY",
        escalate=True,
        consensus_score=0.7,
        real_action="BUY",
        chain_path="kette.jsonl",
    )


def test_flag_an_ohne_kettenpfad_nimmt_default():
    from core.round_table import runner

    cfg = SimpleNamespace(SHADOW_SPECIALIST_VOTE_ENABLED=True)
    with patch("config.get_config", return_value=cfg), patch(
        _REGISTRY, _registry(SimpleNamespace())
    ), patch(_RECORDER) as recorder:
        runner._maybe_record_shadow_specialist_vote("MSFT", 0.3, None)

    recorder.assert_called_once_with(
        symbol="MSFT",
        sentiment_score=None,
        recommendation=None,
        escalate=False,
        consensus_score=0.3,
        real_action=None,
        chain_path="shadow_specialist_votes.jsonl",
    )


def test_recorder_fehler_loggt_warning_unter_runner_logger(caplog):
    from core.round_table import runner

    with patch("config.get_config", return_value=_cfg()), patch(
        _REGISTRY, _registry(_bericht())
    ), patch(_RECORDER, side_effect=OSError("platte voll")), caplog.at_level(
        logging.WARNING, logger="core.round_table.runner"
    ):
        runner._maybe_record_shadow_specialist_vote("AAPL", 0.7, "BUY")

    treffer = [
        r
        for r in caplog.records
        if r.name == "core.round_table.runner" and r.levelno == logging.WARNING
    ]
    assert len(treffer) == 1
    assert "shadow-specialist-vote hook failed for AAPL" in treffer[0].getMessage()
    assert "platte voll" in treffer[0].getMessage()
