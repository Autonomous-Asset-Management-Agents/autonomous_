"""Small-account sizing: both order floors reduced to Alpaca's real $1 minimum.

Root cause (LIVE-verified 2026-07-27, real money): a funded €220 account with 0 positions had EVERY
BUY blocked ``blocked:risk`` "position size resolved to 0 (risk/cash)". Two flat floors collide on a
small account:

  * the cash constraint reserves a flat **$50** slippage buffer and splits the rest across the slot
    count: ``((cash - 50) / slots) / price`` → ``(220-50)/10 = $17`` per order;
  * the dust filter (``MIN_ORDER_VALUE_USD``, absent from config → fallback **$50**) then rejects the
    $17 order: ``$17 < $50 → 0``.

Alpaca supports fractional/notional orders at **$0 commission**, so the sub-$50 rationale is void; the
only real floor is Alpaca's **$1** notional minimum (already enforced at risk_manager.py:858-861).
Both flat floors are reduced to $1 (configurable, both editions). Large accounts are economically
unchanged (the 5 % MAX_TOTAL_EXPOSURE cushion, not the $50 buffer, provides slippage headroom).

TDD Red→Green (CLAUDE.md §5.1).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.risk_manager import RiskManager

_ROOT = Path(__file__).resolve().parents[2]


def _size(total_capital, cash, price, *, slots=10, atr_pct=0.03, conviction=0.5):
    """Drive the REAL calculate_position_size for an empty-book BUY (kill switch clear,
    broker positions empty so the exposure cap never binds)."""
    client = MagicMock()
    client.get_all_positions.return_value = []  # empty book → exposure cap non-binding
    rm = RiskManager(client=client, total_capital=total_capital)
    with patch("core.kill_switch.kill_switch") as ks:
        ks.is_halted.return_value = False
        return rm.calculate_position_size(
            stop_loss_atr_multiplier=3.0,
            atr=price * atr_pct,
            confidence="high",
            num_stocks_in_strategy=slots,
            current_price=price,
            account_cash=cash,
            allow_fractional=True,
            conviction_score=conviction,
        )


def test_funded_200_account_can_buy():
    """€220 funded, empty book → a fractional order of notional ≥ $1 (was resolved to 0 by $50 dust)."""
    size = _size(total_capital=220.0, cash=220.0, price=220.0)
    assert size > 0.0, "funded €220 account must be able to place a fractional BUY"
    assert size * 220.0 >= 1.0, "order notional must clear Alpaca's $1 floor"


def test_tiny_55_account_can_buy():
    """$55 account: the flat $50 buffer alone would zero it ((55-50)/10=$0.5). With the $1 buffer it trades."""
    size = _size(total_capital=55.0, cash=55.0, price=100.0)
    assert size > 0.0, "the cash buffer must not swallow a tiny funded account"
    assert size * 100.0 >= 1.0


def test_below_one_dollar_still_blocked():
    """Alpaca's $1 notional floor still holds: a sub-$1 order resolves to 0 (not a $0.50 dust order)."""
    size = _size(total_capital=6.0, cash=6.0, price=100.0)
    assert (
        size == 0.0
    ), "orders below Alpaca's $1 notional minimum must still be rejected"


def test_large_account_places_normal_order_not_dust():
    """A $100k account still sizes a normal large order — the $50→$1 change is economically immaterial."""
    size = _size(total_capital=100_000.0, cash=100_000.0, price=200.0)
    assert size * 200.0 > 1_000.0, "large account must still place a full-size order"


def _load_oss_config():
    spec = importlib.util.spec_from_file_location(
        "config_oss_small_acct", str(_ROOT / "config.oss.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_order_floor_config_parity_both_editions():
    """MIN_ORDER_VALUE_USD and RISK_CASH_BUFFER_USD exist, equal across editions, and == 1.0 (Alpaca min)."""
    import config as ent

    oss = _load_oss_config()
    ent_cfg = ent.get_config()
    for name in ("MIN_ORDER_VALUE_USD", "RISK_CASH_BUFFER_USD"):
        oss_val = getattr(oss, name)
        ent_val = getattr(ent_cfg, name)
        assert oss_val == ent_val == 1.0, (
            f"{name} must be 1.0 in both editions (Alpaca minimum): "
            f"oss={oss_val!r} enterprise={ent_val!r}"
        )
