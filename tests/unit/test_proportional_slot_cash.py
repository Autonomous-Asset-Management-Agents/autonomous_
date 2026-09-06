"""#3135 — proportional slot-cash split.

The cash constraint in ``calculate_position_size`` splits free cash into ``slots`` EQUAL  # noqa: E501
parts (``(cash - buffer) / slots``), so every concurrent BUY of a cycle is capped at the  # noqa: E501
SAME dollar amount. That flattens the per-name IV/conviction size differentiation produced  # noqa: E501
upstream by #3094 (VIXAware-IV -> forecast_vol -> vol_targeting_scaler) and the conviction  # noqa: E501
sizer — empirically observed on the live install (8 buys, risk_size_scaler 0.50-1.089, all  # noqa: E501
filled at ~$1,005). Flag ``PROPORTIONAL_SLOT_CASH_ENABLED`` (default OFF, byte-identical)  # noqa: E501
scales each slot by this position's own target value so calmer / higher-conviction names  # noqa: E501
get a bigger slice — but ONLY ever SHRINKS vs the equal split, so the no-leverage safety  # noqa: E501
axiom (sum of concurrent budgets <= cash) is preserved.
"""

from unittest.mock import patch

import pytest

from core.risk_manager import RiskManager

pytestmark = pytest.mark.iron_dome


def _rm():
    # client=None → the optional broker-backed total-exposure cap (§5b) is skipped, so this  # noqa: E501
    # isolates the cash-slot sizing math. total_capital drives the target-value reference.  # noqa: E501
    return RiskManager(client=None, total_capital=100_000)


_COMMON = dict(
    stop_loss_atr_multiplier=3.0,
    atr=5.0,
    confidence="medium",  # confidence_scaler = 1.0
    market_data={"vix": 15.0},  # VIX <= 18 → vix_risk_scaler = 1.0
    num_stocks_in_strategy=10,  # slots = 10 → (cash-buffer)/slots ≈ $999.9
    current_price=100.0,
    account_cash=10_000.0,
    allow_fractional=True,
    forecast_vol=None,  # vol_targeting no-op → vol_scaler = 1.0
)


def _shares(rm, **over):
    kw = dict(_COMMON)
    kw.update(over)
    return rm.calculate_position_size(**kw)


def test_flag_on_conviction_differentiates_slot_cash():
    """ON → higher conviction gets a bigger cash slice (today: both clamp equal → RED)."""  # noqa: E501
    rm = _rm()
    with patch("config.PROPORTIONAL_SLOT_CASH_ENABLED", True, create=True):
        hi = _shares(rm, conviction_score=0.90)
        lo = _shares(rm, conviction_score=0.55)
    assert (
        hi > lo
    ), f"expected higher conviction to get more cash, got hi={hi} lo={lo}"  # noqa: E501


def test_flag_off_is_equal_split_byte_identical():
    """OFF → every concurrent BUY capped at the SAME (cash-buffer)/slots (today's behaviour)."""  # noqa: E501
    rm = _rm()
    with patch("config.PROPORTIONAL_SLOT_CASH_ENABLED", False, create=True):
        hi = _shares(rm, conviction_score=0.90)
        lo = _shares(rm, conviction_score=0.55)
    assert hi == lo


def test_on_never_exceeds_equal_split_no_leverage():
    """SAFETY: the proportional cap can only SHRINK vs the equal split → no leverage."""  # noqa: E501
    rm = _rm()
    with patch("config.PROPORTIONAL_SLOT_CASH_ENABLED", False, create=True):
        equal = _shares(rm, conviction_score=0.90)
    with patch("config.PROPORTIONAL_SLOT_CASH_ENABLED", True, create=True):
        prop = _shares(rm, conviction_score=0.90)
    assert prop <= equal + 1e-9


def test_iv_forecast_vol_differentiates_slot_cash():
    """ON + vol-targeting → calmer (lower IV/forecast_vol) name gets a bigger slice than a  # noqa: E501
    volatile one at the SAME conviction (the #3094 IV signal now survives the cash cap).  # noqa: E501
    """
    rm = _rm()
    with patch(
        "config.PROPORTIONAL_SLOT_CASH_ENABLED", True, create=True
    ), patch(  # noqa: E501
        "config.VOL_TARGETING_SIZING_ENABLED", True, create=True
    ):
        calm = _shares(
            rm, conviction_score=0.70, forecast_vol=0.010
        )  # low IV → scaler up
        volatile = _shares(
            rm, conviction_score=0.70, forecast_vol=0.060
        )  # high IV → down
    assert calm > volatile


def test_dynamic_sizing_off_falls_back_to_equal():
    """When conviction sizing is off (_sizing_target_value is None), demand_fraction = 1.0  # noqa: E501
    → equal split (no proportional scaling to base it on)."""
    rm = _rm()
    with patch(
        "config.PROPORTIONAL_SLOT_CASH_ENABLED", True, create=True
    ), patch(  # noqa: E501
        "config.ENABLE_DYNAMIC_SIZING", False, create=True
    ):
        hi = _shares(rm, conviction_score=0.90)
        lo = _shares(rm, conviction_score=0.55)
    assert hi == lo
