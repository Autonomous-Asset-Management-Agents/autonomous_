# tests/unit/test_position_size_vol_targeting.py
"""#1953 TRD-2 Inc 1 — vol-targeting docking in calculate_position_size (T6-T10).

The sizer gains an ADDITIVE `forecast_vol` parameter (default None = old
behaviour). Behind VOL_TARGETING_SIZING_ENABLED (default OFF) the HAR-RV
forecast becomes an inverse-vol risk-parity factor inside final_risk_scaler:

  ON  + valid forecast  -> size scales by clip(target/fv, lo, hi)
  ON  + missing forecast-> fail-safe no-op at WARNING (desktop neutral state)
  OFF                   -> byte-identical to today (BORA dark-ship guarantee)

The factor applies BEFORE every hard cap (max-% / cash / exposure /
compliance) — risk parity may never bypass a safety ceiling (T9).
"""

import logging
from unittest.mock import MagicMock, patch

import pytest

from core.risk_manager import RiskManager, vol_targeting_scaler


@pytest.fixture(autouse=True)
def reset_kill_switch_fixture():
    from core.kill_switch import kill_switch

    kill_switch.reset()
    kill_switch.redis_client = None
    kill_switch._initialized = True
    yield
    kill_switch.reset()


def _rm(capital: float = 20_000.0) -> RiskManager:
    # No broker client: isolates the sizing math (the aggregate exposure cap
    # requires self.client — same shape as tests/unit/test_risk_forbid_leverage.py).
    with patch("core.risk_manager.CLOUD_LOGGING_AVAILABLE", False):
        return RiskManager(None, capital)


_PRICE = 100.0


def _size(rm: RiskManager, conviction: float = 0.5, **kw) -> float:
    return rm.calculate_position_size(
        stop_loss_atr_multiplier=3.0,
        atr=2.0,
        confidence="medium",
        size_scaler=1.0,
        num_stocks_in_strategy=1,
        current_price=_PRICE,
        account_cash=15_000.0,
        allow_fractional=True,
        conviction_score=conviction,
        market_data={"vix": 20.0},
        **kw,
    )


def _flag(monkeypatch, enabled: bool) -> None:
    import config

    monkeypatch.setattr(config, "VOL_TARGETING_SIZING_ENABLED", enabled, raising=False)


class TestRiskParityDirection:
    def test_t6_calm_sized_up_wild_sized_down(self, monkeypatch):
        """Gherkin core: identical conviction/price/atr/cash — the calm name
        (fv=0.0075, hi-clamped 1.5x) must be sized ABOVE the wild one
        (fv=0.03, lo-clamped 0.5x), and both scale off the same baseline."""
        _flag(monkeypatch, True)
        rm = _rm()
        baseline = _size(rm)  # no forecast -> WARNING no-op baseline
        calm = _size(rm, forecast_vol=0.0075)
        wild = _size(rm, forecast_vol=0.03)
        assert calm > wild
        assert calm == pytest.approx(baseline * 1.5, rel=1e-6)
        assert wild == pytest.approx(baseline * 0.5, rel=1e-6)


class TestDarkShipByteIdentity:
    def test_t7_flag_off_ignores_forecast(self, monkeypatch):
        """BORA: flag OFF (default) => a supplied forecast changes NOTHING."""
        _flag(monkeypatch, False)
        rm = _rm()
        assert _size(rm, forecast_vol=0.0075) == _size(rm)

    def test_t7b_default_signature_unchanged(self, monkeypatch):
        """Callers that never pass forecast_vol are untouched in either flag state."""
        _flag(monkeypatch, False)
        off = _size(_rm())
        _flag(monkeypatch, True)
        on = _size(_rm())
        assert off == on

    def test_t8_flag_on_missing_forecast_is_warned_noop(self, monkeypatch, caplog):
        """Fail-safe: ON + forecast None (desktop neutral state) == flag-off size,
        surfaced at WARNING (§5.6) — never a silent over-size."""
        _flag(monkeypatch, True)
        rm = _rm()
        with caplog.at_level(logging.WARNING):
            on_none = _size(rm, forecast_vol=None)
        _flag(monkeypatch, False)
        assert on_none == _size(rm)
        assert any(
            "forecast_vol" in r.message and "no-op" in r.message
            for r in caplog.records
            if r.levelno == logging.WARNING
        )


class TestScalerNeverBypassesCaps:
    def test_t9_max_position_cap_still_binds(self, monkeypatch):
        """A 1.5x calm-scaler at max conviction runs into MAX_POSITION_PERCENT:
        the final size equals the cap, not the scaled target (scaler acts BEFORE
        every hard ceiling)."""
        from config import MAX_POSITION_PERCENT

        _flag(monkeypatch, True)
        rm = _rm()
        cap_shares = rm.total_capital * MAX_POSITION_PERCENT / _PRICE
        uncapped = _size(rm, conviction=1.0) * 1.5  # what 1.5x WOULD produce
        capped = _size(rm, conviction=1.0, forecast_vol=0.0075)
        assert uncapped > cap_shares  # the scenario genuinely overshoots the cap
        assert capped == pytest.approx(cap_shares, rel=1e-6)


class TestAuditTrailMirror:
    def test_t10_effective_scaler_mirrored_into_context(self, monkeypatch):
        """MiFID traceability: the APPLIED vol scaler lands in
        DecisionContext.risk_size_scaler (existing audit sink, models.py:63)."""
        from core.cloud_logger import DecisionContext
        from core.risk_manager import apply_vol_targeting_audit

        _flag(monkeypatch, True)
        ctx = DecisionContext(symbol="TEST")
        # target 0.015 / fv 0.025 = 0.6 (Gherkin scenario value)
        scaler = apply_vol_targeting_audit(ctx, 0.025)
        assert scaler == pytest.approx(0.6)
        assert ctx.risk_size_scaler == pytest.approx(0.6)

    def test_t10b_flag_off_leaves_context_untouched(self, monkeypatch):
        from core.cloud_logger import DecisionContext
        from core.risk_manager import apply_vol_targeting_audit

        _flag(monkeypatch, False)
        ctx = DecisionContext(symbol="TEST")
        assert apply_vol_targeting_audit(ctx, 0.025) == 1.0
        assert ctx.risk_size_scaler == 1.0  # pristine default

    def test_t10c_none_context_is_safe(self, monkeypatch):
        from core.risk_manager import apply_vol_targeting_audit

        _flag(monkeypatch, True)
        assert apply_vol_targeting_audit(None, 0.025) == pytest.approx(0.6)


class TestConfigGatedScalerFunction:
    def test_flag_off_never_reads_tunables(self, monkeypatch):
        _flag(monkeypatch, False)
        assert vol_targeting_scaler(0.0075) == 1.0

    def test_flag_on_reads_tunables(self, monkeypatch):
        import config

        _flag(monkeypatch, True)
        monkeypatch.setattr(config, "VOL_TARGET_DAILY_VOL", 0.02, raising=False)
        monkeypatch.setattr(config, "VOL_SIZE_SCALER_LO", 0.25, raising=False)
        monkeypatch.setattr(config, "VOL_SIZE_SCALER_HI", 4.0, raising=False)
        assert vol_targeting_scaler(0.005) == pytest.approx(4.0)
        assert vol_targeting_scaler(0.08) == pytest.approx(0.25)
