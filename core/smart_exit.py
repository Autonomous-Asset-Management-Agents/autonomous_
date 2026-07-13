# smart_exit.py
# --- SMART EXIT: Rule-based exit logic (no panic sell, maximize profit capture) ---
# Works with LSTM rankings: sell when dropped from top N, or trailing/take-profit/stop-loss
# All percentages are configurable via config.py (TRAILING_STOP_PCT, STOP_LOSS_PCT, TAKE_PROFIT_PCT, etc.)

from dataclasses import dataclass
from datetime import datetime
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
# #1952 — Min-hold + hysteresis gate on rule #1 (rebalance-sell) ONLY.
# The LSTM signal is a ~5-day-horizon signal; the legacy rule #1 sold a name the
# moment it fell out of the top-N — every cycle — churning the book daily and
# discarding the horizon alpha (bake-off: daily-naive net Sharpe 0.758 vs
# ~5-day-hold ~1.56, +0.80). These gate ONLY the rebalance-sell; stop-loss,
# take-profit and trailing (rules 2-4) are never affected. Rollback lever:
# SMART_EXIT_MIN_HOLD_DAYS=0 restores the exact legacy sell-on-drop behavior.
SMART_EXIT_MIN_HOLD_DAYS = _smart_exit_config("SMART_EXIT_MIN_HOLD_DAYS", 5.0)
SMART_EXIT_EXIT_RANK_HYSTERESIS = _smart_exit_config(
    "SMART_EXIT_EXIT_RANK_HYSTERESIS", 3.0
)
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


def resolve_hold_hours(
    entry_time: Optional[datetime],
    now: datetime,
    min_hold_days: float = SMART_EXIT_MIN_HOLD_DAYS,
) -> float:
    """Holding age in hours for the smart-exit rules, with a #1952 fail-open default.

    ``entry_time`` is None when the position was NOT opened in this process — e.g.
    a position reconciled after an engine restart, where the strategy's in-memory
    ``_entry_time`` map is empty. Returning ~0h there would silently freeze the
    min-hold gate (rule #1 rebalance-sell suppressed until rank collapse), holding
    dropped names far past the intended window. Instead FAIL OPEN: return a value
    just past the min-hold window so rule #1 behaves like the legacy sell-on-drop
    for unknown-age positions. Risk rules (stop-loss / take-profit / trailing) are
    unaffected either way. A durable, restart-surviving entry time is the proper fix
    (follow-up). Also None-safe — never raises on an unknown entry time.
    """
    if entry_time is None:
        return (min_hold_days + 1.0) * 24.0
    return (now - entry_time).total_seconds() / 3600.0


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
    min_hold_days: float = SMART_EXIT_MIN_HOLD_DAYS,
    exit_rank_hysteresis: float = SMART_EXIT_EXIT_RANK_HYSTERESIS,
) -> ExitDecision:
    """
    Decide whether to sell a position using rule-based logic.
    No panic selling: trailing and stops only after minimum hold or profit.

    Smart take-profit (when smart_take_profit=True):
    - ATR-based: target at least 2*ATR % gain in volatile names (atr_pct = ATR/price).
    - Time-scaled: after 24h hold, effective target increases (up to 1.5x) to reduce churn.

    Rules (checked in order):
    1. Dropped from top N (LSTM no longer recommends) -> SELL (rebalance), GATED
       by a min-hold + hysteresis window (#1952): a plain rotation-sell only fires
       once the name has been held >= min_hold_days, OR earlier if its rank has
       collapsed past top_n_size * exit_rank_hysteresis (sustained adverse info).
       Within the window without a collapse the name is HELD (no daily churn) and
       execution FALLS THROUGH to the risk rules below.
    2. Stop-loss: down more than stop_loss_pct from entry -> SELL
    3. Take-profit: up more than effective take_profit_pct -> SELL
    4. Trailing stop: after min_hold and min_profit, price below high-water * (1 - trail%) -> SELL
    5. Otherwise -> HOLD

    SAFETY (#1952): the min-hold gate applies to rule #1 ONLY. Stop-loss (rule 2),
    take-profit (rule 3) and trailing stop (rule 4) are reached by fall-through and
    fire normally inside the min-hold window — risk exits are never blocked.
    Rollback: min_hold_days=0 restores the legacy immediate sell-on-drop behavior.
    """
    if entry_price <= 0 or current_price <= 0:
        return ExitDecision("HOLD", "Invalid prices")

    pnl_pct = ((current_price - entry_price) / entry_price) * 100
    drawdown_from_high_pct = (
        ((high_water_mark - current_price) / high_water_mark) * 100
        if high_water_mark > 0
        else 0
    )

    # 1. Dropped from top N -> sell to rebalance (LSTM prefers other names).
    #    #1952: gated by min-hold + hysteresis. The LSTM signal is a ~5-day-horizon
    #    signal, so a name is committed for at least min_hold_days before a plain
    #    rotation-sell fires. An EARLY exit still fires within the window when the
    #    rank has collapsed past top_n_size * exit_rank_hysteresis (sustained
    #    adverse info, not daily noise). Otherwise HOLD and fall through to the
    #    risk rules (stop-loss / take-profit / trailing) below — those are NEVER
    #    gated. min_hold_days=0 restores the legacy immediate sell-on-drop.
    if not in_top_n and lstm_rank is not None and lstm_rank > top_n_size:
        days_held = hours_held / 24.0
        min_hold_ok = days_held >= min_hold_days
        rank_collapsed = lstm_rank > top_n_size * exit_rank_hysteresis
        if min_hold_ok or rank_collapsed:
            trigger = "rebalancing" if min_hold_ok else "rank-collapse rebalancing"
            return ExitDecision(
                "SELL",
                f"Dropped from LSTM top {top_n_size} (rank {lstm_rank}) - {trigger}",
            )
        # In min-hold window, no collapse: do NOT churn on daily noise. Fall
        # through so stop-loss / take-profit / trailing can still protect the position.

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
