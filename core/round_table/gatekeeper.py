# core/round_table/gatekeeper.py
# Epic 2.5 — Round Table V2: ComplianceGatekeeper (Iron Dome)
#
# Deterministischer Hard-Block vor Trade-Execution.
# Kein AI-Agent kann diesen Block überschreiben.
#
# Checks:
#   1. PDT (Pattern Day Trader): < 3 Day Trades in 5 Tagen
#   2. Konzentrations-Limit: Position > 25% Portfolio
#   3. Tages-Limit: > max_daily_trades
#   4. Position-Lock Guard: position_locked=True (Partial Fill Reconciliation)
#
# Policy: CODING_POLICY.md §1 Compliance-First, §11.5 TDD

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class GatekeeperDecision:
    """
    Ergebnis einer ComplianceGatekeeper-Prüfung.
    approved=False + reason erklärt das Veto (MiFID II Audit-Trail).
    """

    approved: bool
    reason: str
    symbol: str


class ComplianceGatekeeper:
    """
    The 'Iron Dome' des Round Table V2.

    Alle Checks sind synchron und dict-basiert (kein async I/O, keine DB-Calls).
    portfolio_context wird vom Aufrufer (run_round_table) injiziert.

    portfolio_context erwartet:
        {
          "day_trades_last_5d": int,          # PDT-Check
          "max_daily_trades": int,             # Tages-Limit
          "current_daily_trades": int,         # Tages-Limit
          "symbol_weights": dict[str, float],  # Konzentration
          "sector_weights": dict[str, float],  # Sektor-Konzentration
          "symbol_sector_map": dict[str, str], # Zuordnung Symbol -> Sektor
          "position_locked": bool,             # Partial Fill Guard
          "traded_today": dict[str, bool],     # #2037 PDT cross-day SELL-Ausnahme (opt-in)
        }
    """

    # Konfigurierbare Schwellenwerte (können in Config extern gesetzt werden)
    PDT_MAX_DAY_TRADES = 3
    CONCENTRATION_LIMIT = 0.25  # 25% max pro Symbol
    SECTOR_CONCENTRATION_LIMIT = 0.30  # 30% max pro Sektor
    # BUY_THRESHOLD: konsistent mit _score_to_signal() in runner.py (score>0.65→BUY).
    # ALLE Checks die "ist das ein BUY-Signal?" prüfen MÜSSEN diese Konstante nutzen —
    # nie 0.65 als Literal duplizieren. Kopplung ist load-bearing.
    BUY_THRESHOLD = 0.65
    # ADR-FINRA-PDT-01: SELL_THRESHOLD = 0.35 (#2037 PDT cross-day SELL-Ausnahme).
    # Rationale: this constant decides "is this a risk-reducing SELL?" for the ONLY branch
    # that relaxes the FINRA PDT hard-block. It MUST equal consensus.SIGNAL_SELL_THRESHOLD
    # and mirror _score_to_signal() (score < 0.35 → SELL) — a test pins the equality. A SELL
    # (score < 0.35) of a position with NO fill today is not a same-day round-trip → exempt;
    # everything at/above 0.35 (HOLD/BUY) is never exempt. Changing this value without the
    # coupling breaks the PDT carve-out's safety envelope. Load-bearing; do not duplicate 0.35.
    SELL_THRESHOLD = 0.35

    # ADR-PDT-01: FINRA Rule 4210 / Reg T, as written.
    #   * The designation attaches on MORE THAN 3 day trades in 5 business days — the
    #     4th trips it. `PDT_MAX_DAY_TRADES = 3` above blocks one trade early.
    #   * It applies ONLY to a margin account with equity BELOW $25,000. At or above
    #     that, it does not bind at all — and the gate never looked.
    # Both corrections live behind GATEKEEPER_PDT_FINRA_ACCURATE (default OFF).
    PDT_FINRA_MAX_DAY_TRADES = 4
    PDT_FINRA_EQUITY_FLOOR = 25_000.0

    def _pdt_day_trade_limit(self) -> int:
        """The day-trade count at which the block engages."""
        from config import get_config

        if getattr(get_config(), "GATEKEEPER_PDT_FINRA_ACCURATE", False):
            return self.PDT_FINRA_MAX_DAY_TRADES
        return self.PDT_MAX_DAY_TRADES  # unchanged default — deliberately stricter

    def _pdt_rule_can_bind(self, portfolio_context: dict) -> bool:
        """False only when equity PROVES the account is outside FINRA's PDT rule.

        ⚠ THE FAIL-SAFE IS THE POINT, AND IT SURVIVES. The existing gate's stated intent
        is "nie ein FINRA-Under-Block" — when in doubt, block. That is right, and this
        keeps it: every uncertain case returns True (the rule might bind → block).
        We return False ONLY on a parseable equity at or above $25,000, i.e. only where
        the rule cannot apply as a matter of fact.

        Note 0.0 is `_coerce_float`'s default for a MISSING field, so it reads as unknown
        rather than as "poor, therefore the rule binds" — same outcome (block), but for
        the honest reason.
        """
        from config import get_config

        if not getattr(get_config(), "GATEKEEPER_PDT_FINRA_ACCURATE", False):
            return True  # flag off → today's behaviour, byte-identical

        equity = portfolio_context.get("equity")
        try:
            equity_val = float(equity)  # None / "n/a" / absent → raise → block
        except (TypeError, ValueError):
            logger.warning(
                "ComplianceGatekeeper: equity unknown (%r) — keeping the PDT block "
                "(fail-safe: never permit a FINRA under-block).",
                equity,
            )
            return True

        if equity_val >= self.PDT_FINRA_EQUITY_FLOOR:
            return False  # proven outside the rule
        return True

    async def check(
        self,
        symbol: str,
        score: float,
        portfolio_context: dict,
    ) -> GatekeeperDecision:
        """
        Prüft ob ein Symbol die Compliance-Grenzen einhält.

        Returns:
            GatekeeperDecision(approved=True) wenn alle Checks bestanden,
            GatekeeperDecision(approved=False, reason=...) bei Veto.
        """
        # 1. Partial Fill / Position Lock Guard
        # SELL-Signale dürfen NIEMALS durch einen Lock blockiert werden.
        # Ein in-flight BUY-Order begründet keinen Lock auf Risk-Reducing SELLs.
        # Nur neue BUY-Signale werden geblockt, solange eine Position gesperrt ist.
        if score > self.BUY_THRESHOLD and portfolio_context.get(
            "position_locked", False
        ):
            return GatekeeperDecision(
                approved=False,
                reason="PositionLocked: An open position or an in-progress swap blocks a new BUY trade",
                symbol=symbol,
            )

        # 2. PDT-Check (Pattern Day Trader Rule, FINRA)
        # #2037: ein cross-day SELL ist KEIN Day-Trade. Exempt einen SELL (score <
        # SELL_THRESHOLD) vom PDT-Block NUR wenn das Symbol HEUTE keinen Fill hatte
        # (traded_today == False). Ein SELL mit heutigem Fill (Open/Add/close+reopen)
        # bleibt geblockt; ein BUY ist NIE befreit. Fail-safe: unbekannt/absent → True →
        # Block (nie ein FINRA-Under-Block). `traded_today` wird von build_portfolio_context
        # NUR befüllt wenn das Opt-in GATEKEEPER_PDT_CROSSDAY_EXEMPTION an ist — sonst leer
        # ⇒ diese Ausnahme ist provably inert ⇒ heutiges Verhalten.
        day_trades = portfolio_context.get("day_trades_last_5d", 0)
        if not self._pdt_rule_can_bind(portfolio_context):
            # ADR-PDT-01 (opt-in): equity PROVES the rule cannot apply — skip the block
            # entirely rather than enforce a rule this account is not subject to.
            pass
        elif day_trades >= self._pdt_day_trade_limit():
            is_sell = score < self.SELL_THRESHOLD
            traded_today = portfolio_context.get("traded_today", {}).get(symbol, True)
            if not (is_sell and not traded_today):
                logger.warning(
                    "ComplianceGatekeeper: PDT-Limit erreicht für %s (%d >= %d)",
                    symbol,
                    day_trades,
                    self.PDT_MAX_DAY_TRADES,
                )
                return GatekeeperDecision(
                    approved=False,
                    reason=f"PDTLimit: {day_trades} day trades in 5 days (limit: {self.PDT_MAX_DAY_TRADES})",
                    symbol=symbol,
                )
            logger.info(
                "ComplianceGatekeeper: PDT-Ausnahme — cross-day SELL %s (kein Fill heute) "
                "trotz day_trades=%d",
                symbol,
                day_trades,
            )

        # 3. Konzentrations-Limit (Symbol-Ebene)
        # Konsistent mit dem Sektor-Limit: nur BUY-Signale können eine
        # Konzentration erhöhen. SELL-Signale bauen ab — nie blockieren.
        if score > self.BUY_THRESHOLD:
            symbol_weights = portfolio_context.get("symbol_weights", {})
            symbol_weight = symbol_weights.get(symbol, 0.0)
            if symbol_weight >= self.CONCENTRATION_LIMIT:
                logger.warning(
                    "ComplianceGatekeeper: BUY blockiert - Konzentrations-Limit für %s: %.1f%% > %.1f%%",
                    symbol,
                    symbol_weight * 100,
                    self.CONCENTRATION_LIMIT * 100,
                )
                return GatekeeperDecision(
                    approved=False,
                    reason=(
                        f"ConcentrationLimit: {symbol} already at {symbol_weight:.1%} "
                        f"of the portfolio (limit: {self.CONCENTRATION_LIMIT:.1%}). "
                        "Only risk-reducing SELL signals are allowed."
                    ),
                    symbol=symbol,
                )

        # 3.b Sektor-Konzentrations-Limit
        # HINWEIS: `check()` bekommt nur den aggregierten score, nicht die Handelsgröße.
        # Deshalb prüfen wir nur echte BUY-Signale (score > 0.65, konsistent mit
        # _score_to_signal() in runner.py: score>0.65→BUY, <0.35→SELL, sonst→HOLD).
        # SELL-Signale bauen die Position ab — sie dürfen NIEMALS durch diesen Check
        # geblockt werden, sonst kann der Bot sein eigenes Risiko nicht reduzieren.
        if score > self.BUY_THRESHOLD:
            sector_weights = portfolio_context.get("sector_weights", {})
            symbol_sector_map = portfolio_context.get("symbol_sector_map", {})
            sector = symbol_sector_map.get(symbol, "Unknown")

            # "Unknown" wird wie ein eigener Sektor behandelt, nicht ignoriert.
            # Verhindert unbegrenzten Aufbau von nicht-gemappten Positionen.
            sector_weight = sector_weights.get(sector, 0.0)
            if sector_weight >= self.SECTOR_CONCENTRATION_LIMIT:
                logger.warning(
                    "ComplianceGatekeeper: BUY blockiert - Sektor-Limit für %s (Sektor: %s): %.1f%% >= %.1f%%",
                    symbol,
                    sector,
                    sector_weight * 100,
                    self.SECTOR_CONCENTRATION_LIMIT * 100,
                )
                return GatekeeperDecision(
                    approved=False,
                    reason=(
                        f"SectorConcentrationLimit: sector '{sector}' already at {sector_weight:.1%} "
                        f"of the portfolio (limit: {self.SECTOR_CONCENTRATION_LIMIT:.1%}). "
                        "Only risk-reducing SELL signals are allowed."
                    ),
                    symbol=symbol,
                )

        # 4. Tages-Limit
        # #2031: nur BUY — eine risikoreduzierende SELL darf NIE durch das (weiche,
        # selbst gesetzte) Tageslimit blockiert werden, konsistent mit Konzentration/
        # Sektor/Lock oben. Die harte FINRA-PDT-Grenze (Check 2) bleibt bewusst
        # richtungsagnostisch (blockt auch SELL), bis #1994 einen cross-day-Guard
        # liefert (ein Same-Day-Round-Trip waere ein Day-Trade → PDT-Verstoss).
        if score > self.BUY_THRESHOLD:
            current_daily = portfolio_context.get("current_daily_trades", 0)
            max_daily = portfolio_context.get("max_daily_trades", 50)
            if current_daily >= max_daily:
                return GatekeeperDecision(
                    approved=False,
                    reason=f"DailyLimit: {current_daily}/{max_daily} trades reached today",
                    symbol=symbol,
                )

        # 5. #2153 Part A — Per-Symbol Anti-Churn cap.
        # Bound how many times ONE symbol may be BUY-approved per NY trading day, so the
        # bot can't re-buy the same name many×/day in fractional lots. SELL-exempt (only
        # score > BUY_THRESHOLD reaches here). Only active behind symbol_churn_guard_active
        # (build_portfolio_context's mirror of GATEKEEPER_SYMBOL_CHURN_LIMIT). Fail-closed:
        # symbol_trades_today_valid=False (the fill-count query failed) ⇒ veto ALL BUYs;
        # a HEALTHY query with the symbol absent = 0 fills ⇒ a fresh symbol stays tradable.
        if score > self.BUY_THRESHOLD and portfolio_context.get(
            "symbol_churn_guard_active", False
        ):
            if not portfolio_context.get("symbol_trades_today_valid", False):
                logger.warning(
                    "ComplianceGatekeeper: churn fill-count unavailable for %s — "
                    "blocking BUY (fail-safe)",
                    symbol,
                )
                return GatekeeperDecision(
                    approved=False,
                    reason="SymbolChurnGuard: fill-count unavailable — blocking BUYs (fail-safe)",
                    symbol=symbol,
                )
            max_per_symbol = int(portfolio_context.get("max_trades_per_symbol", 5))
            n = int(portfolio_context.get("symbol_trades_today", {}).get(symbol, 0))
            if n >= max_per_symbol:
                logger.warning(
                    "ComplianceGatekeeper: BUY blockiert - Symbol-Tageslimit für %s: %d/%d",
                    symbol,
                    n,
                    max_per_symbol,
                )
                return GatekeeperDecision(
                    approved=False,
                    reason=f"SymbolDailyLimit: {n}/{max_per_symbol} trades for {symbol} today",
                    symbol=symbol,
                )

        # 6. Intraday Timing & Buy Pacing (BUY-only; SELL / risk-exits ALWAYS bypass).
        # Config is read in build_portfolio_context (like the churn guard, gate 5); here we
        # only read the pre-computed context. Every measured field is FAIL-OPEN: absent
        # (None) ⇒ that sub-guard does not fire ⇒ byte-identical to today (a clock error /
        # closed market / empty buy-history can never fabricate a BUY veto). Clean re-home
        # of #2546: ONE wired enforcement point, not two inert copies.
        if score > self.BUY_THRESHOLD and portfolio_context.get(
            "intraday_timing_active", False
        ):
            # 6a. OpeningNoiseGuard — no BUY in the opening-noise window.
            mins_open = portfolio_context.get("minutes_since_market_open")
            no_buy_open = portfolio_context.get("no_buy_opening_minutes", 30)
            if mins_open is not None and mins_open < no_buy_open:
                return GatekeeperDecision(
                    approved=False,
                    reason=f"OpeningNoiseGuard: no-BUY window active (first {no_buy_open}m after open)",
                    symbol=symbol,
                )
            # 6b. BuyPacingGuard — cooldown between consecutive BUYs.
            last_buy_m = portfolio_context.get("last_buy_elapsed_minutes")
            cooldown = portfolio_context.get("global_buy_cooldown_minutes", 30)
            if last_buy_m is not None and last_buy_m < cooldown:
                return GatekeeperDecision(
                    approved=False,
                    reason=f"BuyPacingGuard: cooldown active (last BUY {last_buy_m:.1f}m < {cooldown}m ago)",
                    symbol=symbol,
                )
            # 6c. BuyPacingGuard — at most N BUYs per hour.
            buys_hr = portfolio_context.get("buys_last_hour")
            max_hr = portfolio_context.get("max_buys_per_hour", 2)
            if buys_hr is not None and buys_hr >= max_hr:
                return GatekeeperDecision(
                    approved=False,
                    reason=f"BuyPacingGuard: max buys per hour reached ({buys_hr} >= {max_hr})",
                    symbol=symbol,
                )

        # Alle Checks bestanden
        _debug_weight = portfolio_context.get("symbol_weights", {}).get(symbol, 0.0)
        logger.debug(
            "ComplianceGatekeeper: %s APPROVED (score=%.3f, day_trades=%d, weight=%.1f%%)",
            symbol,
            score,
            day_trades,
            _debug_weight * 100,
        )
        return GatekeeperDecision(
            approved=True,
            reason="AllChecksPassed: PDT, concentration and daily limit OK",
            symbol=symbol,
        )
