# tests/unit/test_guard_knobs_hardening.py
"""#2714 (Epic #1957) — arm the dead knobs (guard audit 09.08).

1) REBALANCE_COOLDOWN_HOURS was set but never read (hard-coded 0.5h); 2) the #2554
stop-out re-entry lockout shipped disabled (default 0); 3) MIN_ORDER_VALUE_USD=$1 let
$1.67-$5 dust orders through; 4) the top-up dead-band was skipped at a full book (Case 2);
5) the trim lever had no session cap (rotation has one)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from config import get_config
from core.portfolio_manager import OpportunityScore, PortfolioManager


def _pm(capital=100_000.0):
    pm = PortfolioManager(client=MagicMock(), total_capital=capital)
    pm.refresh_positions = lambda: None
    return pm


def test_rebalance_cooldown_hours_is_wired():
    pm = _pm()
    pm._rebalance_cooldown_hours = 2.0  # config value must be HONORED (was dead)
    pm._last_rebalance["X"] = datetime.now(timezone.utc) - timedelta(hours=1)
    assert pm._can_trade_symbol("X") is False  # 1h < 2h cooldown


def test_stopout_reentry_lockout_armed_by_default():
    assert float(get_config().STOPOUT_REENTRY_COOLDOWN_MIN) == 240.0


def test_min_order_value_floor_raised():
    assert float(get_config().MIN_ORDER_VALUE_USD) == 50.0


def test_trim_session_cap_exists():
    assert int(get_config().TRIM_MAX_EXITS_PER_SESSION) == 3


def test_topup_dead_band_applies_at_full_book():
    pm = _pm()
    pm.max_positions = 1  # book is full with the one held name
    pm._topup_dead_band_pct = 5.0
    pm._position_scores["X"] = MagicMock(market_value=20_000.0, total_score=70.0)
    pm._can_trade_symbol = lambda s: True
    pm._blend_conviction = lambda s, c: 0.5
    pm._conviction_target_pct = lambda c: 22.0  # target 22% vs held 20% => inside band
    opp = OpportunityScore(symbol="X", current_price=100.0, model_confidence=0.5)
    opp.total_score = 80.0
    ok, reason, close = pm.should_open_new_position(opp)
    assert ok is False and close is None
    assert "dead-band" in reason
