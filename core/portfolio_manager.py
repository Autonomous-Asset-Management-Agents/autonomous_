# portfolio_manager.py
# --- SMART PORTFOLIO MANAGEMENT: Self-Aware Position Comparison & Intelligent Rebalancing ---

import logging
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import pytz

from core.governance.portfolio_constraints import load_approved_constraints
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


# #2947 — Konstanten der Kandidaten-Bewertung. Sie stehen hier und nicht als Literale in
# score_opportunity, weil beide einen Rechenfehler markieren, der genau daran lag, dass niemand
# die Zahl neben ihrer Bedeutung sah.
#
# ADR-SCORE-01: Summe der vier Komponentengewichte in score_opportunity (0,25 + 0,20 + 0,20
# + 0,25). Der Positions-Score summiert auf 1,00; beide wurden gegen dieselbe Schwelle
# voneinander abgezogen. Aendert sich eines der Gewichte, MUSS diese Zahl mitwandern — der
# Paritaetstest in tests/unit/test_opportunity_score_arithmetic.py haelt das fest.
_OPPORTUNITY_WEIGHT_SUM = 0.90

# ADR-SCORE-02: Punkte je Einheit Modellvertrauen. Der Eingang ist die LSTM-Vorhersage im
# Bereich [-1, +1] (Schwellen im Code bei +/-0,6). 50 Punkte je Einheit ergeben die im
# Ursprungskommentar beabsichtigte Spreizung von +/-50 Punkten um den Neutralwert 50.
_CONFIDENCE_POINTS_PER_UNIT = 50.0

# Der historische Faktor: geschrieben fuer einen Eingang in [-5, +5], der nie geliefert wurde.
# Nur noch fuer den Rueckfall (OPPORTUNITY_SCORE_NORMALIZED=false).
_CONFIDENCE_POINTS_PER_UNIT_LEGACY = 10.0

# Das historische Gewicht des RL-Agenten im Round Table. Bezugsgroesse fuer die Skalierung des
# Aktions-Bonus: bei diesem Gewicht ist der Bonus der volle historische Betrag, bei 0,0 entfaellt er.
_RL_WEIGHT_NOMINAL = 0.40


def _opportunity_score_cfg() -> "tuple[bool, float]":
    """#2947: (behoben?, Skalierungsfaktor des Aktions-Bonus) fuer score_opportunity.

    Auf CALL-Zeit gelesen (CODING_POLICY §2.10), nie in eine Modulkonstante gefroren — dieselbe
    Begruendung wie bei `_displacement_min_hold_days`.

    Fail-SAFE in Richtung weniger Handel: laesst sich die Config nicht lesen, gilt die behobene
    Arithmetik OHNE Aktions-Bonus. Ein unlesbarer Schalter darf keinen Kauf beguenstigen.
    """
    try:
        from config import get_config

        cfg = get_config()
        normalized = bool(getattr(cfg, "OPPORTUNITY_SCORE_NORMALIZED", True))
        if not normalized:
            # Rueckfall: alte Arithmetik, Bonus unbedingt (Faktor 1,0) — byte-identisch.
            return False, 1.0
        weight = float(getattr(cfg, "RL_CONFIDENCE_WEIGHT", _RL_WEIGHT_NOMINAL) or 0.0)
        return True, max(0.0, min(1.0, weight / _RL_WEIGHT_NOMINAL))
    except Exception:  # noqa: BLE001 — fail-safe: behoben, ohne Bonus
        logging.warning(
            "PortfolioManager: could not resolve the opportunity-score config — using the "
            "corrected arithmetic WITHOUT the action bonus (fail-safe, #2947)."
        )
        return True, 0.0


