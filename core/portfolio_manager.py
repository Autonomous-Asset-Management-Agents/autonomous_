# portfolio_manager.py
# --- SMART PORTFOLIO MANAGEMENT: Self-Aware Position Comparison & Intelligent Rebalancing ---

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from core.protocols import BrokerClientProtocol


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
    last_updated: datetime = field(default_factory=datetime.now)


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
        self._position_history: Dict[str, List[Dict]] = (
            {}
        )  # Historical scores for trend

        # Load config values with defaults
        try:
            from config import (
                CONSECUTIVE_SELL_BYPASS_THRESHOLD,
                MAX_TRADES_PER_SYMBOL_PER_DAY,
                MIN_HOLD_HOURS,
                REBALANCE_COOLDOWN_HOURS,
                REBALANCE_DRIFT_THRESHOLD_PCT,
            )

            self._drift_threshold_pct = REBALANCE_DRIFT_THRESHOLD_PCT
            self._rebalance_cooldown_hours = REBALANCE_COOLDOWN_HOURS
            self._min_hold_hours = MIN_HOLD_HOURS
            self._max_trades_per_day = MAX_TRADES_PER_SYMBOL_PER_DAY
            self._consecutive_sell_threshold = CONSECUTIVE_SELL_BYPASS_THRESHOLD
        except ImportError:
            self._drift_threshold_pct = 5.0
            self._rebalance_cooldown_hours = 4.0
            self._min_hold_hours = 4.0
            self._max_trades_per_day = 3
            self._consecutive_sell_threshold = 8

        # Rebalancing controls
        self._last_rebalance: Dict[str, datetime] = {}  # {symbol: last_rebalance_time}

        # Trade history for churn prevention
        self._trade_history: Dict[str, List[datetime]] = {}  # {symbol: [trade_times]}

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
                    first_buy = min(self._trade_history[symbol])
                    days_held = (datetime.now() - first_buy).days

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
                    last_updated=datetime.now(),
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

        except Exception as e:
            logging.warning("Portfolio Manager: Error refreshing positions: %s", e)

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

    def update_position_conviction(self, symbol: str, conviction: float):
        """Update conviction score for a position (called by strategy)"""
        if symbol in self._position_scores:
            # Convert 0-1 conviction to 0-100 score
            self._position_scores[symbol].conviction_score = conviction * 100
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
            "timestamp": datetime.now().isoformat(),
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
        num_positions = len(self._position_scores)

        # Case 1: Portfolio has room - be strategic and permissive (trust LSTM+RL)
        if num_positions < self.max_positions:
            # Only block on daily trade limit (8/day per symbol), not 30-min cooldown
            if not self._can_trade_symbol_when_room(opportunity.symbol):
                return False, f"{opportunity.symbol} at daily trade limit", None
            if (
                opportunity.total_score >= 25
            ):  # Low threshold when we have room - capture good signals
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
            today = datetime.now().date()
            trades_today = [t for t in self._trade_history[symbol] if t.date() == today]
            if len(trades_today) >= self._max_trades_per_day:
                return False
        return True

    def _can_trade_symbol(self, symbol: str) -> bool:
        """Check if symbol is available for trading when at max positions (swap/rebalance)."""
        now = datetime.now()
        if symbol in self._last_rebalance:
            hours_since = (now - self._last_rebalance[symbol]).total_seconds() / 3600
            if hours_since < 0.5:
                return False
        if symbol in self._trade_history:
            today = now.date()
            trades_today = [t for t in self._trade_history[symbol] if t.date() == today]
            if len(trades_today) >= self._max_trades_per_day:
                return False
        return True

    def record_trade(self, symbol: str, side: str):
        """Record a trade for churn prevention tracking"""
        now = datetime.now()

        if symbol not in self._trade_history:
            self._trade_history[symbol] = []

        self._trade_history[symbol].append(now)
        self._last_rebalance[symbol] = now

        # Keep only last 30 days of history
        cutoff = now - timedelta(days=30)
        self._trade_history[symbol] = [
            t for t in self._trade_history[symbol] if t > cutoff
        ]

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
        last_trade = max(self._trade_history[symbol])
        hours_held = (datetime.now() - last_trade).total_seconds() / 3600

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

        # Calculate target allocation per position
        target_allocation = self.total_capital / self.max_positions
        target_pct = 100.0 / self.max_positions

        for symbol, score in self._position_scores.items():
            current_pct = (score.market_value / self.total_capital) * 100
            drift = current_pct - target_pct

            # Only recommend if drift exceeds threshold
            if abs(drift) < self._drift_threshold_pct:
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
