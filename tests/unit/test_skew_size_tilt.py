# tests/unit/test_skew_size_tilt.py
"""#3199 — UpsideSkew sizing-asymmetry tilt: the pure ``skew_size_tilt`` helper.

Mirrors the #1953 ``vol_targeting_scaler`` contract (config-gated, dark by
default, fail-open). The 25Δ risk-reversal percentile ``p`` becomes a bounded
multiplicative size factor:

  tilt = clip(1 + 2*cap*(p - 0.5), 1-cap, 1+cap)

  p=0.5 (neutral)          -> 1.0
  p=1.0 (max upside skew)  -> 1+cap
  p=0.0 (max downside skew)-> 1-cap
  flag OFF / cap 0 / bad p -> 1.0 exactly (byte-identical, fail-open)

Purity matters: the SAME helper feeds the sizer AND the MiFID II audit mirror,
so the applied and logged multipliers can never drift (#3199 audit-trail fix).
"""

import logging

import pytest

from core.risk_manager import skew_size_tilt


def _skewflag(monkeypatch, enabled: bool, cap: float = 0.20) -> None:
    import config

    monkeypatch.setattr(config, "SKEW_SIZE_TILT_ENABLED", enabled, raising=False)
    monkeypatch.setattr(config, "SKEW_SIZE_TILT_CAP", cap, raising=False)


class TestDarkShipByteIdentity:
    def test_flag_off_is_one_regardless_of_p(self, monkeypatch):
        _skewflag(monkeypatch, False)
        for p in (0.0, 0.5, 1.0, None, 1.5):
            assert skew_size_tilt(p) == 1.0

    def test_cap_zero_is_neutral_even_when_enabled(self, monkeypatch):
        _skewflag(monkeypatch, True, cap=0.0)
        assert skew_size_tilt(1.0) == 1.0
        assert skew_size_tilt(0.0) == 1.0


class TestTiltDirection:
    def test_neutral_percentile_is_one(self, monkeypatch):
        _skewflag(monkeypatch, True, cap=0.20)
        assert skew_size_tilt(0.5) == pytest.approx(1.0)

    def test_max_upside_skew_hits_upper_cap(self, monkeypatch):
        _skewflag(monkeypatch, True, cap=0.20)
        assert skew_size_tilt(1.0) == pytest.approx(1.20)

    def test_max_downside_skew_hits_lower_cap(self, monkeypatch):
        _skewflag(monkeypatch, True, cap=0.20)
        assert skew_size_tilt(0.0) == pytest.approx(0.80)

    def test_interior_is_linear(self, monkeypatch):
        _skewflag(monkeypatch, True, cap=0.20)
        # 1 + 2*0.20*(0.75-0.5) = 1 + 0.4*0.25 = 1.10
        assert skew_size_tilt(0.75) == pytest.approx(1.10)

    def test_monotone_increasing_in_p(self, monkeypatch):
        _skewflag(monkeypatch, True, cap=0.20)
        assert skew_size_tilt(0.9) > skew_size_tilt(0.6) > skew_size_tilt(0.4)

    def test_never_exceeds_cap_band(self, monkeypatch):
        # even a percentile fed slightly past the nominal edge stays clamped
        _skewflag(monkeypatch, True, cap=0.10)
        assert 0.90 <= skew_size_tilt(1.0) <= 1.10
        assert 0.90 <= skew_size_tilt(0.0) <= 1.10


class TestFailOpen:
    def test_none_percentile_is_warned_noop(self, monkeypatch, caplog):
        _skewflag(monkeypatch, True, cap=0.20)
        with caplog.at_level(logging.WARNING):
            assert skew_size_tilt(None) == 1.0
        assert any(
            "rr_percentile" in r.message and "no-op" in r.message
            for r in caplog.records
            if r.levelno == logging.WARNING
        )

    def test_out_of_range_percentile_fails_open(self, monkeypatch):
        _skewflag(monkeypatch, True, cap=0.20)
        assert skew_size_tilt(1.5) == 1.0
        assert skew_size_tilt(-0.2) == 1.0

    def test_nan_percentile_fails_open(self, monkeypatch):
        _skewflag(monkeypatch, True, cap=0.20)
        assert skew_size_tilt(float("nan")) == 1.0

    def test_bool_percentile_fails_open(self, monkeypatch):
        # bool is an int subclass — must not be read as p=1.0/0.0
        _skewflag(monkeypatch, True, cap=0.20)
        assert skew_size_tilt(True) == 1.0
        assert skew_size_tilt(False) == 1.0

    def test_silent_when_log_missing_false(self, monkeypatch, caplog):
        _skewflag(monkeypatch, True, cap=0.20)
        with caplog.at_level(logging.WARNING):
            assert skew_size_tilt(None, log_missing=False) == 1.0
        assert not [r for r in caplog.records if r.levelno == logging.WARNING]


# --- sizer + MiFID II audit integration -----------------------------------

from unittest.mock import patch  # noqa: E402

from core.risk_manager import RiskManager  # noqa: E402

_PRICE = 100.0