def _holding_period_cfg() -> "tuple[float, float]":
    """#2940: (fresh_score, ramp_span_days) fuer holding_period_score.

    Auf CALL-Zeit gelesen, nie in eine Modulkonstante gefroren — genau der Fehler, durch den
    operator-gesetzte Werte in diesem Code schon inert geworden sind (siehe
    `_displacement_min_hold_days`). Default (30.0, 0.0) reproduziert die historischen Literale
    BYTE-IDENTISCH; der Sweep variiert sie pro Prozess.
    """
    try:
        from config import get_config

        cfg = get_config()
        fresh = float(getattr(cfg, "HOLDING_PERIOD_SCORE_FRESH", 30.0) or 30.0)
        span = float(getattr(cfg, "HOLDING_PERIOD_SCORE_SPAN_DAYS", 0.0) or 0.0)
        return fresh, span
    except Exception:  # noqa: BLE001 — fail-safe auf die historischen Werte
        return 30.0, 0.0


def holding_period_score(
    days_held: float, fresh: float = 30.0, span_days: float = 0.0
) -> float:
    """Punkte fuer die Haltedauer einer Position (0-100), Gewicht 20 % im Gesamtscore.

    **Warum konfigurierbar (#2940).** Die historischen Stufen 30/50/70/90 an den Grenzen
    1/3/7 Tagen stammen aus einer Zeit VOR der 20-Tage-Mindesthaltedauer. Zwei Folgen, beide
    gemessen: Der Score erreicht sein Maximum an Tag 7, obwohl die Strategie 20 Tage committet;
    und eine frische Position bekommt 30 Punkte, was sie mit 12 Punkten Abstand zum
    wahrscheinlichsten Verdraengungskandidaten macht (Live-Karussell 17./18.08., #2935) —
    obwohl der Kommentar hier "reward patience, penalize churning" verspricht.

    Ob eine andere Kurve besser ist, ist eine MESSFRAGE (Sweep), keine Reparatur. Deshalb nur
    eine Naht, kein neues Verhalten:

    * ``span_days == 0`` (Default): die historischen Stufen, byte-identisch.
    * ``span_days > 0``: linearer Anstieg von ``fresh`` auf 90 ueber ``span_days`` Tage —
      Reifung wird ueber die tatsaechliche Bindungsdauer verdient statt in 7 Tagen.
    * ``fresh``: der Wert fuer < 1 Tag. 50.0 druckt "unbekannt ist nicht schlecht" aus.

    Praezedenz fuer die Kopplung an die Betreiber-Vorgabe: #2696 hat die Literal-5 im
    "lange gehalten, wenig Gewinn"-Argument durch ``min_hold_days`` ersetzt. Diese Kurve ist
    die letzte Stelle mit Literalen aus der alten Ordnung.
    """
    d = max(0.0, float(days_held or 0.0))
    if span_days and span_days > 0:
        return fresh + (90.0 - fresh) * min(1.0, d / float(span_days))
    if d < 1:
        return float(fresh)
    if d < 3:
        return 50.0
    if d < 7:
        return 70.0
    return 90.0


