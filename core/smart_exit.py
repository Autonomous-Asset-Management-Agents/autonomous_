# smart_exit.py
# --- SMART EXIT: Rule-based exit logic (no panic sell, maximize profit capture) ---
# Works with LSTM rankings: sell when dropped from top N, or trailing/take-profit/stop-loss
# All percentages are configurable via config.py (TRAILING_STOP_PCT, STOP_LOSS_PCT, TAKE_PROFIT_PCT, etc.)

from dataclasses import dataclass
from typing import Optional


def _smart_exit_config(name: str, default: float) -> float:
    try:
        import config

        return getattr(config, name, default)
    except ImportError:
        return default


# Defaults (overridden by config when present)
TRAILING_STOP_PCT = _smart_exit_config("TRAILING_STOP_PCT", 3.0)
STOP_LOSS_PCT = _smart_exit_config("STOP_LOSS_PCT", 7.0)
TAKE_PROFIT_PCT = _smart_exit_config("TAKE_PROFIT_PCT", 25.0)
MIN_HOLD_HOURS_BEFORE_TRAIL = _smart_exit_config("MIN_HOLD_HOURS_BEFORE_TRAIL", 1.0)
MIN_PROFIT_FOR_TRAIL_PCT = _smart_exit_config("MIN_PROFIT_FOR_TRAIL_PCT", 2.0)
# Smart take-profit: ATR-based and time-scaled targets (reduce churn, capture more in volatile names)
SMART_TAKE_PROFIT_ENABLED = bool(_smart_exit_config("SMART_TAKE_PROFIT_ENABLED", 1.0))
TAKE_PROFIT_ATR_MULTIPLIER = _smart_exit_config(
    "TAKE_PROFIT_ATR_MULTIPLIER", 2.0
)  # Target at least 2*ATR % gain
TAKE_PROFIT_TIME_SCALE_HOURS = 24.0  # After 24h hold, scale target up
TAKE_PROFIT_TIME_SCALE_FACTOR = (
    1.2  # 20% higher target after TAKE_PROFIT_TIME_SCALE_HOURS
)
TAKE_PROFIT_MAX_SCALE = 1.5  # Cap effective target at base * this


@dataclass
class ExitDecision:
    """Result of smart exit evaluation"""

    action: str  # "HOLD" or "SELL"
    reason: str  # Human-readable reason


def _effective_take_profit_pct(
    take_profit_pct: float,
    hours_held: float,
    atr_pct: Optional[float],
    smart_enabled: bool,
) -> float:
    """Compute effective take-profit %: ATR-based minimum and time-scaled target."""
    effective = take_profit_pct
    if smart_enabled and take_profit_pct > 0:
        # ATR-based: in volatile names, aim for at least 2*ATR as % of price (avoid cutting winners early)
        if atr_pct is not None and atr_pct > 0:
            atr_target_pct = atr_pct * 100 * TAKE_PROFIT_ATR_MULTIPLIER
            effective = max(effective, min(take_profit_pct, atr_target_pct))
        # Time-scaled: hold longer -> allow higher target (reduce churn, capture extended moves)
        if hours_held >= TAKE_PROFIT_TIME_SCALE_HOURS:
            scale = 1.0 + (TAKE_PROFIT_TIME_SCALE_FACTOR - 1.0) * min(
                1.0, (hours_held - TAKE_PROFIT_TIME_SCALE_HOURS) / 48.0
            )
            scale = min(scale, TAKE_PROFIT_MAX_SCALE)
            effective = effective * scale
    return effective


def should_sell_smart(
    symbol: str,
    entry_price: float,
    current_price: float,
    high_water_mark: float,
    hours_held: float,
    in_top_n: bool,
    lstm_rank: Optional[int],
    top_n_size: int = 10,
    trailing_stop_pct: float = TRAILING_STOP_PCT,
    stop_loss_pct: float = STOP_LOSS_PCT,
    take_profit_pct: float = TAKE_PROFIT_PCT,
    min_hold_hours: float = MIN_HOLD_HOURS_BEFORE_TRAIL,
    min_profit_for_trail_pct: float = MIN_PROFIT_FOR_TRAIL_PCT,
    atr_pct: Optional[float] = None,
    smart_take_profit: bool = True,
) -> ExitDecision:
    """
    Decide whether to sell a position using rule-based logic.
    No panic selling: trailing and stops only after minimum hold or profit.

    Smart take-profit (when smart_take_profit=True):
    - ATR-based: target at least 2*ATR % gain in volatile names (atr_pct = ATR/price).
    - Time-scaled: after 24h hold, effective target increases (up to 1.5x) to reduce churn.

    Rules (checked in order):
    1. Dropped from top N (LSTM no longer recommends) -> SELL (rebalance)
    2. Stop-loss: down more than stop_loss_pct from entry -> SELL
    3. Take-profit: up more than effective take_profit_pct -> SELL
    4. Trailing stop: after min_hold and min_profit, price below high-water * (1 - trail%) -> SELL
    5. Otherwise -> HOLD
    """
    if entry_price <= 0 or current_price <= 0:
        return ExitDecision("HOLD", "Invalid prices")

    pnl_pct = ((current_price - entry_price) / entry_price) * 100
    drawdown_from_high_pct = (
        ((high_water_mark - current_price) / high_water_mark) * 100
        if high_water_mark > 0
        else 0
    )

    # 1. Dropped from top N -> sell to rebalance (LSTM prefers other names)
    if not in_top_n and lstm_rank is not None and lstm_rank > top_n_size:
        return ExitDecision(
            "SELL",
            f"Dropped from LSTM top {top_n_size} (rank {lstm_rank}) - rebalancing",
        )

    # 2. Hard stop-loss (always apply)
    if pnl_pct <= -stop_loss_pct:
        return ExitDecision(
            "SELL", f"Stop-loss: {pnl_pct:.1f}% below entry (limit -{stop_loss_pct}%)"
        )

    # 3. Take-profit (smart: ATR-based + time-scaled target to reduce churn)
    effective_tp = _effective_take_profit_pct(
        take_profit_pct,
        hours_held,
        atr_pct,
        smart_enabled=bool(smart_take_profit and SMART_TAKE_PROFIT_ENABLED),
    )
    if pnl_pct >= effective_tp:
        return ExitDecision(
            "SELL", f"Take-profit: +{pnl_pct:.1f}% (target +{effective_tp:.1f}%)"
        )

    # 4. Trailing stop: only after minimum hold and some profit (avoid panic on small dips)
    if hours_held >= min_hold_hours and pnl_pct >= min_profit_for_trail_pct:
        if drawdown_from_high_pct >= trailing_stop_pct:
            return ExitDecision(
                "SELL",
                f"Trailing stop: {drawdown_from_high_pct:.1f}% down from high (trail {trailing_stop_pct}%)",
            )

    return ExitDecision("HOLD", "No exit trigger")
