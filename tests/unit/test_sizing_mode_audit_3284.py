# tests/unit/test_sizing_mode_audit_3284.py
"""#3284 Punkt 3 — MiFID II Art. 17 „applied == logged" for the sizing PROCEDURE.

The sizer records WHICH procedure formed the size ("conviction" | "a" | "b") plus the
target base weight into ``sizing_trace``; ``apply_sizing_mode_audit`` mirrors them onto the
DecisionContext (persisted via decisions.sizing_mode / sizing_target_weight). Since
clean-weight "b" is the default, risk_size_scaler alone is mode-ambiguous — this closes it.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.cloud_logger import DecisionContext
from core.risk_manager import RiskManager, apply_sizing_mode_audit


@pytest.fixture(autouse=True)
def _ks():
    from core.kill_switch import kill_switch

    kill_switch.reset()
    kill_switch.redis_client = None
    kill_switch._initialized = True
    yield
    kill_switch.reset()


def _cfg_n(n):
    return SimpleNamespace(
        CASH_AWARE_SLOTS_ENABLED=False,
        MAX_POSITIONS=n,
        FULL_UNIVERSE_TRADING_ENABLED=False,
        FULL_UNIVERSE_MAX_POSITIONS=n,
    )


def _trace(mode, **kw):
    import config

    with patch.object(config, "CLEAN_WEIGHT_SIZING", mode), patch.object(
        config, "VOL_TARGETING_SIZING_ENABLED", True
    ), patch("config.get_config", return_value=_cfg_n(10)), patch(
        "core.risk_manager.CLOUD_LOGGING_AVAILABLE", False
    ):
        t: dict = {}
        RiskManager(None, 100_000.0).calculate_position_size(
            stop_loss_atr_multiplier=3.0,
            atr=2.0,
            confidence="medium",
            current_price=100.0,
            account_cash=10_000_000.0,
            allow_fractional=True,
            conviction_score=0.5,
            market_data={"vix": 20.0},
            num_stocks_in_strategy=1,
            sizing_trace=t,
            **kw,
        )
        return t


class TestSizerRecordsMode:
    def test_off_is_conviction(self):
        t = _trace("off")
        assert t.get("sizing_mode") == "conviction"
        assert t.get("sizing_target_weight") == pytest.approx(0.175)  # 0.05+0.25*0.5

    def test_arm_a_is_strict_one_over_n(self):
        t = _trace("a")
        assert t.get("sizing_mode") == "a"
        assert t.get("sizing_target_weight") == pytest.approx(0.10)  # 1/N, N=10

    def test_arm_b_is_vol_tilted(self):
        t = _trace("b", forecast_vol=0.0075)  # vol scaler 1.5
        assert t.get("sizing_mode") == "b"
        assert t.get("sizing_target_weight") == pytest.approx(0.15)  # 1/N * 1.5


class TestAuditMirror:
    def test_copies_trace_to_context(self):
        ctx = DecisionContext(symbol="X")
        apply_sizing_mode_audit(ctx, {"sizing_mode": "b", "sizing_target_weight": 0.15})
        assert ctx.sizing_mode == "b"
        assert ctx.sizing_target_weight == pytest.approx(0.15)

    def test_defensive_noop(self):
        # None context / None trace / empty trace must never raise and must not invent values.
        apply_sizing_mode_audit(None, {"sizing_mode": "b"})
        apply_sizing_mode_audit(DecisionContext(symbol="X"), None)
        ctx = DecisionContext(symbol="X")
        apply_sizing_mode_audit(ctx, {})
        assert ctx.sizing_mode is None and ctx.sizing_target_weight is None


class TestSchema:
    def test_context_defaults_none(self):
        ctx = DecisionContext(symbol="X")
        assert ctx.sizing_mode is None and ctx.sizing_target_weight is None

    def test_decisions_columns_exist(self):
        from core.database.models import Decision

        cols = set(Decision.__table__.columns.keys())
        assert {"sizing_mode", "sizing_target_weight"} <= cols
