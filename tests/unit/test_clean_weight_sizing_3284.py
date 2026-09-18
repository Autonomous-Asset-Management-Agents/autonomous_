# tests/unit/test_clean_weight_sizing_3284.py
"""#3284 (Epic #3086) — Clean-weight sizing, Variant 2 (PER-SYMBOL).

CLEAN_WEIGHT_SIZING:
  "off" -> byte-identical conviction path (dark-ship default)
  "a"   -> strict 1/N (equal-DOLLAR, DeMiguel baseline)
  "b"   -> 1/N x vol-targeting x active switchable tilts (equal-RISK-ish)

Conviction LEAVES the SIZE lever (stays direction/admission; Grinold-Kahn:
signal once). final_risk_scaler (vix/confidence/size/reduction) is NOT applied
in a/b. N binds to effective_max_positions() (console-configurable 10-20). The
hard MAX cap still binds; there is deliberately NO MIN floor after the vol-tilt
(it would neuter vol-targeting at N=20 where 1/N == MIN). Cash never binds in
these tests (huge account_cash) so the weight math is isolated.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.risk_manager import RiskManager

_PRICE = 100.0
_CAP = 20_000.0


@pytest.fixture(autouse=True)
def reset_kill_switch_fixture():
    from core.kill_switch import kill_switch

    kill_switch.reset()
    kill_switch.redis_client = None
    kill_switch._initialized = True
    yield
    kill_switch.reset()


def _rm(capital: float = _CAP) -> RiskManager:
    # No broker client isolates the sizing math (mirrors test_position_size_vol_targeting).
    with patch("core.risk_manager.CLOUD_LOGGING_AVAILABLE", False):
        return RiskManager(None, capital)


def _cfg_n(n: int) -> SimpleNamespace:
    # effective_max_positions() reads config.get_config(); FULL_UNIVERSE off => MAX_POSITIONS.
    return SimpleNamespace(
        CASH_AWARE_SLOTS_ENABLED=False,
        MAX_POSITIONS=n,
        FULL_UNIVERSE_TRADING_ENABLED=False,
        FULL_UNIVERSE_MAX_POSITIONS=n,
    )


def _set(monkeypatch, *, mode="off", vol_targeting=True) -> None:
    import config

    monkeypatch.setattr(config, "CLEAN_WEIGHT_SIZING", mode, raising=False)
    monkeypatch.setattr(
        config, "VOL_TARGETING_SIZING_ENABLED", vol_targeting, raising=False
    )


def _size(rm: RiskManager, conviction: float = 0.5, N: int = 10, **kw) -> float:
    with patch("config.get_config", return_value=_cfg_n(N)):
        return rm.calculate_position_size(
            stop_loss_atr_multiplier=3.0,
            atr=2.0,
            confidence="medium",
            size_scaler=1.0,
            num_stocks_in_strategy=1,
            current_price=_PRICE,
            account_cash=10_000_000.0,  # huge => cash never binds; isolate the weight
            allow_fractional=True,
            conviction_score=conviction,
            market_data={"vix": 20.0},
            **kw,
        )


class TestByteIdenticalOff:
    def test_off_is_conviction_sensitive(self, monkeypatch):
        """off (default): the untouched conviction path — size grows with conviction."""
        _set(monkeypatch, mode="off")
        rm = _rm()
        assert _size(rm, conviction=0.9) > _size(rm, conviction=0.1)

    def test_unset_uses_default_b(self, monkeypatch):
        """PROMOTED (#3284): the default is now "b" — an unset flag sizes clean-weight,
        so conviction must NOT move the size (it only affects direction/admission)."""
        import config

        monkeypatch.delattr(config, "CLEAN_WEIGHT_SIZING", raising=False)
        rm = _rm()
        assert _size(rm, conviction=0.1, N=10) == _size(rm, conviction=0.9, N=10)


class TestArmAStrictOneOverN:
    def test_a_is_one_over_n_and_conviction_independent(self, monkeypatch):
        _set(monkeypatch, mode="a")
        rm = _rm()
        lo = _size(rm, conviction=0.1, N=10)
        hi = _size(rm, conviction=0.9, N=10)
        assert lo == hi == pytest.approx(_CAP * 0.10 / _PRICE)  # 20 shares

    def test_a_binds_to_effective_max_positions(self, monkeypatch):
        _set(monkeypatch, mode="a")
        rm = _rm()
        assert _size(rm, N=10) == pytest.approx(_CAP * 0.10 / _PRICE)  # 20
        assert _size(rm, N=20) == pytest.approx(_CAP * 0.05 / _PRICE)  # 10

    def test_a_max_cap_binds_for_small_n(self, monkeypatch):
        """N=3 -> 1/3 > MAX_POSITION_PERCENT (0.25) -> hard cap binds."""
        _set(monkeypatch, mode="a")
        rm = _rm()
        assert _size(rm, N=3) == pytest.approx(_CAP * 0.25 / _PRICE)  # 50

    def test_a_ignores_vol(self, monkeypatch):
        _set(monkeypatch, mode="a")
        rm = _rm()
        assert _size(rm, N=10, forecast_vol=0.0075) == _size(
            rm, N=10, forecast_vol=0.03
        )

    def test_a_differs_from_off(self, monkeypatch):
        rm = _rm()
        _set(monkeypatch, mode="off")
        off = _size(rm, conviction=0.9, N=10)
        _set(monkeypatch, mode="a")
        assert _size(rm, conviction=0.9, N=10) != off


class TestArmBVolTilt:
    def test_b_calm_up_wild_down(self, monkeypatch):
        _set(monkeypatch, mode="b")
        rm = _rm()
        calm = _size(rm, N=10, forecast_vol=0.0075)  # clip(0.015/0.0075)=2.0 -> 1.5
        wild = _size(rm, N=10, forecast_vol=0.03)  # clip(0.015/0.03)=0.5
        assert calm == pytest.approx(_CAP * 0.10 * 1.5 / _PRICE)  # 30
        assert wild == pytest.approx(_CAP * 0.10 * 0.5 / _PRICE)  # 10
        assert calm > wild

    def test_b_conviction_does_not_affect_size(self, monkeypatch):
        _set(monkeypatch, mode="b")
        rm = _rm()
        a = _size(rm, conviction=0.1, N=10, forecast_vol=0.015)  # vol 1.0
        b = _size(rm, conviction=0.9, N=10, forecast_vol=0.015)
        assert a == b == pytest.approx(_CAP * 0.10 / _PRICE)  # 20

    def test_b_max_cap_binds(self, monkeypatch):
        """N=5 -> 1/5=0.20, x1.5 vol = 0.30 > MAX 0.25 -> capped."""
        _set(monkeypatch, mode="b")
        rm = _rm()
        assert _size(rm, N=5, forecast_vol=0.0075) == pytest.approx(
            _CAP * 0.25 / _PRICE
        )  # 50

    def test_b_vol_tilt_may_derisk_below_min_no_floor(self, monkeypatch):
        """N=20 (1/N=5%=MIN): a wild name (vol 0.5x) sizes to 2.5% — below MIN by
        design (vol de-risking), NOT floored back up to MIN."""
        _set(monkeypatch, mode="b")
        rm = _rm()
        assert _size(rm, N=20, forecast_vol=0.03) == pytest.approx(
            _CAP * 0.05 * 0.5 / _PRICE
        )  # 5 shares (2.5%)


class TestSwitchableTiltIsLive:
    def test_b_skew_switch_changes_order_value(self, monkeypatch):
        """Regulatory invariant: flipping SKEW_SIZE_TILT_ENABLED must change the
        executed order value — a configurable tilt is never a dead control."""
        import config

        _set(monkeypatch, mode="b")
        rm = _rm()
        monkeypatch.setattr(config, "SKEW_SIZE_TILT_ENABLED", False, raising=False)
        off = _size(rm, N=10, forecast_vol=0.015, rr_percentile=0.95)
        monkeypatch.setattr(config, "SKEW_SIZE_TILT_ENABLED", True, raising=False)
        monkeypatch.setattr(config, "SKEW_SIZE_TILT_CAP", 0.2, raising=False)
        on = _size(rm, N=10, forecast_vol=0.015, rr_percentile=0.95)
        assert on != off


class TestCashFollowsTargetOneOverN:
    """The cash coupling normalises against the EQUAL slot (1/N), not the 30% conviction
    scale — else clean targets (<=15%) claim <=0.5 of their slot and the book under-invests
    (the 2-day-probe finding: arm b filled ~$45k of $100k). No-leverage holds (each <= slot).
    """

    _CASH = 100_000.0
    _SLOTS = 10  # num_stocks_in_strategy -> the divisor; matches N=10

    def _sz(self, rm, N=10, **kw):
        from unittest.mock import patch

        with patch("config.get_config", return_value=_cfg_n(N)):
            return rm.calculate_position_size(
                stop_loss_atr_multiplier=3.0,
                atr=2.0,
                confidence="medium",
                size_scaler=1.0,
                num_stocks_in_strategy=self._SLOTS,
                current_price=_PRICE,
                account_cash=self._CASH,
                allow_fractional=True,
                conviction_score=0.5,
                market_data={"vix": 20.0},
                **kw,
            )

    def _slot_shares(self):
        # ((cash - buffer)/slots)/price — the full equal slot in shares.
        return ((self._CASH - 1.0) / self._SLOTS) / _PRICE

    def test_arm_a_fills_the_full_equal_slot(self, monkeypatch):
        _set(monkeypatch, mode="a")
        got = self._sz(_rm(self._CASH))
        assert got == pytest.approx(
            self._slot_shares(), rel=1e-3
        )  # demand_fraction == 1.0

    def test_arm_b_volatile_derisks_below_slot(self, monkeypatch):
        _set(monkeypatch, mode="b")
        # vol 0.5 (fv=0.03) -> w=0.05 -> demand_fraction 0.5 -> half the slot.
        got = self._sz(_rm(self._CASH), forecast_vol=0.03)
        assert got == pytest.approx(self._slot_shares() * 0.5, rel=1e-3)

    def test_arm_b_calm_capped_at_slot_no_up_tilt(self, monkeypatch):
        _set(monkeypatch, mode="b")
        # vol 1.5 (fv=0.0075) -> w=0.15 -> demand_fraction clamps to 1.0: fills the slot,
        # never exceeds it (per-symbol up-tilt needs the concurrent set — Phase 1b).
        got = self._sz(_rm(self._CASH), forecast_vol=0.0075)
        assert got == pytest.approx(self._slot_shares(), rel=1e-3)


class TestConfigFlagPresent:
    def test_flag_default_b(self):
        # PROMOTED (#3284, Owner-Waiver 2026-09-09): clean-weight b is the default sizing.
        import config

        assert str(getattr(config, "CLEAN_WEIGHT_SIZING")).lower() == "b"

    def test_registered_as_operator_setting(self):
        # #3284: the sizing procedure is an operator-facing, WORM-audited Trading-Setting —
        # a live enum, not a hidden constant. Default matches config ("b").
        from core.trading_settings import REGISTRY

        s = REGISTRY.get("CLEAN_WEIGHT_SIZING")
        assert s is not None and s.kind == "enum"
        assert set(s.choices) == {"off", "a", "b"}
        assert str(s.default) == "b"
