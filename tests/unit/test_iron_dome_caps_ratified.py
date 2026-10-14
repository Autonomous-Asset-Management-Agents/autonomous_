# ADR-SEC-06 (#1599): lock in the ratified Iron Dome hard-floor caps. Each value is a
# Compliance/Risk risk-appetite decision — changing any of them requires a NEW ratification,
# so this regression test makes a silent change fail CI and forces a re-ratification.

from core.governance.iron_dome_policy import (
    DAILY_DRAWDOWN_PCT_CEILING,
    MAX_DAILY_TRADES_CEILING,
    MAX_ORDER_VALUE_CEILING,
    PORTFOLIO_STOP_LOSS_PCT_CEILING,
    WASH_TRADE_WINDOW_MIN_SECONDS,
)


def test_ratified_hard_floor_caps():
    # "to set" caps ratified under #1599
    assert MAX_ORDER_VALUE_CEILING == 100_000.0  # ADR-C01
    assert PORTFOLIO_STOP_LOSS_PCT_CEILING == 0.10  # ADR-R07
    assert DAILY_DRAWDOWN_PCT_CEILING == 0.20  # ADR-R01
    # derived caps confirmed under #1599
    assert MAX_DAILY_TRADES_CEILING == 50  # ADR-C04
    assert WASH_TRADE_WINDOW_MIN_SECONDS == 30  # ADR-C03
