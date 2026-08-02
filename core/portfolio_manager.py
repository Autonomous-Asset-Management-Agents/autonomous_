# portfolio_manager.py
# --- SMART PORTFOLIO MANAGEMENT: Self-Aware Position Comparison & Intelligent Rebalancing ---

import logging
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import pytz

from core.protocols import BrokerClientProtocol

_MARKET_TZ = pytz.timezone("America/New_York")


def _ensure_aware_utc(dt: datetime) -> datetime:
    """Treat a naive datetime as UTC; leave aware datetimes unchanged.

    Defense-in-depth for POLICY-01: PortfolioManager now stamps all history in
    aware UTC, but a naive value could still survive a hot-reload. Normalising here
    guarantees aware-vs-aware arithmetic, so a stray naive stamp can never raise
    TypeError in a cooldown or date comparison.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _trading_day_et(dt: datetime) -> date:
    """The ET calendar day a UTC instant belongs to — the NY trading-day boundary.

    'Trades today' must be counted per NY session, not per UTC day: migrating the
    history to UTC would otherwise silently shift a naive-local ``.date()`` by the
    UTC offset and mis-bucket late-evening ET trades into the wrong session day.
    """
    return _ensure_aware_utc(dt).astimezone(_MARKET_TZ).date()


@dataclass
class PositionScore:
    """Comprehensive scoring for a position's worthiness to be held"""

    symbol: str
    qty: float
    avg_entry: float
    current_price: float
    market_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float

    # Scoring components (0-100 each)
    momentum_score: float = 50.0  # Price trend strength
    conviction_score: float = 50.0  # Original trade conviction
    risk_adjusted_score: float = 50.0  # Return vs volatility
    holding_period_score: float = 50.0  # Time-based (avoid churning)

    # Final composite score
    total_score: float = 50.0

    # Metadata
    days_held: int = 0
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class OpportunityScore:
    """Scoring for a potential new position"""

    symbol: str
    current_price: float

    # Signal components
    rl_action: int = 0  # 0=HOLD, 1=BUY, 2=SELL
    model_confidence: float = 0.0

    # Technical scores (0-100)
    momentum_score: float = 50.0
    value_score: float = 50.0  # RSI oversold = high value
    trend_score: float = 50.0  # ADX strength

    # Final composite
    total_score: float = 50.0

    # Debate results
    arguments_for: List[str] = field(default_factory=list)
    arguments_against: List[str] = field(default_factory=list)
    debate_conclusion: str = ""


