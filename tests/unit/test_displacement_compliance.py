# tests/unit/test_displacement_compliance.py
"""#2712 Inc 1 (Epic #1957, P0) — displacement SELL + recovery BUY become VISIBLE to compliance.

Order forensics: XOM/PM identical-qty sell+buy in the same minute — both legs submitted with
zero guard calls, so the 60s wash-trade window (built exactly for this) saw neither side.
Inc 1 is observe-only: every displacement/recovery leg is passed through check_order (result
LOGGED, never blocking — the recovery is a deliberate rollback and must not strand) and
record_trade (so the wash buffer finally contains both sides). Fail-open on guardian errors.
"""

from unittest.mock import MagicMock

from core.engine.order_executor import register_displacement_leg


def test_leg_is_recorded_and_checked_with_full_identity():
    g = MagicMock()
    g.check_order.return_value = True
    register_displacement_leg(
        g,
        symbol="XOM",
        side="sell",
        qty=0.4429,
        price=152.94,
        user_id="global",
        held_qty=0.4429,
        kind="displacement_sell",
    )
    order = g.check_order.call_args.args[0]
    assert order["symbol"] == "XOM" and order["side"] == "sell"
    assert order["user_id"] == "global"  # keying fix — wash buffer must cross-match
    assert order["held_qty"] == 0.4429
    g.record_trade.assert_called_once()


def test_wash_hit_is_logged_but_never_blocks(caplog):
    g = MagicMock()
    g.check_order.return_value = False  # wash window fired
    with caplog.at_level("WARNING"):
        ok = register_displacement_leg(
            g,
            symbol="PM",
            side="buy",
            qty=0.3067,
            price=189.01,
            user_id="global",
            held_qty=0.0,
            kind="displacement_recovery_buy",
        )
    assert ok is False  # surfaced to the caller for logging/telemetry
    assert any("displacement" in m.lower() for m in caplog.messages)
    g.record_trade.assert_called_once()  # still recorded — visibility is the point


def test_guardian_errors_fail_open():
    g = MagicMock()
    g.check_order.side_effect = RuntimeError("boom")
    ok = register_displacement_leg(
        g,
        symbol="IQV",
        side="sell",
        qty=0.07,
        price=230.4,
        user_id="global",
        held_qty=0.07,
        kind="displacement_sell",
    )
    assert ok is True  # never raise into the order path


def test_none_guardian_is_noop():
    assert (
        register_displacement_leg(
            None,
            symbol="X",
            side="sell",
            qty=1,
            price=1,
            user_id="global",
            held_qty=1,
            kind="displacement_sell",
        )
        is True
    )