def _book_cap_enforced() -> bool:
    """#2886: is the book-cap invariante armed? Read via get_config() at CALL time
    (CODING_POLICY §2.10) so tests and env can flip it; missing attr ⇒ False
    (dark by default — OFF is byte-identical legacy behaviour)."""
    try:
        from config import get_config

        return bool(getattr(get_config(), "BOOK_CAP_ENFORCEMENT_ENABLED", False))
    except Exception:  # noqa: BLE001 — a config failure must never crash the PM
        import logging

        logging.warning(
            "Failed to read BOOK_CAP_ENFORCEMENT_ENABLED config, falling back to False",
            exc_info=True,
        )
        return False


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
    # #2935: `days_held` ist ein int aus `.days` — 0 heisst SOWOHL "heute gekauft" ALS AUCH
    # "Alter unbekannt" (keine Handelshistorie). Der Verdraengungs-Riegel darf nur den zweiten
    # Fall fail-open behandeln, sonst rutschen ausgerechnet die frischesten Positionen durch
    # (Live-Karussell 17./18.08.). `age_known` trennt die beiden Faelle. Default False =
    # Alter unbekannt ⇒ jeder andere Erzeuger von PositionScore verhaelt sich byte-identisch.
    age_known: bool = False
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
                    # #2886 (flag-gated): account-identity tracking. A changed id ⇒ the
                    # internal state describes the PREVIOUS account — reset it before it
                    # can steer decisions on the new one (the 14.08. fail-open root).
                    if _book_cap_enforced():
                        acc_id = str(
                            getattr(acc, "account_number", None)
                            or getattr(acc, "id", None)
                            or ""
                        )
                        if acc_id:
                            if (
                                self._seen_account_id is not None
                                and acc_id != self._seen_account_id
                            ):
                                logging.warning(
                                    "[PortfolioManager] broker account changed "
                                    "(%s → %s) — resetting internal position state "
                                    "and rehydrating from the broker (#2886)",
                                    self._seen_account_id,
                                    acc_id,
                                )
                                self._position_scores = {}
                                self._position_history = {}
                                self._trade_history = {}
                            self._seen_account_id = acc_id
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

                # Calculate days held. #2935: `age_known` merkt sich, OB die Historie das
                # Alter belegt — nur dann darf die Haltefrist greifen bzw. fail-open entfallen.
                days_held = 0
                age_known = False
                if symbol in self._trade_history and self._trade_history[symbol]:
                    first_buy = min(
                        _ensure_aware_utc(t) for t in self._trade_history[symbol]
                    )
                    days_held = (
                        datetime.now(timezone.utc) - _ensure_aware_utc(first_buy)
                    ).days
                    age_known = True

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
                    age_known=age_known,
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
        # #2940: Kurve konfigurierbar; Default = die historischen Stufen 30/50/70/90 an den
        # Grenzen 1/3/7 Tagen, byte-identisch. Begruendung: holding_period_score.__doc__
        _hp_fresh, _hp_span = _holding_period_cfg()
        score.holding_period_score = holding_period_score(
            score.days_held, fresh=_hp_fresh, span_days=_hp_span
        )

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

        # #2947: zwei Rechenfehler, gemeinsam behoben (sie wirken gegeneinander, siehe
        # _opportunity_score_cfg). `normalized=False` reproduziert die alte Arithmetik
        # byte-identisch — der dokumentierte Rueckfall.
        normalized, rl_bonus_factor = _opportunity_score_cfg()

        # MODEL CONFIDENCE SCORE
        # #2947 (Fehler 2): der Faktor gehoert zum Wertebereich des EINGANGS. Der urspruengliche
        # Kommentar hier lautete "Confidence typically ranges from -5 to +5" — mit Faktor 10 also
        # eine beabsichtigte Spreizung von +/-50 Punkten. Geliefert wird aber die LSTM-Vorhersage
        # in +/-1 (Schwellen im Code bei +/-0,6, siehe order_executor.py:2103), womit die
        # Komponente trotz 25 % Gewicht nur +/-10 Punkte spannte: ueber die gesamte Bandbreite
        # 5 Punkte Gesamtscore. Der korrigierte Faktor stellt die beabsichtigte Spreizung her.
        conf_points = (
            _CONFIDENCE_POINTS_PER_UNIT
            if normalized
            else _CONFIDENCE_POINTS_PER_UNIT_LEGACY
        )
        conf_normalized = max(0, min(100, 50 + (model_confidence * conf_points)))

        # RL ACTION bonus
        # #2947 (Fehler 1): der Bonus wird mit dem Round-Table-Gewicht des RL-Agenten skaliert.
        # Begruendung: `rl_action=1` ist an beiden Live-Aufrufstellen ein Literal
        # (order_executor.py:1094, :2385), die Pauschale wurde also UNBEDINGT vergeben — und der
        # Agent, den sie vertritt, steht mit RL_CONFIDENCE_WEIGHT=0.0 in beiden Editionen
        # bewusst still (RTR-0: richtungsblindes Votum). Ueber das Gewicht gekoppelt entfaellt der
        # Bonus damit heute vollstaendig und kehrt bei einer Reaktivierung proportional zurueck,
        # ohne erneute Code-Aenderung. Der Log-Text nennt nur, was tatsaechlich votiert hat.
        action_bonus = 0.0
        if rl_action == 1:  # BUY signal
            action_bonus = 15.0 * rl_bonus_factor
            if action_bonus:
                opp.arguments_for.append("RL model says BUY")
        elif rl_action == 2:  # SELL signal
            action_bonus = -15.0 * rl_bonus_factor
            if action_bonus:
                opp.arguments_against.append("RL model says SELL (not BUY)")

        # TOTAL SCORE
        # #2947 (Fehler 1): die vier Gewichte summieren sich auf 0,90, waehrend der Positions-Score
        # auf 1,00 summiert — beide wurden gegen die Schwelle 15 voneinander abgezogen. Zusammen mit
        # der unbedingten Pauschale kuerzte sich die Marge vollstaendig weg:
        #     (Basis + 15) − Position > 15   <=>   Basis > Position
        # Die Division durch die Gewichtssumme normiert auf 1,00, ohne die relative Bedeutung der
        # Komponenten zu veraendern.
        weighted = (
            opp.value_score * 0.25
            + opp.trend_score * 0.20
            + opp.momentum_score * 0.20
            + conf_normalized * 0.25
        )
        if normalized:
            weighted /= _OPPORTUNITY_WEIGHT_SUM
        opp.total_score = weighted + action_bonus
        opp.total_score = max(0, min(100, opp.total_score))

        return opp

    def _displacement_min_hold_days(self) -> float:
        """Holding period that governs displacement, or 0.0 when it must not gate (#2696).

        Resolved at CALL time, never frozen into a module constant — that is how
        operator-set env values have gone inert in this codebase before. Returns 0.0 (no gate)
        when the flag is off or the config cannot be read, so the veto always fails OPEN.
        """
        try:
            from config import get_config  # local import — the module's own pattern

            cfg = get_config()
            if not getattr(cfg, "DISPLACEMENT_RESPECTS_MIN_HOLD", False):
                return 0.0
            return float(getattr(cfg, "SMART_EXIT_MIN_HOLD_DAYS", 0.0) or 0.0)
        except Exception:  # noqa: BLE001 — fail-open: never freeze the book over config
            logging.warning(
                "PortfolioManager: could not resolve the displacement holding period — "
                "displacement proceeds ungated this cycle (fail-open)."
            )
            return 0.0

    def debate_position_swap(
        self, opportunity: OpportunityScore, weakest_position: Optional[PositionScore]
    ) -> Tuple[bool, str]:
        """
        Self-debate: Should we swap the weakest position for this new opportunity?

        Returns: (should_swap, reasoning)
        """
        if weakest_position is None:
            return True, "No existing positions - proceed with new position"

        # ADR-R14 (#2696) — HOLDING-PERIOD VETO, checked BEFORE the debate.
        #
        # Displacement is the dominant exit path: on a 10-day replay it produced 13 of 24 SELLs,
        # against 8 from intelligent_exit trailing and 3 from rotation. Yet it read none of the
        # holding policy — `days_held < 1` was hard-coded below and the operator's
        # SMART_EXIT_MIN_HOLD_DAYS (desktop: 20) governed only the rotation path. That is why
        # arming the holding period did not stop the churn.
        #
        # It has to be a VETO, not another argument: the decision logic below opens with
        # ``if score_diff > 15: should_swap = True``, which ignores every args_against entry. A
        # soft argument is decorative in that branch, so swapping the literal for the config
        # value would have changed nothing.
        #
        # This gates ONLY opportunity-driven displacement. ``debate_position_swap`` is reached
        # exclusively from Case 2 of ``should_open_new_position``, so it always requires an
        # incoming candidate and can only ever *swap*. Risk exits run on separate paths and are
        # untouched: ``_run_position_stop_checks`` (trading_loop.py, pre-board,
        # triggered_by_stop=True), intelligent_exit trailing, DrawdownGuard.
        #
        # Fail-OPEN in every uncertain case (unknown age, unreadable config): the veto only ever
        # blocks trading, and "we do not know how long this has been held" is not evidence that a
        # position deserves protection. Mirrors can_sell_position's "no trade history - can sell".
        min_hold_days = self._displacement_min_hold_days()
        # #2935: `0 <` war die Luecke — es befreite Tag 0, also genau die eben gekaufte
        # Position, die nach der Score-Formel ohnehin die schwaechste ist
        # (holding_period_score = 30 bei days_held < 1, 20 % Gewicht). Der Fail-open-Gedanke
        # bleibt woertlich erhalten, haengt jetzt aber am EXPLIZITEN Alters-Marker statt am
        # doppeldeutigen Wert 0.
        if (
            min_hold_days > 0
            and getattr(weakest_position, "age_known", False)
            and weakest_position.days_held < min_hold_days
        ):
            reason = (
                f"Keeping {weakest_position.symbol}: minimum holding period not met "
                f"({weakest_position.days_held}/{min_hold_days:.0f} days) — "
                f"displacement is opportunity-driven and waits; risk exits are unaffected"
            )
            self._debate_history.append(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "new_opportunity": opportunity.symbol,
                    "opportunity_score": opportunity.total_score,
                    "weakest_position": weakest_position.symbol,
                    "position_score": weakest_position.total_score,
                    "arguments_for_swap": [],
                    "arguments_against_swap": [reason],
                    "decision": "HOLD",
                    "reasoning": reason,
                }
            )
            if len(self._debate_history) > 100:
                self._debate_history = self._debate_history[-100:]
            return False, reason

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

        # ADR-R14 (#2696): "sitting a long time for little gain" is coupled to the CONFIGURED
        # holding period instead of a hard-coded 5 days. A growth name necessarily passes through
        # a "6 days, +1%" phase, and the literal 5 turned exactly that phase into an argument for
        # selling — the direct opponent of "identify growth names and hold them mid-term". The
        # rule's core stays: capital must not sit in a dead position forever. Five days is simply
        # not a basis for that judgement; the operator's holding period is.
        # Flag off → the literal 5 stays in force (byte-identical rollback).
        _stale_after = min_hold_days if min_hold_days > 0 else 5
        if (
            weakest_position.days_held > _stale_after
            and weakest_position.unrealized_pnl_pct < 2
        ):
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

        # #2886 (flag-gated): the slot count must be BROKER-VERIFIED before it may
        # grant a new-position slot. The 14.08. incident: after an account switch the
        # internal map was empty/stale, `num_positions` read 0 and Case 1 below waved
        # 12 buys through a full book. Fail-closed: an unconfirmed refresh blocks NEW
        # buys only — SELLs and risk exits never pass through this function.
        if _book_cap_enforced():
            if not self._last_refresh_ok:
                return (
                    False,
                    "slot_unverified: broker position state unavailable — "
                    "new BUYs fail closed (#2886)",
                    None,
                )
            if num_positions > self.max_positions:
                return (
                    False,
                    f"book_overflow: {num_positions}/{self.max_positions} positions "
                    f"held — all BUYs halted while the excess unwinds (#2886)",
                    None,
                )

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
                _band_reason = self._topup_dead_band_reason(opportunity)
                if _band_reason:
                    return False, _band_reason, None
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
        # #2714: the top-up dead-band applies at a FULL book too - a held name inside its
        # band must not be re-bought via the swap path (the Case-1-only check let full-book
        # installs micro-top-up on signal jitter; see the $16.65 1/N dust batch, 06.08).
        _band_reason = self._topup_dead_band_reason(opportunity)
        if _band_reason:
            return False, _band_reason, None
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

    def _topup_dead_band_reason(self, opportunity) -> Optional[str]:
        """#2714: shared top-up dead-band (Case 1 + Case 2). Returns the block reason or
        None. A NEW name has no holding and never matches; read-only conviction blend.
        """
        held = self._position_scores.get(opportunity.symbol)
        if held is None or self._topup_dead_band_pct <= 0 or self.total_capital <= 0:
            return None
        conv = self._blend_conviction(opportunity.symbol, opportunity.model_confidence)
        target_pct = self._conviction_target_pct(conv)
        current_pct = (held.market_value / self.total_capital) * 100.0
        if target_pct - current_pct < self._topup_dead_band_pct:
            return (
                f"{opportunity.symbol} within top-up dead-band "
                f"(held {current_pct:.1f}% vs conviction target {target_pct:.1f}%, "
                f"band {self._topup_dead_band_pct:.0f}%)"
            )
        return None

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

    def get_rebalance_recommendations(
        self, symbol_sector_map: Optional[Dict[str, str]] = None
    ) -> List[Dict]:
        """
        Analyze portfolio and recommend rebalancing actions.
        Only recommends if drift exceeds threshold and cooldowns are satisfied.

        #2654 (Epic #2655): an ADDITIVE sector-cap stage enforces HITL-approved relative
        sector caps (four-eyes store, #2653) with market-value-proportional REDUCE
        recommendations. ``symbol_sector_map`` comes from the engine's portfolio_context
        (same source the gatekeeper reads); callers that don't pass it get today's
        behaviour byte-identically — and if approved caps exist without a map this cycle,
        that gap is WARNED loudly, never silently skipped.
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

        # --- #2654: HITL-approved sector caps → native proportional REDUCE (upstream of the
        # Iron Dome, which stays the untouched fail-closed net). Fail-safe empty store ⇒ the
        # whole stage is a no-op and the output above is byte-identical to before.
        constraints = load_approved_constraints()
        if constraints:
            if not symbol_sector_map:
                logging.warning(
                    "SECTOR_CAP_NO_SECTOR_DATA: approved sector constraints %s present but "
                    "no symbol->sector map this cycle - caps NOT enforced (sector-source "
                    "brick pending in portfolio_context)",
                    sorted(constraints),
                )
            else:
                # Reductions already recommended above count toward each sector's excess —
                # a symbol is never double-dipped by the drift pass AND the sector pass.
                existing_reduce = {
                    r["symbol"]: abs(r["adjustment_value"])
                    for r in recommendations
                    if r["action"] == "REDUCE"
                }
                for sector, cap in constraints.items():
                    members = [
                        (sym, score)
                        for sym, score in self._position_scores.items()
                        if symbol_sector_map.get(sym) == sector
                    ]
                    if not members:
                        continue
                    sector_value = sum(s.market_value for _, s in members)
                    sector_pct = sector_value / self.total_capital
                    if sector_pct <= cap:
                        continue
                    excess = sector_value - cap * self.total_capital
                    excess -= sum(existing_reduce.get(sym, 0.0) for sym, _ in members)
                    if excess <= 0:
                        continue
                    # Iterative allocator over the FREE names (not cooldown-locked, not
                    # already reduced above), market-value-proportional.
                    eligible = [
                        (sym, s)
                        for sym, s in members
                        if sym not in existing_reduce and self._can_trade_symbol(sym)
                    ]
                    eligible_value = sum(s.market_value for _, s in eligible)
                    planned = min(excess, eligible_value)
                    if planned > 0 and eligible_value > 0:
                        for sym, score in eligible:
                            share = planned * (score.market_value / eligible_value)
                            if share <= 0:
                                continue
                            current_pct = (
                                score.market_value / self.total_capital
                            ) * 100
                            recommendations.append(
                                {
                                    "symbol": sym,
                                    "action": "REDUCE",
                                    "current_pct": current_pct,
                                    "target_pct": cap * 100,
                                    "drift_pct": (sector_pct - cap) * 100,
                                    "adjustment_value": -share,
                                    # Same additive contract as the drift pass: the exit
                                    # hook sizes the partial SELL from market_value, never
                                    # a price division.
                                    "market_value": score.market_value,
                                    "position_score": score.total_score,
                                    "reasoning": (
                                        f"SECTOR_CAP {sector}: {sector_pct:.1%} > approved "
                                        f"cap {cap:.1%} - reduce {sym} by ${share:,.0f}"
                                    ),
                                }
                            )
                    if planned < excess:
                        # Archon audit: a cooldown/conviction-blocked residual must never be
                        # a silent breach — the operator sees WHY the cap is not yet met.
                        remaining_pct = (
                            (sector_value - planned) / self.total_capital * 100
                        )
                        logging.warning(
                            "SECTOR_CAP_PARTIAL_TRIM_COOLDOWN_BLOCKED: Sector %s remains "
                            "at %.2f%% (Cap: %.2f%%)",
                            sector,
                            remaining_pct,
                            cap * 100,
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
