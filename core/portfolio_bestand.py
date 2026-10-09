# portfolio_bestand.py
# #4187 (H-5j/H-5k, #4291): Bestand und Ueberzeugung des Portfolio-Kerns, als Mixin von
# PortfolioManager.
"""Bestandsabgleich, Positions-Scores und Ueberzeugung (``BestandMixin``).

Schnitt: ``docs/3738-arc-e6-gestalt/H5_SCHNITT_portfolio_manager.md`` (#4187), Abschnitte H-5j
und H-5k; Umsetzung #4291. Zulassung (``should_open_new_position``, ``_topup_target_pct``) und
Verdraengung lesen den Bestand ueber ``self``.

Zugriffsregel (Entscheidung §3): Kein Zielmodul importiert ``core.portfolio_manager``. Wer am
Modulobjekt patcht, patcht dort, wo der Name gelesen wird — hier also in
``core.portfolio_bestand``. Der Kern exportiert ``_holding_period_cfg`` und
``holding_period_score`` wieder; ein Patch auf den Re-Export liefe ins Leere. Spaete Importe
(``config.get_config``) bleiben spaet.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Optional

from core.portfolio_typen import (
    PositionScore,
    _book_cap_enforced,
    _ensure_aware_utc,
    _now_utc,
)


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


class BestandMixin:
    """Bestand und Ueberzeugung von ``PortfolioManager`` (#4187, H-5j).

    Der Zustand entsteht in ``PortfolioManager.__init__``: ``client``, ``total_capital``,
    ``_seen_account_id``, ``_seen_refresh``, ``_position_scores``, ``_position_history``,
    ``_trade_history``, ``_position_opened_at``, ``_last_refresh_ok``, ``_conviction_ewma``,
    ``_conviction_ewma_enabled``, ``_conviction_ewma_alpha``, ``_live_consensus``,
    ``_min_position_pct``, ``_max_position_pct``.
    """

    def refresh_positions(self) -> Dict[str, PositionScore]:
        """Fetch current positions and calculate scores for each"""
        try:
            # Live trading: keep total_capital in sync with account equity (fixes distribution)
            self._bestand_konto_abgleich()
            positions = self.client.get_all_positions()

            # #3291 current-lot clock (dark). Capture the prior held-set BEFORE the rebuild so
            # an absent→present symbol is a WITNESSED re-entry (fresh holding clock), and skip
            # the first refresh (rehydration — existing positions fall back to _trade_history).
            _cur_lot = self._bestand_lot_uhr_aktiv()
            _prior_syms = set(self._position_scores)
            _is_first_refresh = not self._seen_refresh
            self._seen_refresh = True

            for pos in positions:
                (
                    symbol,
                    qty,
                    avg_entry,
                    current_price,
                    market_value,
                    unrealized_pnl,
                    unrealized_pnl_pct,
                ) = self._bestand_position_lesen(pos)

                # #3291: stamp a WITNESSED re-entry (fresh holding clock). Not on the first
                # refresh (rehydration) and not for a name still held from the prior cycle.
                if _cur_lot and not _is_first_refresh and symbol not in _prior_syms:
                    self._position_opened_at[symbol] = _now_utc()

                days_held, age_known = self._bestand_haltedauer(symbol, _cur_lot)

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

            self._bestand_geschlossene_entfernen(positions)

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

    def _bestand_konto_abgleich(self) -> None:
        """H-5k: Equity nach ``total_capital`` und, unter ``_book_cap_enforced()``, der
        Kontowechsel (#2886). Fängt eigene Fehler (BUG-AI-114); ein Fehler hier markiert den
        Bestand NICHT als veraltet — nur der Positionsabruf zählt (O3-SAFETY)."""
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
                            self._position_opened_at = {}  # #3291: stale on new account
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

    def _bestand_lot_uhr_aktiv(self) -> bool:
        """H-5k: ``ROTATION_MIN_HOLD_CURRENT_LOT`` (#3291), bei einem Lesefehler aus (§5.6)."""
        try:
            from config import get_config as _gc

            _cur_lot = bool(getattr(_gc(), "ROTATION_MIN_HOLD_CURRENT_LOT", False))
        except Exception:  # noqa: BLE001 — a config read must never break the refresh
            # §5.6 / AGENT_WAY_OF_WORKING §2.2: a fallback is logged at WARNING, never
            # swallowed silently — a config-read failure on the days_held path must be
            # visible (it silently reverts to the legacy min(_trade_history) age).
            logging.warning(
                "PortfolioManager: could not read ROTATION_MIN_HOLD_CURRENT_LOT — "
                "falling back to the legacy min(_trade_history) age (flag treated OFF).",
                exc_info=True,
            )
            _cur_lot = False
        return _cur_lot

    @staticmethod
    def _bestand_position_lesen(pos) -> tuple:
        """H-5k: eine Broker-Position (Alpaca-Objekt oder Dict) als ``(symbol, qty, avg_entry,
        current_price, market_value, unrealized_pnl, unrealized_pnl_pct)``."""
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
            unrealized_pnl_pct = (unrealized_pnl / cost * 100) if cost > 0 else 0
        return (
            symbol,
            qty,
            avg_entry,
            current_price,
            market_value,
            unrealized_pnl,
            unrealized_pnl_pct,
        )

    def _bestand_haltedauer(self, symbol: str, cur_lot: bool) -> "tuple[int, bool]":
        """H-5k: ``(days_held, age_known)`` einer gehaltenen Position (#2935, #3291)."""
        # Calculate days held. #2935: `age_known` merkt sich, OB das Alter belegt ist.
        # #3291: prefer the CURRENT-lot clock over min(_trade_history), which dated a
        # re-entered name to a prior, already-closed holding and defeated the min-hold.
        days_held = 0
        age_known = False
        if cur_lot and symbol in self._position_opened_at:
            days_held = (_now_utc() - self._position_opened_at[symbol]).days
            age_known = True
        elif symbol in self._trade_history and self._trade_history[symbol]:
            first_buy = min(_ensure_aware_utc(t) for t in self._trade_history[symbol])
            days_held = (_now_utc() - _ensure_aware_utc(first_buy)).days
            age_known = True
        return days_held, age_known

    def _bestand_geschlossene_entfernen(self, positions) -> None:
        """H-5k: Positionen, die der Broker nicht mehr meldet, verlassen den Bestand."""
        # Remove closed positions
        current_symbols = {
            pos.symbol if hasattr(pos, "symbol") else pos.get("symbol")
            for pos in positions
        }
        closed = [s for s in self._position_scores if s not in current_symbols]
        for s in closed:
            del self._position_scores[s]
            # #3291: full exit resets the current-lot clock, so a re-entry starts a fresh
            # holding period (not dated to the closed lot's old buys).
            self._position_opened_at.pop(s, None)
            # Full exit → drop the conviction EWMA so a later re-entry starts fresh (raw), not
            # anchored to a stale smoothed value from the previous holding.
            self.clear_conviction(s)

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
        # #3180: a symbol we no longer hold must not keep a stale consensus that could veto a future
        # exit — drop it on the same full-exit boundary.
        self._live_consensus.pop(symbol, None)

    def set_live_consensus(self, symbol: str, consensus: float) -> None:
        """#3180: store the LIVE round-table blend-consensus (0-1) for a held symbol (harvest seam).
        A non-finite/None value is ignored so the gate fails open (no stale/garbage veto).
        """
        try:
            v = float(consensus)
        except (TypeError, ValueError):
            return
        if v == v and v not in (float("inf"), float("-inf")):  # not NaN/inf
            self._live_consensus[symbol] = v

    def get_live_consensus(self, symbol: str) -> Optional[float]:
        """#3180: the last stored live consensus for a symbol, or None (⇒ gate fails open)."""
        return self._live_consensus.get(symbol)

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
