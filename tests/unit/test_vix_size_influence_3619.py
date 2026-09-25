"""#3619 — the size lever reaches the order, and the VIX influence on size is settable.

Measured before this change (installed app, fallback decision logs <-> Alpaca fills,
11.-23.09.2026): in clean-weight mode "b" the FIRST tranche equals the vol-scaled target
exactly (FCX target 3,987 $ -> fill 3,988 $), but the build does not stop there — the
top-up dead-band still used the CONVICTION target (5-30 %), so ABNB (target 6,020 $) was
bought up to ~20,000 $ in five tranches. Part 1 makes the dead-band see the same target the
sizer uses and records WHICH cap bound in the decision record. Part 2 adds
``VIX_SIZE_INFLUENCE`` (0..1, ships 1.0 = byte-identical) blending the vol scaler.
"""

import inspect
import os
import re
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import config as _config
from core.portfolio_manager import OpportunityScore, PortfolioManager, PositionScore
from core.trading_settings import REGISTRY
from core.trading_settings import agents_view as _agents_view

pytestmark = pytest.mark.vc2  # Stufen-Marker (#3396): Positionsgroesse = VC-2

HERE = os.path.dirname(inspect.getfile(_config))
CAPITAL = 100_000.0


# --- Part 1a: the top-up dead-band uses the clean-weight (vol-scaled) target ----------


def _pm():
    pm = PortfolioManager(client=MagicMock(), total_capital=CAPITAL)
    pm.refresh_positions = lambda: None
    pm._last_refresh_ok = True
    pm._conviction_ewma_enabled = False
    pm._topup_dead_band_pct = 5.0
    return pm


def _hold(pm, pct):
    value = CAPITAL * pct / 100.0
    pm._position_scores["X"] = PositionScore(
        symbol="X",
        qty=value / 100.0,
        avg_entry=100.0,
        current_price=100.0,
        market_value=value,
        unrealized_pnl=0.0,
        unrealized_pnl_pct=0.0,
    )


def _opp(forecast_vol=None, conf=1.0):
    return OpportunityScore(
        symbol="X",
        current_price=100.0,
        model_confidence=conf,
        total_score=80.0,
        forecast_vol=forecast_vol,
    )


def _cfg(monkeypatch, **over):
    cfg = _config.get_config()
    base = {
        "CLEAN_WEIGHT_SIZING": "b",
        "VOL_TARGETING_SIZING_ENABLED": True,
        "VOL_TARGET_DAILY_VOL": 0.015,
        "VOL_SIZE_SCALER_LO": 0.5,
        "VOL_SIZE_SCALER_HI": 1.5,
        "VIX_SIZE_INFLUENCE": 1.0,
        "MAX_POSITION_PERCENT": 0.25,
    }
    base.update(over)
    for k, v in base.items():
        monkeypatch.setattr(cfg, k, v, raising=False)
        monkeypatch.setattr(_config, k, v, raising=False)


@pytest.fixture(autouse=True)
def _twenty_slots(monkeypatch):
    monkeypatch.setattr("core.risk_manager.effective_max_positions", lambda: 20)
    with patch("core.engine.regime_signal.active_throttle_factor", return_value=1.0):
        yield


def test_volatile_name_stops_building_at_the_vol_scaled_target(monkeypatch):
    """forecast_vol 0.030 -> scaler 0.5 -> target 1/20 * 0.5 = 2.5 %; a 0.5 % holding
    is inside the 5 % band -> no top-up. (Old behaviour: conviction target 25 % -> keep
    buying.)"""
    _cfg(monkeypatch)
    pm = _pm()
    _hold(pm, 0.5)
    reason = pm._topup_dead_band_reason(_opp(forecast_vol=0.030))
    assert reason is not None
    assert "2.5%" in reason and "clean-weight" in reason


def test_calm_name_target_is_scaled_up_but_the_band_still_ends_the_build(monkeypatch):
    """forecast_vol 0.010 -> scaler 1.5 -> target 7.5 %: held 1 % -> top-up allowed
    (6.5 >= 5); held 3 % -> blocked (4.5 < 5)."""
    _cfg(monkeypatch)
    pm = _pm()
    _hold(pm, 1.0)
    assert pm._topup_dead_band_reason(_opp(forecast_vol=0.010)) is None
    _hold(pm, 3.0)
    assert pm._topup_dead_band_reason(_opp(forecast_vol=0.010)) is not None


