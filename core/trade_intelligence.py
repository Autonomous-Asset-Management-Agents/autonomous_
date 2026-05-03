# trade_intelligence.py
# --- SELF-LEARNING TRADE INTELLIGENCE SYSTEM ---
# Makes the bot smarter by learning from its own trading patterns in real-time

import logging
import json
import os
import numpy as np
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass, asdict, field

import config
from core.redis_client import RedisClient

TRADE_INTELLIGENCE_FILE = os.path.join(config.DATA_DIR, "trade_intelligence.json")


def _convert_to_native(obj):
    """Convert numpy types to native Python types for JSON serialization"""
    if isinstance(obj, dict):
        return {k: _convert_to_native(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_convert_to_native(item) for item in obj]
    elif isinstance(obj, (np.integer, np.int64, np.int32)):
        return int(obj)
    elif isinstance(obj, (np.floating, np.float64, np.float32)):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, np.bool_):
        return bool(obj)
    return obj


@dataclass
class CompletedTrade:
    """Record of a completed round-trip trade"""

    symbol: str
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    qty: float
    side: str  # 'long' or 'short'
    pnl: float
    pnl_pct: float
    hold_duration_hours: float
    entry_confidence: float = 0.0
    exit_reason: str = ""  # 'signal', 'stop_loss', 'trailing_stop', 'swap'

    # Market context at entry
    entry_rsi: float = 50.0
    entry_adx: float = 20.0
    entry_vix: float = 20.0

    # Agent attribution
    round_table_scores: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["entry_time"] = self.entry_time.isoformat()
        d["exit_time"] = self.exit_time.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "CompletedTrade":
        d["entry_time"] = datetime.fromisoformat(d["entry_time"])
        d["exit_time"] = datetime.fromisoformat(d["exit_time"])
        return cls(**d)


@dataclass
class OpenPosition:
    """Track an open position for outcome measurement"""

    symbol: str
    entry_time: datetime
    entry_price: float
    qty: float
    entry_confidence: float = 0.0
    entry_rsi: float = 50.0
    entry_adx: float = 20.0
    entry_vix: float = 20.0

    # Agent attribution
    round_table_scores: Dict[str, float] = field(default_factory=dict)


@dataclass
class SymbolIntelligence:
    """Intelligence gathered about trading a specific symbol"""

    symbol: str
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    avg_hold_hours: float = 0.0

    # Pattern detection
    churn_count: int = 0  # Trades held < 1 hour
    quick_losses: int = 0  # Losses on trades < 2 hours
    best_trade_pnl: float = 0.0
    worst_trade_pnl: float = 0.0

    # Adaptive confidence threshold
    # If we keep losing on this symbol, require higher confidence
    confidence_adjustment: float = 0.0  # Added to required confidence threshold

    @property
    def win_rate(self) -> float:
        return self.winning_trades / max(1, self.total_trades)

    @property
    def profit_factor(self) -> float:
        total_wins = self.avg_win * self.winning_trades
        total_losses = abs(self.avg_loss) * self.losing_trades
        return total_wins / max(1, total_losses)