class PortfolioManager:
    """
    Intelligent Portfolio Management System

    Features:
    1. Position Awareness - Track and score all current holdings
    2. Self-Debate - Compare new opportunities against existing positions
    3. Smart Rebalancing - Adjust allocations without rapid trading
    4. Churn Prevention - Enforce minimum hold periods and cooldowns
    """

    def __init__(
        self,
        client: BrokerClientProtocol,
        total_capital: float,
        max_positions: int = 10,
        user_id: str = "oss-single",
    ):
        self.client = client
        self.total_capital = total_capital
        self.max_positions = max_positions
        self.user_id = (
            user_id  # Redis Key-Namespace: pm:trade_history:{user_id}:{symbol}
        )

        # Position tracking
        self._position_scores: Dict[str, PositionScore] = {}
        # O3-SAFETY: True only after a refresh_positions() that CONFIRMED the live position set.
        # Starts False so cash-aware sizing stays conservative (fixed book-cap divisor) until the
        # first successful broker read — a stale/unconfirmed count must never collapse the divisor.
        self._last_refresh_ok: bool = False
        self._position_history: Dict[str, List[Dict]] = (
            {}
        )  # Historical scores for trend

        # Load config values with defaults
        try:
            from config import (
                CONSECUTIVE_SELL_BYPASS_THRESHOLD,
                CONVICTION_EWMA_ALPHA,
                CONVICTION_EWMA_ENABLED,
                DECONCENTRATION_TRIM_RESPECTS_CONVICTION,
                MAX_POSITION_PERCENT,
                MAX_TRADES_PER_SYMBOL_PER_DAY,
                MIN_HOLD_HOURS,
                MIN_ORDER_INTERVAL_SEC,
                MIN_POSITION_PERCENT,
                POSITION_TOPUP_DEAD_BAND_PCT,
                REBALANCE_COOLDOWN_HOURS,
                REBALANCE_DRIFT_THRESHOLD_PCT,
            )

            self._drift_threshold_pct = REBALANCE_DRIFT_THRESHOLD_PCT
            self._rebalance_cooldown_hours = REBALANCE_COOLDOWN_HOURS
            self._min_hold_hours = MIN_HOLD_HOURS
            self._max_trades_per_day = MAX_TRADES_PER_SYMBOL_PER_DAY
            self._consecutive_sell_threshold = CONSECUTIVE_SELL_BYPASS_THRESHOLD
            self._min_order_interval_sec = MIN_ORDER_INTERVAL_SEC
            # Conviction-weighting fix: trim to the concentration cap, not flat 1/N.
            self._trim_respects_conviction = DECONCENTRATION_TRIM_RESPECTS_CONVICTION
            self._max_position_pct = MAX_POSITION_PERCENT
            # Conviction EWMA + top-up dead-band (churn fix).
            self._conviction_ewma_enabled = CONVICTION_EWMA_ENABLED
            self._conviction_ewma_alpha = CONVICTION_EWMA_ALPHA
            self._topup_dead_band_pct = POSITION_TOPUP_DEAD_BAND_PCT
            self._min_position_pct = MIN_POSITION_PERCENT
        except ImportError:
            self._drift_threshold_pct = 5.0
            self._rebalance_cooldown_hours = 4.0
            self._min_hold_hours = 4.0
            self._max_trades_per_day = 3
            self._consecutive_sell_threshold = 8
            self._min_order_interval_sec = 900
            self._trim_respects_conviction = True
            self._max_position_pct = 0.25
            self._conviction_ewma_enabled = True
            self._conviction_ewma_alpha = 0.3
            self._topup_dead_band_pct = 5.0
            self._min_position_pct = 0.05
        # Per-symbol EWMA state for conviction smoothing (0-1). Reset on restart (no Redis on desktop).
        self._conviction_ewma: Dict[str, float] = {}

        # Rebalancing controls
        self._last_rebalance: Dict[str, datetime] = {}  # {symbol: last_rebalance_time}

        # Trade history for churn prevention
        self._trade_history: Dict[str, List[datetime]] = {}  # {symbol: [trade_times]}
        # FINDING-01 (#2176 audit): the 30-day prune in record_trade is a
        # read-modify-write on the per-symbol list; this lock keeps that mutation and
        # the cooldown read atomic if the PM is ever touched from an offloaded worker
        # thread (async broker offload), so a concurrent append cannot be dropped.
        self._history_lock = threading.Lock()

        # Consecutive sell signal tracking (bypass hold period after N consecutive SELLs)
        self._consecutive_sell_signals: Dict[str, int] = {}  # {symbol: count}
        # Note: _consecutive_sell_threshold is now loaded from config above (default: 8)

        # Debate logging
        self._debate_history: List[Dict] = []

        logging.info(
            f"📊 Portfolio Manager initialized: max_positions={max_positions}, min_hold={self._min_hold_hours}h, cooldown={self._rebalance_cooldown_hours}h"
        )

    def update_total_capital(self, total_capital: float) -> None:
        """Update total capital from live account (call when equity changes to fix distribution)."""
        if total_capital is not None and total_capital > 0:
            self.total_capital = float(total_capital)
            logging.debug(
                f"Portfolio Manager: total_capital updated to ${self.total_capital:,.2f}"
            )

    def refresh_positions(self) -> Dict[str, PositionScore]:
        """Fetch current positions and calculate scores for each"""
        try:
            # Live trading: keep total_capital in sync with account equity (fixes distribution)
            if hasattr(self.client, "get_account"):
                try:
                    acc = self.client.get_account()
                    eq = float(getattr(acc, "equity", 0) or 0)
                    if eq > 0:
                        self.total_capital = eq
                except Exception as e:
                    # BUG-AI-114 (#1240): do NOT swallow silently. The last live
                    # value is kept (self-healing next cycle), but the sync failure
                    # must be visible so an API flap isn't invisible.
                    logging.warning(
                        "[PortfolioManager] equity sync failed — keeping last "
                        "total_capital (%.2f): %s",
                        self.total_capital,
                        e,
                    )
            positions = self.client.get_all_positions()

            for pos in positions:
                # Handle both Alpaca objects and dicts
                if hasattr(pos, "symbol"):
                    symbol = pos.symbol
                    qty = float(pos.qty)
                    avg_entry = float(pos.avg_entry_price)
                    current_price = float(pos.current_price)
                    market_value = float(pos.market_value)
                    unrealized_pnl = float(pos.unrealized_pl)
                    unrealized_pnl_pct = float(pos.unrealized_plpc) * 100
                else:
                    symbol = pos.get("symbol", "N/A")
                    qty = float(pos.get("qty", 0))
                    avg_entry = float(pos.get("avg_entry_price", 0))
                    current_price = float(pos.get("current_price", avg_entry))
                    market_value = float(pos.get("market_value", qty * current_price))
                    unrealized_pnl = float(pos.get("unrealized_pl", 0))
                    cost = qty * avg_entry
                    unrealized_pnl_pct = (
                        (unrealized_pnl / cost * 100) if cost > 0 else 0
                    )

                # Calculate days held
                days_held = 0
                if symbol in self._trade_history and self._trade_history[symbol]:
                    first_buy = min(
                        _ensure_aware_utc(t) for t in self._trade_history[symbol]
                    )
                    days_held = (
                        datetime.now(timezone.utc) - _ensure_aware_utc(first_buy)
                    ).days

                # Create/update position score
                score = PositionScore(
                    symbol=symbol,
                    qty=qty,
                    avg_entry=avg_entry,
                    current_price=current_price,
                    market_value=market_value,
                    unrealized_pnl=unrealized_pnl,
                    unrealized_pnl_pct=unrealized_pnl_pct,
                    days_held=days_held,
                    last_updated=datetime.now(timezone.utc),
                )

                # Calculate component scores
                self._calculate_position_scores(score)
                self._position_scores[symbol] = score

            # Remove closed positions
            current_symbols = {
                pos.symbol if hasattr(pos, "symbol") else pos.get("symbol")
                for pos in positions
            }
            closed = [s for s in self._position_scores if s not in current_symbols]
            for s in closed:
                del self._position_scores[s]
                # Full exit → drop the conviction EWMA so a later re-entry starts fresh (raw), not
                # anchored to a stale smoothed value from the previous holding.
                self.clear_conviction(s)

            # O3-SAFETY: this refresh CONFIRMED the live position set (get_all_positions succeeded
            # and the map was rebuilt + pruned). Cash-aware sizing may trust the held count.
            self._last_refresh_ok = True
        except Exception as e:
            logging.warning("Portfolio Manager: Error refreshing positions: %s", e)
            # O3-SAFETY: the read failed — `_position_scores` is now STALE (un-pruned, possibly an
            # over-count). `effective_free_slots` reads this flag and fails safe to the fixed book-cap
            # divisor so a stale/inflated count cannot collapse the divisor and oversize a buy.
            self._last_refresh_ok = False

        return self._position_scores

    def _calculate_position_scores(self, score: PositionScore):
        """Calculate all component scores for a position"""

        # 1. MOMENTUM SCORE (based on unrealized P&L)
        # +10% = 100 score, -10% = 0 score, 0% = 50 score
        pnl_pct = score.unrealized_pnl_pct
        score.momentum_score = max(0, min(100, 50 + (pnl_pct * 5)))

        # 2. HOLDING PERIOD SCORE (reward patience, penalize churning)
        # < 1 day = 30, 1-3 days = 50, 3-7 days = 70, 7+ days = 90
        days = score.days_held
        if days < 1:
            score.holding_period_score = 30.0
        elif days < 3:
            score.holding_period_score = 50.0
        elif days < 7:
            score.holding_period_score = 70.0
        else:
            score.holding_period_score = 90.0

        # 3. RISK-ADJUSTED SCORE (simple P&L per day held)
        # Avoid division by zero
        days_for_calc = max(1, score.days_held)
        daily_return = score.unrealized_pnl_pct / days_for_calc
        # +1% daily = 100, -1% daily = 0
        score.risk_adjusted_score = max(0, min(100, 50 + (daily_return * 50)))

        # 4. CONVICTION SCORE (placeholder - will be updated by strategy)
        # Default to 50, updated when we have model data

        # TOTAL SCORE (weighted average)
        score.total_score = (
            score.momentum_score * 0.35  # 35% weight on momentum
            + score.holding_period_score * 0.20  # 20% weight on holding period
            + score.risk_adjusted_score * 0.25  # 25% weight on risk-adjusted
            + score.conviction_score * 0.20  # 20% weight on conviction
        )

    def _blend_conviction(self, symbol: str, raw: float) -> float:
        """EWMA-blend a raw per-cycle conviction (0-1) with the symbol's stored value — a READ
        (never stores). Flag off or no prior → raw. `smoothed = alpha·raw + (1-alpha)·prev`.
        """
        try:
            r = max(0.0, min(1.0, float(raw)))
        except (TypeError, ValueError):
            return 0.0
        prev = self._conviction_ewma.get(symbol)
        if not self._conviction_ewma_enabled or prev is None:
            return r
        a = self._conviction_ewma_alpha
        return a * r + (1.0 - a) * prev

    def _conviction_target_pct(self, conviction: float) -> float:
        """The dynamic sizer's target weight (%) for a conviction (0-1), mirroring
        risk_manager.calculate_position_size: min + (max_sizing - min)*conv, capped at the hard cap.
        """
        try:
            from config import get_config

            max_sizing = float(
                getattr(get_config(), "MAX_POSITION_PERCENT_SIZING", 0.30)
            )
        except (
            Exception
        ):  # noqa: BLE001 — a config read must never break a buy decision
            max_sizing = 0.30
        conv = max(0.0, min(1.0, float(conviction)))
        target = self._min_position_pct + (max_sizing - self._min_position_pct) * conv
        return min(target, self._max_position_pct) * 100.0

    def clear_conviction(self, symbol: str) -> None:
        """Drop a symbol's EWMA state on a full exit so a later re-entry starts fresh (raw)."""
        self._conviction_ewma.pop(symbol, None)

    def update_position_conviction(self, symbol: str, conviction: float):
        """Advance the per-symbol conviction EWMA on a fill and store the SMOOTHED value for ranking
        (churn fix — the conviction that drives ranking/sizing no longer jitters cycle-to-cycle).
        """
        smoothed = self._blend_conviction(symbol, conviction)
        self._conviction_ewma[symbol] = (
            smoothed  # the one place the EWMA advances (per fill)
        )
        if symbol in self._position_scores:
            self._position_scores[symbol].conviction_score = smoothed * 100
            self._calculate_position_scores(self._position_scores[symbol])

    def get_weakest_position(self) -> Optional[PositionScore]:
        """Return the position with lowest score (candidate for replacement)"""
        if not self._position_scores:
            return None
        return min(self._position_scores.values(), key=lambda p: p.total_score)

    def get_strongest_position(self) -> Optional[PositionScore]:
        """Return the position with highest score"""
        if not self._position_scores:
            return None
        return max(self._position_scores.values(), key=lambda p: p.total_score)

    def score_opportunity(
        self,
        symbol: str,
        current_price: float,
        rl_action: int,
        model_confidence: float,
        features: Optional[Dict] = None,
    ) -> OpportunityScore:
        """Score a potential new position opportunity"""

        opp = OpportunityScore(
            symbol=symbol,
            current_price=current_price,
            rl_action=rl_action,
            model_confidence=model_confidence,
        )

        if features:
            rsi = features.get("rsi_14", 50.0)
            adx = features.get("adx_14", 20.0)
            macd = features.get("macd", 0.0)

            # VALUE SCORE: RSI < 30 = oversold = high value opportunity
            if rsi < 30:
                opp.value_score = 90.0
                opp.arguments_for.append(f"Oversold (RSI {rsi:.1f})")
            elif rsi < 40:
                opp.value_score = 70.0
                opp.arguments_for.append(f"Near oversold (RSI {rsi:.1f})")
            elif rsi > 70:
                opp.value_score = 20.0
                opp.arguments_against.append(f"Overbought (RSI {rsi:.1f})")
            else:
                opp.value_score = 50.0

            # TREND SCORE: ADX > 25 = strong trend
            if adx > 30:
                opp.trend_score = 85.0
                opp.arguments_for.append(f"Strong trend (ADX {adx:.1f})")
            elif adx > 25:
                opp.trend_score = 70.0
                opp.arguments_for.append(f"Moderate trend (ADX {adx:.1f})")
            elif adx < 15:
                opp.trend_score = 30.0
                opp.arguments_against.append(f"No clear trend (ADX {adx:.1f})")
            else:
                opp.trend_score = 50.0

            # MOMENTUM: MACD direction
            if macd > 0:
                opp.momentum_score = 60 + min(40, abs(macd) * 5)
                opp.arguments_for.append(f"Positive momentum (MACD {macd:.2f})")
            else:
                opp.momentum_score = 40 - min(40, abs(macd) * 5)
                if abs(macd) > 2:
                    opp.arguments_against.append(f"Negative momentum (MACD {macd:.2f})")

        # MODEL CONFIDENCE SCORE
        # Confidence typically ranges from -5 to +5
        conf_normalized = max(0, min(100, 50 + (model_confidence * 10)))

        # RL ACTION bonus
        action_bonus = 0
        if rl_action == 1:  # BUY signal
            action_bonus = 15
            opp.arguments_for.append("RL model says BUY")
        elif rl_action == 2:  # SELL signal
            action_bonus = -15
            opp.arguments_against.append("RL model says SELL (not BUY)")

        # TOTAL SCORE
        opp.total_score = (
            opp.value_score * 0.25
            + opp.trend_score * 0.20
            + opp.momentum_score * 0.20
            + conf_normalized * 0.25
            + action_bonus
        )
        opp.total_score = max(0, min(100, opp.total_score))

        return opp

    def debate_position_swap(
        self, opportunity: OpportunityScore, weakest_position: Optional[PositionScore]
    ) -> Tuple[bool, str]:
        """
        Self-debate: Should we swap the weakest position for this new opportunity?

        Returns: (should_swap, reasoning)
        """
        if weakest_position is None:
            return True, "No existing positions - proceed with new position"

        debate_log = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "new_opportunity": opportunity.symbol,
            "opportunity_score": opportunity.total_score,
            "weakest_position": weakest_position.symbol,
            "position_score": weakest_position.total_score,
            "arguments_for_swap": [],
            "arguments_against_swap": [],
            "decision": "",
        }

        # Arguments FOR swapping
        args_for = opportunity.arguments_for.copy()

        score_diff = opportunity.total_score - weakest_position.total_score
        if score_diff > 15:
            args_for.append(f"New opportunity scores {score_diff:.1f} points higher")

        if weakest_position.unrealized_pnl_pct < -5:
            args_for.append(
                f"{weakest_position.symbol} is down {weakest_position.unrealized_pnl_pct:.1f}%"
            )

        if weakest_position.days_held > 5 and weakest_position.unrealized_pnl_pct < 2:
            args_for.append(
                f"{weakest_position.symbol} held {weakest_position.days_held} days with minimal gain"
            )

        # Arguments AGAINST swapping
        args_against = opportunity.arguments_against.copy()

        if score_diff < 10:
            args_against.append(
                f"Score difference only {score_diff:.1f} points - not compelling"
            )

        if weakest_position.unrealized_pnl_pct > 5:
            args_against.append(
                f"{weakest_position.symbol} is profitable (+{weakest_position.unrealized_pnl_pct:.1f}%)"
            )

        if weakest_position.days_held < 1:
            args_against.append(
                f"{weakest_position.symbol} held less than 1 day - give it time"
            )

        # Check for churn
        if not self._can_trade_symbol(weakest_position.symbol):
            args_against.append(
                f"{weakest_position.symbol} in cooldown period (churn prevention)"
            )

        if not self._can_trade_symbol(opportunity.symbol):
            args_against.append(f"{opportunity.symbol} in cooldown period")

        debate_log["arguments_for_swap"] = args_for
        debate_log["arguments_against_swap"] = args_against

        # DECISION LOGIC - strategic: more willing to swap weak for strong
        should_swap = False
        if score_diff > 15:
            should_swap = True
            reasoning = f"Strong upgrade: {opportunity.symbol} (score {opportunity.total_score:.0f}) >> {weakest_position.symbol} (score {weakest_position.total_score:.0f})"
        elif score_diff > 10 and len(args_for) >= len(args_against):
            should_swap = True
            reasoning = f"Upgrade: {opportunity.symbol} vs {weakest_position.symbol} ({len(args_for)} vs {len(args_against)} args)"
        elif score_diff > 8 and weakest_position.unrealized_pnl_pct < -2:
            should_swap = True
            reasoning = f"Cut loser {weakest_position.symbol} ({weakest_position.unrealized_pnl_pct:+.1f}%) for better opportunity"
        else:
            should_swap = False
            reasoning = (
                f"Keeping {weakest_position.symbol}: score diff {score_diff:.1f}"
                if len(args_against) == 0
                else f"Keeping {weakest_position.symbol}: {args_against[0]}"
            )

        debate_log["decision"] = "SWAP" if should_swap else "HOLD"
        debate_log["reasoning"] = reasoning
        self._debate_history.append(debate_log)

        # Keep last 100 debates
        if len(self._debate_history) > 100:
            self._debate_history = self._debate_history[-100:]

        return should_swap, reasoning

    def should_open_new_position(
        self, opportunity: OpportunityScore
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Main decision function: Should we open this new position?
        Strategic: when we have room, be permissive; when full, debate swap proactively.

        Returns: (should_open, reasoning, symbol_to_close)
        - symbol_to_close is set if we need to close a position to make room
        """
        self.refresh_positions()

        # Part C (ADR-R12): per-symbol order cooldown — refuse to re-OPEN a name
        # traded within MIN_ORDER_INTERVAL_SEC. Covers both the room and swap paths
        # below; BUY-side only, so exits / risk-reducing SELLs are never gated here.
        if self._within_order_cooldown(opportunity.symbol):
            return (
                False,
                f"{opportunity.symbol} in order cooldown "
                f"(< {self._min_order_interval_sec}s since last trade)",
                None,
            )

        num_positions = len(self._position_scores)

        # Case 1: Portfolio has room - be strategic and permissive (trust LSTM+RL)
        if num_positions < self.max_positions:
            # Only block on daily trade limit (8/day per symbol), not 30-min cooldown
            if not self._can_trade_symbol_when_room(opportunity.symbol):
                return False, f"{opportunity.symbol} at daily trade limit", None
            if (
                opportunity.total_score >= 25
            ):  # Low threshold when we have room - capture good signals
                # Top-up dead-band (churn fix): don't re-BUY a name already held within the dead-band
                # of its EWMA-smoothed conviction target weight — no micro re-size on signal jitter
                # (the buy-side counterpart to the rebalance drift threshold; a NEW name has no holding
                # and skips this). A read-only conviction blend; the EWMA advances only on a fill.
                held = self._position_scores.get(opportunity.symbol)
                if (
                    held is not None
                    and self._topup_dead_band_pct > 0
                    and self.total_capital > 0
                ):
                    conv = self._blend_conviction(
                        opportunity.symbol, opportunity.model_confidence
                    )
                    target_pct = self._conviction_target_pct(conv)
                    current_pct = (held.market_value / self.total_capital) * 100.0
                    if target_pct - current_pct < self._topup_dead_band_pct:
                        return (
                            False,
                            f"{opportunity.symbol} within top-up dead-band "
                            f"(held {current_pct:.1f}% vs conviction target {target_pct:.1f}%, "
                            f"band {self._topup_dead_band_pct:.0f}%)",
                            None,
                        )
                return (
                    True,
                    f"Room for new position (have {num_positions}/{self.max_positions})",
                    None,
                )
            return (
                False,
                f"Opportunity score too low ({opportunity.total_score:.0f}/100)",
                None,
            )

        # Case 2: Portfolio is full - need to debate swap (strategic rebalance)
        if not self._can_trade_symbol(opportunity.symbol):
            return (
                False,
                f"{opportunity.symbol} in cooldown - traded too recently",
                None,
            )
        weakest = self.get_weakest_position()
        should_swap, reasoning = self.debate_position_swap(opportunity, weakest)
        if should_swap:
            return True, reasoning, weakest.symbol if weakest else None
        return False, reasoning, None

    def _can_trade_symbol_when_room(self, symbol: str) -> bool:
        """When portfolio has room: only enforce daily limit, no rebalance cooldown (strategic: allow new names)."""
        if symbol in self._trade_history:
            today = _trading_day_et(datetime.now(timezone.utc))
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
        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
        return elapsed < self._min_order_interval_sec

    def _can_trade_symbol(self, symbol: str) -> bool:
        """Check if symbol is available for trading when at max positions (swap/rebalance)."""
        now = datetime.now(timezone.utc)
        if symbol in self._last_rebalance:
            hours_since = (
                now - _ensure_aware_utc(self._last_rebalance[symbol])
            ).total_seconds() / 3600
            if hours_since < 0.5:
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
        now = datetime.now(timezone.utc)
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
        hours_held = (datetime.now(timezone.utc) - last_trade).total_seconds() / 3600

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

    def get_rebalance_recommendations(self) -> List[Dict]:
        """
        Analyze portfolio and recommend rebalancing actions.
        Only recommends if drift exceeds threshold and cooldowns are satisfied.
        """
        self.refresh_positions()
        recommendations = []

        if not self._position_scores:
            return recommendations

        # Validate total_capital to avoid weird distribution (negative or zero)
        if self.total_capital <= 0:
            logging.warning(
                "Portfolio Manager: total_capital <= 0 - skipping rebalance recommendations"
            )
            return recommendations

        # Target-weight authority (conviction-weighting fix). RESPECTS_CONVICTION → de-concentrate
        # ONLY genuine over-concentration past MAX_POSITION_PERCENT (the hard sizing/gatekeeper cap),
        # so the conviction sizer's 5–25% band is left intact — winners are not trimmed back to a
        # flat 1/N weight the sizer just built past (which caused the buy-high/sell-low churn).
        # Legacy (flag off) → flat 1/max_positions equal-weight target, symmetric REDUCE/INCREASE.
        if self._trim_respects_conviction:
            target_pct = self._max_position_pct * 100.0
        else:
            target_pct = 100.0 / self.max_positions
        target_allocation = self.total_capital * (target_pct / 100.0)

        for symbol, score in self._position_scores.items():
            current_pct = (score.market_value / self.total_capital) * 100
            drift = current_pct - target_pct

            # RESPECTS_CONVICTION: only weight OVER the cap is a problem (a below-cap conviction
            # weight is legitimate — never trimmed, never INCREASEd). Legacy: symmetric drift band.
            if self._trim_respects_conviction:
                if drift < self._drift_threshold_pct:
                    continue
            elif abs(drift) < self._drift_threshold_pct:
                continue

            # Check cooldown
            if not self._can_trade_symbol(symbol):
                continue

            action = "REDUCE" if drift > 0 else "INCREASE"
            target_value = target_allocation
            current_value = score.market_value
            adjustment = target_value - current_value

            recommendations.append(
                {
                    "symbol": symbol,
                    "action": action,
                    "current_pct": current_pct,
                    "target_pct": target_pct,
                    "drift_pct": drift,
                    "adjustment_value": adjustment,
                    # Additive (#sell-decisions Lever B): the current $-value of the
                    # holding. The de-concentration TRIM sizes a partial SELL as a
                    # dollar-fraction of THIS (held_qty * min(1, |adjustment|/market_value)),
                    # never a price division — the rec carries no price, and a 0/None
                    # price would crash the exit hook. Read-only reporting is unaffected.
                    "market_value": score.market_value,
                    "position_score": score.total_score,
                    "reasoning": f"{action} {symbol}: drift {drift:+.1f}% from target",
                }
            )

        # Sort by absolute drift (largest first)
        recommendations.sort(key=lambda x: abs(x["drift_pct"]), reverse=True)

        return recommendations

    def get_portfolio_summary(self) -> Dict:
        """Get a summary of current portfolio state"""
        self.refresh_positions()

        if not self._position_scores:
            return {
                "num_positions": 0,
                "max_positions": self.max_positions,
                "total_value": 0,
                "total_pnl": 0,
                "total_pnl_pct": 0,
                "average_score": 0,
                "weakest": None,
                "strongest": None,
            }

        total_value = sum(p.market_value for p in self._position_scores.values())
        total_pnl = sum(p.unrealized_pnl for p in self._position_scores.values())
        avg_score = sum(p.total_score for p in self._position_scores.values()) / len(
            self._position_scores
        )

        weakest = self.get_weakest_position()
        strongest = self.get_strongest_position()

        return {
            "num_positions": len(self._position_scores),
            "max_positions": self.max_positions,
            "total_value": total_value,
            "total_pnl": total_pnl,
            "total_pnl_pct": (
                (total_pnl / (total_value - total_pnl) * 100)
                if total_value > total_pnl
                else 0
            ),
            "average_score": avg_score,
            "weakest": (
                {
                    "symbol": weakest.symbol,
                    "score": weakest.total_score,
                    "pnl_pct": weakest.unrealized_pnl_pct,
                }
                if weakest
                else None
            ),
            "strongest": (
                {
                    "symbol": strongest.symbol,
                    "score": strongest.total_score,
                    "pnl_pct": strongest.unrealized_pnl_pct,
                }
                if strongest
                else None
            ),
        }

    def get_debate_history(self, limit: int = 10) -> List[Dict]:
        """Get recent position swap debates"""
        return self._debate_history[-limit:]
