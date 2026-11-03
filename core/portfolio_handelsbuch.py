# portfolio_handelsbuch.py
# #4187 (H-5f, #4287): Handelsbuch und Verkaufssignale des Portfolio-Kerns, als Mixin von PortfolioManager.
"""Handelsbuch, Churn-Sperren und Verkaufssignale (``HandelsbuchMixin``).

Schnitt: ``docs/3738-arc-e6-gestalt/H5_SCHNITT_portfolio_manager.md`` (#4187), Abschnitt H-5f;
Umsetzung #4287. Zulassung, Verdrängung und Bericht lesen das Handelsbuch über ``self``.

Zugriffsregel (Entscheidung §3): Kein Zielmodul importiert ``core.portfolio_manager``. Wer am
Modulobjekt patcht, patcht dort, wo der Name gelesen wird — hier also in
``core.portfolio_handelsbuch``.
"""

import logging
from datetime import date, datetime, timedelta
from typing import Tuple

import pytz

from core.portfolio_typen import _ensure_aware_utc, _now_utc

_MARKET_TZ = pytz.timezone("America/New_York")


def _trading_day_et(dt: datetime) -> date:
    """The ET calendar day a UTC instant belongs to — the NY trading-day boundary.

    'Trades today' must be counted per NY session, not per UTC day: migrating the
    history to UTC would otherwise silently shift a naive-local ``.date()`` by the
    UTC offset and mis-bucket late-evening ET trades into the wrong session day.
    """
    return _ensure_aware_utc(dt).astimezone(_MARKET_TZ).date()


class HandelsbuchMixin:
    """Handelsbuch von ``PortfolioManager`` (#4187, H-5f).

    Der Zustand entsteht in ``PortfolioManager.__init__``: ``_trade_history``,
    ``_history_lock``, ``_last_rebalance``, ``_max_trades_per_day``,
    ``_min_order_interval_sec``, ``_rebalance_cooldown_hours``,
    ``_consecutive_sell_signals``, ``_consecutive_sell_threshold``, ``_min_hold_hours``,
    ``_last_refresh_ok``.
    """

    def _can_trade_symbol_when_room(self, symbol: str) -> bool:
        """When portfolio has room: only enforce daily limit, no rebalance cooldown (strategic: allow new names)."""
        if symbol in self._trade_history:
            today = _trading_day_et(_now_utc())
            trades_today = [
                t for t in self._trade_history[symbol] if _trading_day_et(t) == today
            ]
            if len(trades_today) >= self._max_trades_per_day:
                return False
        return True

    def _within_order_cooldown(self, symbol: str) -> bool:
        """True if ``symbol`` last traded within MIN_ORDER_INTERVAL_SEC.

        The per-symbol anti-churn floor (ADR-R12): block re-OPENING a name we just
        traded so a buy->sell->buy micro-churn cannot spend the daily-trade budget.
        BUY-side only — called from ``should_open_new_position`` and never from an
        exit path, so it can never trap a risk-reducing SELL.
        """
        if self._min_order_interval_sec <= 0:
            return False
        with self._history_lock:
            history = self._trade_history.get(symbol)
            if not history:
                return False
            last = max(_ensure_aware_utc(t) for t in history)
        elapsed = (_now_utc() - last).total_seconds()
        return elapsed < self._min_order_interval_sec

    def _can_trade_symbol(self, symbol: str) -> bool:
        """Check if symbol is available for trading when at max positions (swap/rebalance)."""
        now = _now_utc()
        if symbol in self._last_rebalance:
            hours_since = (
                now - _ensure_aware_utc(self._last_rebalance[symbol])
            ).total_seconds() / 3600
            # #2714: honor REBALANCE_COOLDOWN_HOURS (was dead - hard-coded 0.5h while
            # the config value was set at init and never read).
            cooldown_h = float(getattr(self, "_rebalance_cooldown_hours", 0.5) or 0.5)
            if hours_since < cooldown_h:
                return False
        if symbol in self._trade_history:
            today = _trading_day_et(now)
            trades_today = [
                t for t in self._trade_history[symbol] if _trading_day_et(t) == today
            ]
            if len(trades_today) >= self._max_trades_per_day:
                return False
        return True

    def record_trade(self, symbol: str, side: str):
        """Record a trade for churn prevention tracking"""
        now = _now_utc()
        cutoff = now - timedelta(days=30)

        # FINDING-01: append + 30-day prune is a read-modify-write; hold the lock so a
        # concurrent record_trade on the same symbol cannot be dropped by the rebuild.
        with self._history_lock:
            history = self._trade_history.setdefault(symbol, [])
            history.append(now)
            # Keep only last 30 days (aware-safe: tolerate any legacy naive stamp).
            self._trade_history[symbol] = [
                _ensure_aware_utc(t) for t in history if _ensure_aware_utc(t) > cutoff
            ]
            self._last_rebalance[symbol] = now

        # O3-SAFETY (recency staleness): a SELL removes a holding that `_position_scores` still lists
        # until the NEXT refresh_positions(). Cash-aware sizing reads that lagged map as the held count,
        # so an un-refreshed post-SELL map OVER-counts held → collapses the divisor → over-concentrates
        # the next BUY. Mark the count unconfirmed so effective_free_slots fails safe to the fixed cap
        # until the next successful refresh re-syncs the map (which then excludes the sold name).
        # Safe direction only: worst case is a slightly-smaller BUY, never a larger one.
        if str(side).lower() == "sell":
            self._last_refresh_ok = False

        logging.debug("📊 Trade recorded: %s %s - Cooldown started", side, symbol)

    def can_sell_position(self, symbol: str) -> Tuple[bool, str]:
        """Check if a position can be sold (minimum hold period or 5 consecutive SELLs)"""
        if symbol not in self._trade_history or not self._trade_history[symbol]:
            return True, "No trade history - can sell"

        # Check if we have enough consecutive SELL signals to bypass hold period
        consecutive_sells = self._consecutive_sell_signals.get(symbol, 0)
        if consecutive_sells >= self._consecutive_sell_threshold:
            return True, f"Hold bypassed: {consecutive_sells} consecutive SELL signals"

        # Find the most recent BUY for this symbol
        last_trade = max(_ensure_aware_utc(t) for t in self._trade_history[symbol])
        hours_held = (_now_utc() - last_trade).total_seconds() / 3600

        if hours_held < self._min_hold_hours:
            return (
                False,
                f"Minimum hold period not met ({hours_held:.1f}/{self._min_hold_hours:.0f} hours) - need {self._consecutive_sell_threshold - consecutive_sells} more SELL signals to bypass",
            )

        return True, "Hold period satisfied"

    def record_sell_signal(self, symbol: str) -> int:
        """Record a SELL signal for a symbol, returns current consecutive count"""
        if symbol not in self._consecutive_sell_signals:
            self._consecutive_sell_signals[symbol] = 0
        self._consecutive_sell_signals[symbol] += 1
        count = self._consecutive_sell_signals[symbol]
        if count >= self._consecutive_sell_threshold:
            logging.info(
                f"🔓 [{symbol}] {count} consecutive SELL signals - hold period bypassed!"
            )
        return count

    def reset_sell_signals(self, symbol: str):
        """Reset consecutive sell signals (called on BUY or HOLD)"""
        if symbol in self._consecutive_sell_signals:
            self._consecutive_sell_signals[symbol] = 0

    def clear_sell_signals_after_sale(self, symbol: str):
        """Clear consecutive sell signals after a successful sale"""
        if symbol in self._consecutive_sell_signals:
            del self._consecutive_sell_signals[symbol]