def test_missing_forecast_means_plain_1_over_n(monkeypatch):
    _cfg(monkeypatch)
    pm = _pm()
    _hold(pm, 0.5)
    reason = pm._topup_dead_band_reason(_opp(forecast_vol=None))
    assert reason is not None and "5.0%" in reason


def test_conviction_mode_keeps_the_conviction_target(monkeypatch):
    _cfg(monkeypatch, CLEAN_WEIGHT_SIZING="off")
    pm = _pm()
    _hold(pm, 0.5)
    assert pm._topup_dead_band_reason(_opp(forecast_vol=0.030)) is None


def test_regime_factor_still_lowers_the_clean_target(monkeypatch):
    _cfg(monkeypatch)
    pm = _pm()
    _hold(pm, 1.0)  # target 7.5 % calm -> allowed; x0.5 -> 3.75 -> blocked
    with patch("core.engine.regime_signal.active_throttle_factor", return_value=0.5):
        reason = pm._topup_dead_band_reason(_opp(forecast_vol=0.010))
    assert reason is not None and "regime" in reason


def test_both_order_paths_hand_the_forecast_to_the_opportunity():
    import core.engine.order_executor as oe

    src = inspect.getsource(oe)
    calls = []
    for m in re.finditer(r"score_opportunity\(", src):
        depth, i = 0, m.end() - 1
        while i < len(src):  # paren-balanced call body
            depth += {"(": 1, ")": -1}.get(src[i], 0)
            if depth == 0:
                break
            i += 1
        calls.append(src[m.start() : i + 1])
    assert len(calls) >= 2
    for call in calls:
        assert "forecast_vol=" in call, call[:200]
    assert (
        "forecast_vol"
        in inspect.signature(PortfolioManager.score_opportunity).parameters
    )


# --- Part 1b: the binding cap is in the decision record --------------------------------


def _rm(capital):
    from datetime import datetime, timezone

    from core.risk_manager import RiskManager

    clock = MagicMock()
    clock.now.return_value = datetime(2026, 9, 24, 14, 0, tzinfo=timezone.utc)
    return RiskManager(client=None, total_capital=capital, clock=clock)


def _size(rm, *, cash, slots, forecast_vol, trace):
    return rm.calculate_position_size(
        stop_loss_atr_multiplier=3.0,
        atr=1.0,
        confidence="high",
        size_scaler=1.0,
        market_data={"vix": 20.0},
        num_stocks_in_strategy=slots,
        current_price=100.0,
        account_cash=cash,
        allow_fractional=True,
        conviction_score=0.75,
        forecast_vol=forecast_vol,
        sizing_trace=trace,
    )


def test_cash_cap_records_its_dollar_value(monkeypatch):
    _cfg(monkeypatch)
    rm = _rm(144_485.0)
    trace = {}
    shares = _size(rm, cash=2_001.0, slots=1, forecast_vol=None, trace=trace)
    assert trace["binding_limit"] == "cash"
    assert float(trace["binding_cap_value"]) == pytest.approx(shares * 100.0, rel=1e-6)


def test_compliance_cap_records_its_dollar_value(monkeypatch):
    _cfg(monkeypatch, MAX_POSITION_PERCENT=0.25)
    rm = _rm(400_000.0)  # 1/20 * 400k = 20k > 10k order cap
    trace = {}
    shares = _size(rm, cash=400_000.0, slots=1, forecast_vol=None, trace=trace)
    assert trace["binding_limit"] == "compliance_order_value"
    assert float(trace["binding_cap_value"]) == pytest.approx(shares * 100.0, rel=1e-6)


def test_target_sets_the_size_when_nothing_binds_and_scales_with_vol(monkeypatch):
    _cfg(monkeypatch)
    rm = _rm(144_485.0)
    t1, t2 = {}, {}
    calm = _size(rm, cash=78_769.0, slots=8, forecast_vol=0.01364, trace=t1) * 100
    volatile = _size(rm, cash=78_769.0, slots=8, forecast_vol=0.030, trace=t2) * 100
    assert "binding_limit" not in t1 and "binding_limit" not in t2
    assert volatile == pytest.approx(144_485.0 * 0.05 * 0.5, rel=1e-3)
    assert calm / volatile == pytest.approx(1.1 / 0.5, rel=1e-2)


