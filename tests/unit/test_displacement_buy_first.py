# tests/unit/test_displacement_buy_first.py
"""#2712 Inc 2 (Archon Option A + Auflage 3) — Buy-first displacement with strict cash pre-check.

Sell-before-buy created the loss roundtrips: a failed swap BUY forced the recovery to re-buy
the just-sold position at spread cost (XOM/PM forensics). Buy-first: the old position is sold
ONLY after the new BUY is confirmed. Mandatory cash guard: without the sale proceeds in the
estimate — insufficient cash ⇒ the swap is SKIPPED entirely (never a silent sell-first
fallback, never a liquidation). Flag DISPLACEMENT_BUY_FIRST=True; False = legacy sell-first.
"""

from unittest.mock import MagicMock

from config import get_config
from core.engine.order_executor import (
    displacement_available_bp,
    submit_deferred_displacement_sell,
)


def test_flag_default_on():
    assert bool(get_config().DISPLACEMENT_BUY_FIRST) is True


def test_strict_precheck_ignores_sale_proceeds():
    # buy-first: available BP is the account's BP alone — the old position is NOT sold yet.
    assert (
        displacement_available_bp(
            buy_first=True, buying_power=100.0, sell_value=5000.0, multiplier=2.0
        )
        == 100.0
    )


def test_legacy_estimate_includes_proceeds_for_margin():
    assert (
        displacement_available_bp(
            buy_first=False, buying_power=100.0, sell_value=50.0, multiplier=2.0
        )
        == 200.0
    )
    # cash account: proceeds settle later — estimate stays BP-only (existing behavior)
    assert (
        displacement_available_bp(
            buy_first=False, buying_power=100.0, sell_value=50.0, multiplier=1.0
        )
        == 100.0
    )


def _client_ok():
    c = MagicMock()
    c.submit_order.return_value = MagicMock(id="sell-1")
    return c


def test_deferred_sell_submits_registers_and_records():
    client, pm, guardian = _client_ok(), MagicMock(), MagicMock()
    guardian.check_order.return_value = True
    ok = submit_deferred_displacement_sell(
        client=client,
        pm=pm,
        guardian=guardian,
        user_id="global",
        symbol="PM",
        qty=0.3067,
    )
    assert ok is True
    req = client.submit_order.call_args.args[0]
    assert req.symbol == "PM" and float(req.qty) == 0.3067
    pm.record_trade.assert_called_once_with("PM", "sell")
    assert guardian.check_order.call_args.args[0]["strategy_id"] == (
        "displacement_sell_deferred"
    )


def test_deferred_sell_failure_is_critical_but_never_raises(caplog):
    client, pm = MagicMock(), MagicMock()
    client.submit_order.side_effect = RuntimeError("broker down")
    with caplog.at_level("CRITICAL"):
        ok = submit_deferred_displacement_sell(
            client=client,
            pm=pm,
            guardian=None,
            user_id="global",
            symbol="XOM",
            qty=0.44,
        )
    assert ok is False  # book temporarily over cap - position RETAINED, no loss
    assert any("displacement" in m.lower() for m in caplog.messages)
    pm.record_trade.assert_not_called()