@pytest.fixture(autouse=True)
def reset_kill_switch_fixture():
    from core.kill_switch import kill_switch

    kill_switch.reset()
    kill_switch.redis_client = None
    kill_switch._initialized = True
    yield
    kill_switch.reset()


def _rm(capital: float = 20_000.0) -> RiskManager:
    with patch("core.risk_manager.CLOUD_LOGGING_AVAILABLE", False):
        return RiskManager(None, capital)


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


def _volflag(monkeypatch, enabled: bool) -> None:
    import config

    monkeypatch.setattr(config, "VOL_TARGETING_SIZING_ENABLED", enabled, raising=False)


class TestSizerTilt:
    def test_tilt_scales_size_when_on(self, monkeypatch):
        """Vol OFF, skew ON: the tilt is the ONLY size factor — p=1.0/cap0.2 => 1.2x."""
        _volflag(monkeypatch, False)
        _skewflag(monkeypatch, True, cap=0.20)
        rm = _rm()
        baseline = _size(rm, rr_percentile=0.5)  # neutral -> 1.0
        up = _size(rm, rr_percentile=1.0)
        assert up == pytest.approx(baseline * 1.20, rel=1e-6)

    def test_downside_skew_sizes_down(self, monkeypatch):
        _volflag(monkeypatch, False)
        _skewflag(monkeypatch, True, cap=0.20)
        rm = _rm()
        baseline = _size(rm, rr_percentile=0.5)
        down = _size(rm, rr_percentile=0.0)
        assert down == pytest.approx(baseline * 0.80, rel=1e-6)

    def test_flag_off_ignores_percentile(self, monkeypatch):
        """BORA: skew flag OFF => a supplied rr_percentile changes NOTHING."""
        _volflag(monkeypatch, False)
        _skewflag(monkeypatch, False)
        rm = _rm()
        assert _size(rm, rr_percentile=1.0) == _size(rm)

    def test_default_signature_unchanged(self, monkeypatch):
        """Callers that never pass rr_percentile are untouched in either flag state."""
        _volflag(monkeypatch, False)
        _skewflag(monkeypatch, False)
        off = _size(_rm())
        _skewflag(monkeypatch, True, cap=0.20)
        on = _size(_rm())
        assert off == on  # no percentile passed -> None -> fail-open 1.0

    def test_missing_percentile_fail_open(self, monkeypatch):
        _volflag(monkeypatch, False)
        _skewflag(monkeypatch, True, cap=0.20)
        rm = _rm()
        assert _size(rm, rr_percentile=None) == _size(rm)


class TestAuditFoldCombined:
    def test_combined_vol_times_skew_is_logged(self, monkeypatch):
        """MiFID II Art. 17: risk_size_scaler = vol * skew (applied == logged)."""
        from core.cloud_logger import DecisionContext
        from core.risk_manager import apply_vol_targeting_audit

        _volflag(monkeypatch, True)
        _skewflag(monkeypatch, True, cap=0.20)
        ctx = DecisionContext(symbol="TEST")
        # vol: target 0.015 / fv 0.025 = 0.6 ; skew: p=1.0/cap0.2 = 1.2 ; 0.6*1.2 = 0.72
        scaler = apply_vol_targeting_audit(ctx, 0.025, 1.0)
        assert scaler == pytest.approx(0.72)
        assert ctx.risk_size_scaler == pytest.approx(0.72)

    def test_skew_only_logs_tilt(self, monkeypatch):
        """Vol OFF, skew ON: audit records the tilt alone (vol scaler == 1.0)."""
        from core.cloud_logger import DecisionContext
        from core.risk_manager import apply_vol_targeting_audit

        _volflag(monkeypatch, False)
        _skewflag(monkeypatch, True, cap=0.20)
        ctx = DecisionContext(symbol="TEST")
        assert apply_vol_targeting_audit(ctx, None, 1.0) == pytest.approx(1.20)
        assert ctx.risk_size_scaler == pytest.approx(1.20)

    def test_both_off_leaves_context_untouched(self, monkeypatch):
        """Dark default: no vol, no skew => scaler 1.0, context pristine (byte-identical)."""
        from core.cloud_logger import DecisionContext
        from core.risk_manager import apply_vol_targeting_audit

        _volflag(monkeypatch, False)
        _skewflag(monkeypatch, False)
        ctx = DecisionContext(symbol="TEST")
        assert apply_vol_targeting_audit(ctx, 0.025, 1.0) == 1.0
        assert ctx.risk_size_scaler == 1.0

    def test_applied_equals_logged(self, monkeypatch):
        """The sizer's applied factor equals the audited scaler (pure helper, no drift)."""
        _volflag(monkeypatch, False)
        _skewflag(monkeypatch, True, cap=0.15)
        rm = _rm()
        baseline = _size(rm, rr_percentile=0.5)
        applied = _size(rm, rr_percentile=0.8) / baseline
        logged = skew_size_tilt(0.8)  # same pure helper the audit uses
        assert applied == pytest.approx(logged, rel=1e-6)