def test_audit_mirror_carries_binding_limit_and_value():
    from core.risk_manager import apply_sizing_mode_audit

    ctx = SimpleNamespace()
    apply_sizing_mode_audit(
        ctx,
        {
            "sizing_mode": "b",
            "sizing_target_weight": 0.05,
            "binding_limit": "cash",
            "binding_cap_value": "1234.5",
        },
    )
    assert ctx.sizing_binding_limit == "cash"
    assert ctx.sizing_cap_value == pytest.approx(1234.5)
    from core.cloud_logger import DecisionContext
    from core.database.models import Decision

    assert DecisionContext().sizing_binding_limit is None
    assert DecisionContext().sizing_cap_value is None
    assert "sizing_binding_limit" in Decision.__table__.columns
    assert "sizing_cap_value" in Decision.__table__.columns
    assert os.path.exists(
        os.path.join(
            HERE, "alembic", "versions", "0016_add_decisions_sizing_binding.py"
        )
    )


# --- Part 2: VIX_SIZE_INFLUENCE ----------------------------------------------------------


def test_registry_and_settings_carry_the_influence():
    s = REGISTRY["VIX_SIZE_INFLUENCE"]
    assert (s.default, s.lo, s.hi) == (1.0, 0.0, 1.0)
    assert float(_config.get_config().VIX_SIZE_INFLUENCE) == 1.0
    assert (
        "VIX_RISK_WEIGHT" in REGISTRY
    )  # stays: the consensus weight of the IV-off path


@pytest.mark.parametrize(
    "influence, expected",
    [(1.0, 0.5), (0.5, 0.75), (0.0, 1.0), (0.25, 0.875)],
)
def test_influence_blends_the_vol_scaler(monkeypatch, influence, expected):
    from core.risk_manager import vol_targeting_scaler

    _cfg(monkeypatch, VIX_SIZE_INFLUENCE=influence)
    assert vol_targeting_scaler(0.030) == pytest.approx(expected)


def test_applied_equals_logged(monkeypatch):
    from core.risk_manager import apply_vol_targeting_audit

    _cfg(monkeypatch, VIX_SIZE_INFLUENCE=0.5)
    ctx = SimpleNamespace(risk_size_scaler=1.0)
    apply_vol_targeting_audit(ctx, 0.030)
    assert ctx.risk_size_scaler == pytest.approx(0.75)


def test_influence_zero_gives_every_name_the_same_target(monkeypatch):
    _cfg(monkeypatch, VIX_SIZE_INFLUENCE=0.0)
    rm = _rm(144_485.0)
    a = _size(rm, cash=78_769.0, slots=8, forecast_vol=0.030, trace={})
    b = _size(rm, cash=78_769.0, slots=8, forecast_vol=0.010, trace={})
    assert a == pytest.approx(b)
    assert a * 100 == pytest.approx(144_485.0 * 0.05, rel=1e-3)


def test_never_a_direction_effect():
    root = os.path.join(HERE, "core", "round_table")
    for f in os.listdir(root):
        if f.endswith(".py"):
            src = open(os.path.join(root, f), encoding="utf-8").read()
            assert "VIX_SIZE_INFLUENCE" not in src, f


def test_agents_view_offers_the_influence_while_iv_drives_size(monkeypatch):
    _cfg(monkeypatch, IMPLIED_VOL_FORECAST_ENABLED=True, VIX_SIZE_INFLUENCE=1.0)
    vix = next(a for a in _agents_view()["agents"] if a["name"] == "VIXAwareRiskAgent")
    assert vix["role"] == "size_input"
    assert vix["weight_key"] == "VIX_SIZE_INFLUENCE"
    assert (vix["min_weight"], vix["max_weight"], vix["default_weight"]) == (
        0.0,
        1.0,
        1.0,
    )
    assert vix["weight"] == pytest.approx(1.0)


def test_agents_view_keeps_the_consensus_weight_when_iv_is_off(monkeypatch):
    _cfg(monkeypatch, IMPLIED_VOL_FORECAST_ENABLED=False)
    vix = next(a for a in _agents_view()["agents"] if a["name"] == "VIXAwareRiskAgent")
    assert vix["role"] == "mean_voter"
    assert vix["weight_key"] == "VIX_RISK_WEIGHT"
    assert vix["default_weight"] == pytest.approx(0.45)


def test_desktop_passthrough_carries_the_new_key():
    cjs = open(
        os.path.join(HERE, "..", "desktop", "electron", "setup-manager.cjs"),
        encoding="utf-8",
    ).read()
    assert cjs.count('"VIX_SIZE_INFLUENCE"') == 2