class TradeIntelligence:
    """
    Self-Learning Trade Intelligence System

    Learns from the bot's own trading history to:
    1. Detect churning patterns (rapid round-trips that lose money)
    2. Identify which signals lead to winning vs losing trades
    3. Dynamically adjust confidence thresholds per symbol
    4. Recognize time-of-day or market condition patterns
    5. Provide real-time feedback on trade quality
    """

    def __init__(self, data_file: str = TRADE_INTELLIGENCE_FILE):
        self.data_file = data_file

        # Trade tracking
        self._open_positions: Dict[str, OpenPosition] = {}
        self._completed_trades: List[CompletedTrade] = []
        self._symbol_intelligence: Dict[str, SymbolIntelligence] = {}

        # Session stats (reset daily)
        self._session_start = datetime.now()
        self._session_trades = 0
        self._session_pnl = 0.0
        self._session_churn_alerts = 0

        # Learning thresholds
        self._base_confidence_threshold = 0.5  # Base required confidence
        self._churn_threshold_hours = 1.0  # Trades < 1hr = churn
        self._recent_window_hours = 24  # Look at last 24 hours for patterns

        # === SELF-TUNING PARAMETERS ===
        # These adjust automatically based on trading performance
        self._adaptive_min_hold_hours = 4.0  # Current minimum hold (can increase)
        self._adaptive_sell_bypass_threshold = (
            8  # Current bypass threshold (can increase)
        )
        self._last_tuning_check = datetime.now()
        self._tuning_check_interval_hours = 4  # Re-evaluate every 4 hours

        # Load historical data
        self._load_data()

        # Run initial self-tuning based on loaded history
        self._self_tune_parameters()

        logging.info(
            f"🧠 Trade Intelligence initialized: {len(self._completed_trades)} historical trades loaded"
        )

    def _load_data(self):
        """Load trade intelligence from Redis"""
        try:
            r = RedisClient.get_sync_redis()
            data_str = r.get("trade_intelligence_data")
            if data_str:
                data = json.loads(data_str)

                self._completed_trades = [
                    CompletedTrade.from_dict(t)
                    for t in data.get("completed_trades", [])
                ]

                for sym, intel_dict in data.get("symbol_intelligence", {}).items():
                    self._symbol_intelligence[sym] = SymbolIntelligence(**intel_dict)

                logging.info(
                    f"📊 Loaded intelligence for {len(self._symbol_intelligence)} symbols from Redis"
                )
            else:
                logging.info(
                    "No trade intelligence found in Redis (new start or flushed)."
                )
        except Exception as e:
            logging.warning("Could not load trade intelligence from Redis: %s", e)

    def _save_data(self):
        """Persist trade intelligence to Redis (called on every record_entry and record_exit)."""
        try:
            recent_trades = (
                self._completed_trades[-500:]
                if len(self._completed_trades) > 500
                else self._completed_trades
            )
            data = {
                "completed_trades": [t.to_dict() for t in recent_trades],
                "symbol_intelligence": {
                    sym: asdict(intel)
                    for sym, intel in self._symbol_intelligence.items()
                },
                "last_updated": datetime.now().isoformat(),
            }
            data = _convert_to_native(data)
            data_str = json.dumps(data)

            r = RedisClient.get_sync_redis()
            r.set("trade_intelligence_data", data_str)
        except Exception as e:
            logging.warning("Could not save trade intelligence to Redis: %s", e)

    # ==================== POSITION TRACKING ====================

    def record_entry(
        self,
        symbol: str,
        entry_price: float,
        qty: float,
        confidence: float = 0.0,
        features: Optional[Dict] = None,
        market_data: Optional[Dict] = None,
        round_table_scores: Optional[Dict[str, float]] = None,
    ):
        """Record a new position entry"""
        now = datetime.now()

        pos = OpenPosition(
            symbol=symbol,
            entry_time=now,
            entry_price=entry_price,
            qty=qty,
            entry_confidence=confidence,
            round_table_scores=round_table_scores or {},
        )

        if features:
            pos.entry_rsi = features.get("rsi_14", 50.0)
            pos.entry_adx = features.get("adx_14", 20.0)

        if market_data:
            pos.entry_vix = market_data.get("vix", 20.0)

        self._open_positions[symbol] = pos
        self._save_data()  # Persist so trade_intelligence.json updates on every entry (not only on exit)
        logging.debug(
            f"🧠 [INTEL] Entry recorded: {symbol} @ ${entry_price:.2f} (conf={confidence:.2f})"
        )

    def record_exit(
        self, symbol: str, exit_price: float, exit_reason: str = "signal"
    ) -> Optional[CompletedTrade]:
        """Record position exit and calculate trade outcome"""
        if symbol not in self._open_positions:
            logging.warning(
                f"🧠 [INTEL] Exit recorded for {symbol} but no open position tracked"
            )
            return None

        pos = self._open_positions.pop(symbol)
        now = datetime.now()

        # Calculate trade metrics
        pnl = (exit_price - pos.entry_price) * pos.qty
        pnl_pct = ((exit_price - pos.entry_price) / pos.entry_price) * 100
        hold_hours = (now - pos.entry_time).total_seconds() / 3600

        trade = CompletedTrade(
            symbol=symbol,
            entry_time=pos.entry_time,
            exit_time=now,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            qty=pos.qty,
            side="long",
            pnl=pnl,
            pnl_pct=pnl_pct,
            hold_duration_hours=hold_hours,
            entry_confidence=pos.entry_confidence,
            exit_reason=exit_reason,
            entry_rsi=pos.entry_rsi,
            entry_adx=pos.entry_adx,
            entry_vix=pos.entry_vix,
            round_table_scores=pos.round_table_scores,
        )

        self._completed_trades.append(trade)
        self._session_trades += 1
        self._session_pnl += pnl

        # Update symbol intelligence
        self._update_symbol_intelligence(trade)

        # Detect patterns
        self._detect_patterns(trade)

        # Credit/Blame RoundTable Agents
        self._attribute_trade_to_agents(trade)

        # Persist
        self._save_data()

        logging.info(
            f"🧠 [INTEL] Trade completed: {symbol} | P&L: ${pnl:+.2f} ({pnl_pct:+.2f}%) | Held: {hold_hours:.1f}h | Reason: {exit_reason}"
        )

        return trade

    def _update_symbol_intelligence(self, trade: CompletedTrade):
        """Update symbol-level intelligence from completed trade"""
        sym = trade.symbol

        if sym not in self._symbol_intelligence:
            self._symbol_intelligence[sym] = SymbolIntelligence(symbol=sym)

        intel = self._symbol_intelligence[sym]
        intel.total_trades += 1
        intel.total_pnl += trade.pnl

        if trade.pnl > 0:
            intel.winning_trades += 1
            # Running average of wins
            intel.avg_win = (
                (intel.avg_win * (intel.winning_trades - 1)) + trade.pnl
            ) / intel.winning_trades
        else:
            intel.losing_trades += 1
            intel.avg_loss = (
                (intel.avg_loss * (intel.losing_trades - 1)) + trade.pnl
            ) / intel.losing_trades

        # Update hold duration average
        intel.avg_hold_hours = (
            (intel.avg_hold_hours * (intel.total_trades - 1))
            + trade.hold_duration_hours
        ) / intel.total_trades

        # Track best/worst
        intel.best_trade_pnl = max(intel.best_trade_pnl, trade.pnl)
        intel.worst_trade_pnl = min(intel.worst_trade_pnl, trade.pnl)

        # Detect churn
        if trade.hold_duration_hours < self._churn_threshold_hours:
            intel.churn_count += 1

        if trade.hold_duration_hours < 2.0 and trade.pnl < 0:
            intel.quick_losses += 1

        # ADAPTIVE CONFIDENCE: Adjust required confidence based on recent performance
        self._calculate_confidence_adjustment(intel)

    def _calculate_confidence_adjustment(self, intel: SymbolIntelligence):
        """
        Dynamically adjust confidence threshold for symbol based on performance.

        - If losing consistently: RAISE the bar (require higher confidence)
        - If winning consistently: LOWER the bar (trust the signals more)
        - FORGIVENESS: Old losses matter less, time heals
        - REMATCH: Don't punish forever - give symbols second chances
        """
        # Look at recent trades for this symbol (only last 20, not 50 - shorter memory)
        recent_trades = [
            t for t in self._completed_trades[-30:] if t.symbol == intel.symbol
        ]

        # FORGIVENESS RULE 1: If no trades in last 30, start fresh
        if len(recent_trades) < 2:
            intel.confidence_adjustment = 0.0
            return

        # FORGIVENESS RULE 2: Apply time decay - trades older than 24h matter less
        now = datetime.now()
        weighted_wins = 0.0
        weighted_total = 0.0
        weighted_churn = 0.0

        for trade in recent_trades:
            hours_ago = (now - trade.exit_time).total_seconds() / 3600
            # Decay: Recent trades weight 1.0, 24h ago = 0.5, 48h ago = 0.25
            decay_weight = max(0.2, 1.0 / (1 + hours_ago / 24))

            weighted_total += decay_weight
            if trade.pnl > 0:
                weighted_wins += decay_weight
            if trade.hold_duration_hours < 1.0:
                weighted_churn += decay_weight

        recent_win_rate = weighted_wins / max(0.1, weighted_total)
        churn_rate = weighted_churn / max(0.1, weighted_total)

        adjustment = 0.0

        # REMATCH RULE: Check if last trade was profitable - reset penalty
        if recent_trades and recent_trades[-1].pnl > 0:
            # Last trade was a win - halve any existing penalty
            adjustment = intel.confidence_adjustment * 0.5
        else:
            # Winning signals
            if recent_win_rate > 0.6:  # Lowered from 0.7 - more forgiving
                adjustment -= 0.1  # Trust signals more
            elif recent_win_rate < 0.25:  # Very bad, but raised floor
                adjustment += 0.2  # Reduced from 0.3 - less punishing
            elif recent_win_rate < 0.4:
                adjustment += 0.1  # Reduced from 0.15

            # Churn penalty (also reduced)
            if churn_rate > 0.6:
                adjustment += 0.15  # Heavy penalty for churning (was 0.2)
            elif churn_rate > 0.4:
                adjustment += 0.05  # Was 0.1

        # FORGIVENESS CAP: Max penalty is 0.35 (was 0.5) - always leave door open
        intel.confidence_adjustment = min(0.35, max(-0.25, adjustment))

    # ==================== AGENT ATTRIBUTION ====================
    def _attribute_trade_to_agents(self, trade: CompletedTrade):
        """
        Assigns Trust Scores to RoundTable agents retroactively.
        If trade.pnl > 0 -> Agents voting > 0.65 get +, Agents < 0.35 get -.
        If trade.pnl < 0 -> Agents voting > 0.65 get -, Agents < 0.35 get +.
        Saves the trust scores to Redis under `agent_trust_scores`.
        """
        if not trade.round_table_scores:
            return  # No attribution if no scores stored at entry

        try:
            r = RedisClient.get_sync_redis()
            if not r:
                return

            raw_scores = r.get("agent_trust_scores")
            trust_scores = json.loads(raw_scores) if raw_scores else {}

            # Simple attribution multiplier
            multiplier = 1.0 if trade.pnl > 0 else -1.0

            for agent, score in trade.round_table_scores.items():
                if agent not in trust_scores:
                    trust_scores[agent] = 0.0

                # Did agent vote strongly BUY?
                if score >= 0.65:
                    trust_scores[agent] += 1.0 * multiplier
                # Did agent vote strongly SELL?
                elif score <= 0.35:
                    trust_scores[agent] -= 1.0 * multiplier

            r.set("agent_trust_scores", json.dumps(trust_scores))
        except Exception as e:
            logging.debug(f"Failed to attribute trade to agents: {e}")

    # ==================== PATTERN DETECTION ====================

    def _detect_patterns(self, trade: CompletedTrade):
        """Detect problematic patterns and log warnings"""
        warnings = []

        # Pattern 1: CHURN - Rapid round-trip with loss
        if trade.hold_duration_hours < 1.0 and trade.pnl < 0:
            warnings.append(
                f"⚠️ CHURN DETECTED: {trade.symbol} held only {trade.hold_duration_hours:.1f}h for ${trade.pnl:.2f} loss"
            )
            self._session_churn_alerts += 1

        # Pattern 2: REPEATED LOSSES - Same symbol losing multiple times recently
        recent_symbol_trades = [
            t for t in self._completed_trades[-20:] if t.symbol == trade.symbol
        ]
        if len(recent_symbol_trades) >= 3:
            recent_losses = sum(1 for t in recent_symbol_trades[-3:] if t.pnl < 0)
            if recent_losses == 3:
                warnings.append(
                    f"⚠️ 3 CONSECUTIVE LOSSES on {trade.symbol}! Consider avoiding this symbol."
                )

        # Pattern 3: SAME-DAY ROUND TRIP - Buy and sell same day multiple times
        today = datetime.now().date()
        today_symbol_trades = [
            t
            for t in self._completed_trades
            if t.exit_time.date() == today and t.symbol == trade.symbol
        ]
        if len(today_symbol_trades) >= 3:
            warnings.append(
                f"⚠️ EXCESSIVE TRADING: {len(today_symbol_trades)} trades on {trade.symbol} today!"
            )

        # Pattern 4: LOW CONFIDENCE TRADE LOST - Model wasn't sure and trade failed
        if trade.entry_confidence < 1.0 and trade.pnl < -50:
            warnings.append(
                f"⚠️ LOW CONFIDENCE LOSS: {trade.symbol} entered at conf={trade.entry_confidence:.2f}, lost ${abs(trade.pnl):.2f}"
            )

        # Log all warnings
        for warning in warnings:
            logging.warning("🧠 [INTEL] %s", warning)

        # Check if we should re-evaluate self-tuning parameters
        self._maybe_self_tune()

        # Periodically apply forgiveness
        self._maybe_apply_forgiveness()

    # ==================== FORGIVENESS SYSTEM ====================

    def _maybe_apply_forgiveness(self):
        """
        Periodically decay all confidence adjustments to give symbols fresh chances.
        This prevents 'traumatization' where one bad day blocks a symbol forever.
        """
        now = datetime.now()

        # Check every 2 hours
        if not hasattr(self, "_last_forgiveness_check"):
            self._last_forgiveness_check = now

        hours_since_check = (now - self._last_forgiveness_check).total_seconds() / 3600
        if hours_since_check < 2:
            return

        self._last_forgiveness_check = now

        # Apply forgiveness: Decay all penalties by 20%
        symbols_forgiven = 0
        for sym, intel in self._symbol_intelligence.items():
            if intel.confidence_adjustment > 0:
                old_adj = intel.confidence_adjustment
                intel.confidence_adjustment *= 0.8  # 20% forgiveness
                if intel.confidence_adjustment < 0.05:  # Nearly zero, reset completely
                    intel.confidence_adjustment = 0.0
                if intel.confidence_adjustment != old_adj:
                    symbols_forgiven += 1

        if symbols_forgiven > 0:
            logging.info(
                f"🧠 [INTEL] FORGIVENESS: Applied time-decay to {symbols_forgiven} symbols"
            )
            self._save_data()

    def reset_symbol_intelligence(self, symbol: str):
        """Manually reset intelligence for a symbol - give it a completely fresh start"""
        if symbol in self._symbol_intelligence:
            self._symbol_intelligence[symbol].confidence_adjustment = 0.0
            self._symbol_intelligence[symbol].churn_count = 0
            self._symbol_intelligence[symbol].quick_losses = 0
            logging.info("🧠 [INTEL] Reset intelligence for %s - fresh start!", symbol)
            self._save_data()

    def reset_all_penalties(self):
        """Reset all confidence penalties - nuclear option for fresh start"""
        for sym, intel in self._symbol_intelligence.items():
            intel.confidence_adjustment = 0.0
        logging.info(
            f"🧠 [INTEL] Reset all penalties for {len(self._symbol_intelligence)} symbols!"
        )
        self._save_data()

    # ==================== SELF-TUNING PARAMETERS ====================

    def _self_tune_parameters(self):
        """
        Automatically adjust trading parameters based on learned performance.

        If churning + losing → increase hold time and bypass threshold
        If performing well → can relax slightly
        """
        if len(self._completed_trades) < 10:
            return  # Need more data

        # Analyze recent trades (last 50 or all if less)
        recent = self._completed_trades[-50:]

        # Calculate metrics
        total_trades = len(recent)
        churn_trades = sum(1 for t in recent if t.hold_duration_hours < 1.0)
        losing_trades = sum(1 for t in recent if t.pnl < 0)
        churn_losses = sum(
            1 for t in recent if t.hold_duration_hours < 1.0 and t.pnl < 0
        )
        total_pnl = sum(t.pnl for t in recent)

        churn_rate = churn_trades / total_trades
        loss_rate = losing_trades / total_trades

        old_hold = self._adaptive_min_hold_hours
        old_bypass = self._adaptive_sell_bypass_threshold

        # === RULE 1: High churn rate + losing = increase hold time ===
        if churn_rate > 0.5 and total_pnl < 0:
            # Churning badly - increase hold time
            self._adaptive_min_hold_hours = min(
                self._adaptive_min_hold_hours + 0.5, 8.0
            )
            logging.info(
                f"🧠 [SELF-TUNE] High churn ({churn_rate:.0%}) + losing → hold time: {old_hold}h → {self._adaptive_min_hold_hours}h"
            )

        # === RULE 2: Churn losses are primary loss source = increase bypass threshold ===
        if churn_losses > 0 and total_pnl < 0:
            churn_loss_pct = churn_losses / max(1, losing_trades)
            if churn_loss_pct > 0.5:
                # Most losses come from churns - make it harder to bypass hold
                self._adaptive_sell_bypass_threshold = min(
                    self._adaptive_sell_bypass_threshold + 1, 12
                )
                logging.info(
                    f"🧠 [SELF-TUNE] Churn losses ({churn_loss_pct:.0%} of losses) → bypass threshold: {old_bypass} → {self._adaptive_sell_bypass_threshold}"
                )

        # === RULE 3: Profitable with low churn = can relax slightly ===
        if total_pnl > 500 and churn_rate < 0.2 and loss_rate < 0.4:
            # Doing well - can slightly relax (but never below baseline)
            self._adaptive_min_hold_hours = max(
                self._adaptive_min_hold_hours - 0.25, 4.0
            )
            self._adaptive_sell_bypass_threshold = max(
                self._adaptive_sell_bypass_threshold - 1, 8
            )
            if (
                old_hold != self._adaptive_min_hold_hours
                or old_bypass != self._adaptive_sell_bypass_threshold
            ):
                logging.info(
                    f"🧠 [SELF-TUNE] Profitable + low churn → relaxing slightly"
                )

        # Apply to portfolio manager if available
        self._apply_tuned_parameters()

    def _maybe_self_tune(self):
        """Check if it's time to re-run self-tuning"""
        now = datetime.now()
        hours_since_tune = (now - self._last_tuning_check).total_seconds() / 3600

        if hours_since_tune >= self._tuning_check_interval_hours:
            self._last_tuning_check = now
            self._self_tune_parameters()

    def _apply_tuned_parameters(self):
        """Apply self-tuned parameters to portfolio manager"""
        try:
            # Import portfolio manager and update its settings
            import strategies

            # If strategy has a portfolio manager, update its params
            if hasattr(strategies, "_rl_strategy") and strategies._rl_strategy:
                pm = strategies._rl_strategy.portfolio_manager
                if pm:
                    pm._min_hold_hours = self._adaptive_min_hold_hours
                    pm._consecutive_sell_threshold = (
                        self._adaptive_sell_bypass_threshold
                    )
                    logging.debug(
                        f"🧠 [SELF-TUNE] Applied to portfolio manager: hold={self._adaptive_min_hold_hours}h, bypass={self._adaptive_sell_bypass_threshold}"
                    )
        except Exception as e:
            logging.debug("Could not apply tuned parameters: %s", e)

    def get_tuned_parameters(self) -> Dict[str, float]:
        """Get current self-tuned parameters for external use"""
        return {
            "min_hold_hours": self._adaptive_min_hold_hours,
            "sell_bypass_threshold": self._adaptive_sell_bypass_threshold,
            "base_confidence": self._base_confidence_threshold,
        }

    # ==================== SMART SIGNAL FILTERING ====================

    def should_trade(
        self,
        symbol: str,
        confidence: float,
        signal: str,
        features: Optional[Dict] = None,
    ) -> Tuple[bool, str]:
        """
        Intelligent pre-trade check - NOW ADVISORY ONLY, RARELY BLOCKS

        Philosophy: Let the LSTM+RL signal logic decide. Intelligence only blocks
        in EXTREME cases (massive consecutive losses or ridiculous churn).

        Returns: (should_trade, reason)
        """
        intel = self._symbol_intelligence.get(symbol)
        now = datetime.now()

        # === EXTREME CHURN ONLY - Block if traded 5+ times in last hour ===
        recent_symbol_trades = [
            t
            for t in self._completed_trades
            if t.symbol == symbol and (now - t.exit_time).total_seconds() < 3600
        ]
        if len(recent_symbol_trades) >= 5:  # 5+ trades in 1 hour = extreme churn
            return (
                False,
                f"Extreme churn: {len(recent_symbol_trades)} trades on {symbol} in last hour - cooling down",
            )

        # === EXTREME LOSS STREAK ONLY - 6+ consecutive losses ===
        if intel and intel.total_trades >= 10:
            recent = [t for t in self._completed_trades[-30:] if t.symbol == symbol][
                -6:
            ]
            if len(recent) == 6 and all(t.pnl < 0 for t in recent):
                # 6 consecutive losses on a well-traded symbol - pause briefly
                if confidence < 2.5:  # Only block if LSTM isn't super confident
                    return (
                        False,
                        f"6 consecutive losses on {symbol} - requiring very high confidence (got {confidence:.2f})",
                    )

        # === CATASTROPHIC WIN RATE ONLY - <10% over 20+ trades ===
        if intel and intel.total_trades >= 20 and intel.win_rate < 0.10:
            if confidence < 2.0:
                return (
                    False,
                    f"{symbol} has catastrophic {intel.win_rate:.0%} win rate over {intel.total_trades} trades",
                )

        # === EVERYTHING ELSE: APPROVED ===
        # The LSTM+RL signal stabilization already filters well
        # Don't second-guess it with confidence thresholds
        return True, "Trade approved (Intelligence now advisory-only)"

    def get_entry_insight(
        self, symbol: str, confidence: float, features: Optional[Dict] = None
    ) -> str:
        """Get human-readable insight about this potential trade"""
        intel = self._symbol_intelligence.get(symbol)

        insights = []

        if intel:
            insights.append(
                f"History: {intel.total_trades} trades, {intel.win_rate:.0%} win rate"
            )

            if intel.total_pnl > 0:
                insights.append(f"Profitable: ${intel.total_pnl:.2f} total")
            elif intel.total_pnl < -100:
                insights.append(f"⚠️ Losing: ${intel.total_pnl:.2f} total")

            if intel.confidence_adjustment > 0.1:
                insights.append(
                    f"⚠️ Confidence bar raised by +{intel.confidence_adjustment:.2f}"
                )
            elif intel.confidence_adjustment < -0.1:
                insights.append(
                    f"✅ Confidence bar lowered by {intel.confidence_adjustment:.2f}"
                )

            if intel.churn_count > intel.total_trades * 0.3:
                insights.append(
                    f"⚠️ High churn rate ({intel.churn_count}/{intel.total_trades} trades < 1h)"
                )
        else:
            insights.append("First trade on this symbol")

        return " | ".join(insights) if insights else "No historical data"

    # ==================== REPORTING ====================

    def get_session_stats(self) -> Dict[str, Any]:
        """Get current session statistics"""
        session_hours = (datetime.now() - self._session_start).total_seconds() / 3600

        return {
            "session_start": self._session_start.isoformat(),
            "session_hours": round(session_hours, 2),
            "trades": self._session_trades,
            "pnl": round(self._session_pnl, 2),
            "churn_alerts": self._session_churn_alerts,
            "trades_per_hour": round(self._session_trades / max(0.1, session_hours), 2),
        }

    def get_symbol_report(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get detailed report for a specific symbol"""
        intel = self._symbol_intelligence.get(symbol)
        if not intel:
            return None

        return {
            "symbol": symbol,
            "total_trades": intel.total_trades,
            "win_rate": f"{intel.win_rate:.1%}",
            "profit_factor": round(intel.profit_factor, 2),
            "total_pnl": round(intel.total_pnl, 2),
            "avg_win": round(intel.avg_win, 2),
            "avg_loss": round(intel.avg_loss, 2),
            "avg_hold_hours": round(intel.avg_hold_hours, 2),
            "churn_count": intel.churn_count,
            "quick_losses": intel.quick_losses,
            "confidence_adjustment": f"{intel.confidence_adjustment:+.2f}",
            "best_trade": round(intel.best_trade_pnl, 2),
            "worst_trade": round(intel.worst_trade_pnl, 2),
        }

    def get_top_performers(self, n: int = 5) -> List[Dict]:
        """Get top N performing symbols"""
        sorted_intel = sorted(
            self._symbol_intelligence.values(), key=lambda x: x.total_pnl, reverse=True
        )
        return [self.get_symbol_report(i.symbol) for i in sorted_intel[:n]]

    def get_worst_performers(self, n: int = 5) -> List[Dict]:
        """Get worst N performing symbols"""
        sorted_intel = sorted(
            self._symbol_intelligence.values(), key=lambda x: x.total_pnl
        )
        return [self.get_symbol_report(i.symbol) for i in sorted_intel[:n]]

    def get_learning_summary(self) -> str:
        """Get a human-readable summary of what the bot has learned"""
        if not self._symbol_intelligence:
            return "No trading history yet - still learning."

        total_trades = sum(i.total_trades for i in self._symbol_intelligence.values())
        total_pnl = sum(i.total_pnl for i in self._symbol_intelligence.values())
        total_churn = sum(i.churn_count for i in self._symbol_intelligence.values())

        lines = [
            f"📊 TRADE INTELLIGENCE SUMMARY",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"Total Trades Analyzed: {total_trades}",
            f"Total P&L: ${total_pnl:+,.2f}",
            f"Churn Trades (< 1h): {total_churn} ({total_churn / max(1, total_trades):.1%})",
            "",
        ]

        # Symbols with raised confidence thresholds
        raised = [
            s
            for s in self._symbol_intelligence.values()
            if s.confidence_adjustment > 0.1
        ]
        if raised:
            lines.append("⚠️ CAUTION SYMBOLS (confidence raised):")
            for s in raised[:5]:
                lines.append(
                    f"   {s.symbol}: {s.win_rate:.0%} win rate, conf +{s.confidence_adjustment:.2f}"
                )
            lines.append("")

        # Best performers
        best = sorted(
            self._symbol_intelligence.values(), key=lambda x: x.total_pnl, reverse=True
        )[:3]
        if best and best[0].total_pnl > 0:
            lines.append("✅ TOP PERFORMERS:")
            for s in best:
                if s.total_pnl > 0:
                    lines.append(
                        f"   {s.symbol}: ${s.total_pnl:+.2f} ({s.win_rate:.0%} win rate)"
                    )
            lines.append("")

        # Worst performers
        worst = sorted(self._symbol_intelligence.values(), key=lambda x: x.total_pnl)[
            :3
        ]
        if worst and worst[0].total_pnl < -50:
            lines.append("❌ WORST PERFORMERS:")
            for s in worst:
                if s.total_pnl < 0:
                    lines.append(
                        f"   {s.symbol}: ${s.total_pnl:+.2f} ({s.win_rate:.0%} win rate)"
                    )

        return "\n".join(lines)


# Singleton instance
_trade_intelligence: Optional[TradeIntelligence] = None


def get_trade_intelligence() -> TradeIntelligence:
    """Get or create singleton TradeIntelligence instance"""
    global _trade_intelligence
    if _trade_intelligence is None:
        _trade_intelligence = TradeIntelligence()
    return _trade_intelligence
