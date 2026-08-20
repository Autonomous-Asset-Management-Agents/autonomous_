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
            market_data={"vix": 20.0},
        )


def test_funded_200_account_can_buy():
    """€600 funded, empty book → a real fractional order, not a floor-zeroed one."""
    size = _size(total_capital=600.0, cash=600.0, price=220.0)
    assert size > 0.0, "funded €600 account must be able to place a fractional BUY"
    # ~(600-1)/10 slots ≈ $60. This pins the CASH-CONSTRAINT slot size, not a $50 floor —
    # there is none; the only floor is Alpaca's $1 (#2784 rev. of #2714).
    assert size * 220.0 >= 50.0, "order must be the slot size, not clamped to a floor"


def test_tiny_55_account_can_buy_at_default_floor():
    """#2784 — requirement (a): a $55 account trades on the DEFAULT config, no override.

    Under the #2721 $50 default this was filtered to 0 ((55-1)/10 = $5.40 < $50), i.e.
    the whole book of a small customer was blocked. The override this test used to need
    is exactly the defect; asserting the default is what pins the product decision.
    """
    size = _size(total_capital=55.0, cash=55.0, price=100.0)
    assert size > 0.0, "a $55 account must be able to trade on default config"
    assert size * 100.0 >= 1.0, "and its order must clear Alpaca's $1 minimum"


def test_below_one_dollar_still_blocked():
    """Sub-floor orders resolve to 0."""
    size = _size(total_capital=6.0, cash=6.0, price=100.0)
    assert size == 0.0, "orders below minimum floor must still be rejected"


def test_large_account_places_normal_order_not_dust():
    """A $100k account still sizes a normal large order."""
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
    """MIN_ORDER_VALUE_USD and RISK_CASH_BUFFER_USD are both $1 and match across editions.

    #2784 reverts the #2714 raise to $50: it blocked every account under ~$1,000 while
    suppressing no exit dust (its only exit-side consumer, the trim lever, is default-OFF).
    Both floors are Alpaca's $1 technical minimum — BORA parity is the point of this test.
    """
    import config as ent

    oss = _load_oss_config()
    ent_cfg = ent.get_config()
    assert oss.MIN_ORDER_VALUE_USD == ent_cfg.MIN_ORDER_VALUE_USD == 1.0
    assert oss.RISK_CASH_BUFFER_USD == ent_cfg.RISK_CASH_BUFFER_USD == 1.0
