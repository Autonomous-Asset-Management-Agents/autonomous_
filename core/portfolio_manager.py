# portfolio_manager.py
# --- SMART PORTFOLIO MANAGEMENT: Self-Aware Position Comparison & Intelligent Rebalancing ---

import logging
import threading
from datetime import datetime
from typing import Dict, List, Optional

from core.portfolio_bericht import BerichtMixin
from core.portfolio_bestand import (  # noqa: F401 — Re-Export (#4187 §3)
    BestandMixin,
    _holding_period_cfg,
    holding_period_score,
)
from core.portfolio_handelsbuch import (  # noqa: F401 — Re-Export (#4187 §3)
    HandelsbuchMixin,
    _trading_day_et,
)
from core.portfolio_typen import (  # noqa: F401 — Re-Export (#4187 §3)
    OpportunityScore,
    PositionScore,
    _book_cap_enforced,
    _ensure_aware_utc,
    _now_utc,
)
from core.portfolio_verdraengung import VerdraengungMixin
from core.portfolio_zulassung import ZulassungMixin
from core.protocols import BrokerClientProtocol

# ADR-R18 (#3963): relative top-up dead-band — see settings.POSITION_TOPUP_DEAD_BAND_REL.
_TOPUP_BAND_REL_DEFAULT = 0.25
_TOPUP_BAND_REL_MAX = 0.9


def _valid_topup_band_rel(value) -> float:
    """The configured relative dead-band, or the default (WARNING, §5.6) when it is not a
    number in [0, 0.9]. 0 disables the band (top-ups are then bounded by the gap alone).
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = float("nan")
    if v != v or v < 0.0 or v > _TOPUP_BAND_REL_MAX:
        logging.warning(
            "POSITION_TOPUP_DEAD_BAND_REL=%r outside [0, %.1f] — using %.2f (#3963).",
            value,
            _TOPUP_BAND_REL_MAX,
            _TOPUP_BAND_REL_DEFAULT,
        )
        return _TOPUP_BAND_REL_DEFAULT
    return v


class PortfolioManager(
    BestandMixin, VerdraengungMixin, ZulassungMixin, HandelsbuchMixin, BerichtMixin
):
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
        # #2886: broker-account identity seen at the last refresh. A CHANGED id means the
        # client now points at a DIFFERENT account (the 14.08. switch) — internal state
        # (positions, trade history, convictions) belongs to the OLD account and must not
        # steer the new one. None until first read; flag-gated in refresh_positions.
        self._seen_account_id: Optional[str] = None
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
                POSITION_TOPUP_DEAD_BAND_REL,
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
            self._topup_dead_band_rel = _valid_topup_band_rel(
                POSITION_TOPUP_DEAD_BAND_REL
            )
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
            self._topup_dead_band_rel = _TOPUP_BAND_REL_DEFAULT
            self._min_position_pct = 0.05
        # Per-symbol EWMA state for conviction smoothing (0-1). Reset on restart (no Redis on desktop).
        self._conviction_ewma: Dict[str, float] = {}
        # #3180: per-symbol LIVE round-table blend-consensus (0-1), written each cycle by the harvest
        # seam for HELD symbols; read by the consensus-retention gate. In-memory, cleared on flat.
        self._live_consensus: Dict[str, float] = {}

        # Rebalancing controls
        self._last_rebalance: Dict[str, datetime] = {}  # {symbol: last_rebalance_time}

        # Trade history for churn prevention
        self._trade_history: Dict[str, List[datetime]] = {}  # {symbol: [trade_times]}
        # #3291: per-symbol CURRENT-lot entry clock (dark, ROTATION_MIN_HOLD_CURRENT_LOT).
        # `days_held` must reflect the current holding, but `min(_trade_history)` dates it to
        # the OLDEST buy in the 30-day window — so a name re-entered within 30 days (sell→rebuy)
        # looked 20+ days old and the rotation min-hold churned the fresh lot (live 2026-09-08).
        # Stamped on a WITNESSED absent→present entry, reset on full exit. `_seen_refresh` gates
        # the first refresh (rehydration) out, so only real re-entries get the fresh clock.
        self._position_opened_at: Dict[str, datetime] = {}
        self._seen_refresh: bool = False
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
