"""#4125 Teil 4 (Plan §2 Punkt 5, §13d L3): ``decision_outcomes.exit_reason`` traegt die Klasse.

- ``build_outcome_row(ctx, verkaufsgrund=…)`` setzt ``exit_reason`` = Klasse; ohne Grund ``None``.
- ``capture_decision_outcome`` und ``order_executor._capture_outcome`` reichen den Grund durch.
- Die Aufrufer geben bei SELL den Grund der Absendung mit (``verkaufsgrund_der_absendung``), bei
  BUY ``None``.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import config
from core.cloud_logger import DecisionContext
from core.contracts.verkaufsgrund import Verkaufsgrund

pytestmark = pytest.mark.vc3

_LOSS_CUT = Verkaufsgrund(
    klasse="loss_cut", werte={"verlust_pct": -4.2, "stufe": -4.0, "haltedauer_h": 26.0}
)


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setattr(
        config.get_config(), "DECISION_CAPTURE_ENABLED", True, raising=False
    )


def _ctx(action="SELL") -> DecisionContext:
    return DecisionContext(symbol="AAPL", action=action, current_price=150.0)


def test_build_outcome_row_setzt_exit_reason_aus_der_klasse():
    from core.decision_capture.capture import build_outcome_row

    assert (
        build_outcome_row(_ctx(), verkaufsgrund=_LOSS_CUT)["exit_reason"] == "loss_cut"
    )


def test_build_outcome_row_ohne_grund_bleibt_exit_reason_leer():
    from core.decision_capture.capture import build_outcome_row

    assert build_outcome_row(_ctx("BUY"))["exit_reason"] is None


def test_capture_reicht_den_grund_in_die_zeile(flag_on):
    from core.decision_capture.capture import capture_decision_outcome

    sink = MagicMock()
    with patch("core.cloud_logger.get_cloud_logger", return_value=sink):
        capture_decision_outcome(_ctx(), verkaufsgrund=_LOSS_CUT)
    (zeile,), _ = sink.log_decision_outcome.call_args
    assert zeile["exit_reason"] == "loss_cut"


def test_capture_outcome_des_executors_reicht_den_grund_durch():
    from core.engine import order_executor

    with patch("core.decision_capture.capture.capture_decision_outcome") as capture:
        order_executor._capture_outcome("ctx", verkaufsgrund=_LOSS_CUT)
    capture.assert_called_once_with("ctx", verkaufsgrund=_LOSS_CUT)


def _engine(**mehr):
    from core.engine.base import BotEngine

    engine = BotEngine.__new__(BotEngine)
    engine.cloud_logger = MagicMock()
    for name, wert in mehr.items():
        setattr(engine, name, wert)
    return engine


@pytest.mark.parametrize("action, erwartet", [("SELL", _LOSS_CUT), ("BUY", None)])
def test_markt_tor_gibt_den_grund_der_absendung_mit(action, erwartet):
    """Aufrufer ``signal_uebergabe._schritt_markt_offen``: SELL mit Grund, BUY ohne."""
    engine = _engine(_market_closed_blocks_order=AsyncMock(return_value=True))
    event = SimpleNamespace(
        symbol="AAPL",
        action=action,
        suggested_quantity=1.0,
        decision_context=SimpleNamespace(current_price=150.0),
        is_simulation=False,
        verkaufsgrund=_LOSS_CUT,
    )
    with patch("core.engine.order_executor._capture_outcome") as capture:
        assert asyncio.run(engine._schritt_markt_offen(event, True)) is False
    capture.assert_called_once_with(event.decision_context, verkaufsgrund=erwartet)
