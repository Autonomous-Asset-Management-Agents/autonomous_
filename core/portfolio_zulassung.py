# portfolio_zulassung.py
# #4187 (H-5g, #4288): Zulassung und Nachkauf des Portfolio-Kerns, als Mixin von PortfolioManager.
"""Zulassung neuer Positionen und Nachkauf (``ZulassungMixin``).

Schnitt: ``docs/3738-arc-e6-gestalt/H5_SCHNITT_portfolio_manager.md`` (#4187), Abschnitt H-5g;
Umsetzung #4288. Sitzungsdeckel und Debatte der Verdrängung, das Handelsbuch und die
Positionsbewertung liest die Zulassung über ``self``.

Zugriffsregel (Entscheidung §3): Kein Zielmodul importiert ``core.portfolio_manager``. Wer am
Modulobjekt patcht, patcht dort, wo der Name gelesen wird — hier also in
``core.portfolio_zulassung``.
"""

import logging
from datetime import timezone
from typing import Optional, Tuple

from core.portfolio_typen import OpportunityScore, _book_cap_enforced, _now_utc
from core.sim.clock import engine_now  # #3317: Sim und Live auf derselben Uhr


class ZulassungMixin:
    """Zulassung und Nachkauf von ``PortfolioManager`` (#4187, H-5g).

    Der Zustand entsteht in ``PortfolioManager.__init__``: ``max_positions``,
    ``total_capital``, ``_max_position_pct``, ``_topup_dead_band_rel``,
    ``_min_order_interval_sec``, ``_position_scores``, ``_last_refresh_ok``.
    """

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

        # #3655: a slot freed by a stop-LOSS exit stays held for
        # STOP_EXIT_SLOT_HOLD_DAYS trading days — a NEW name waits, the sold name may
        # return (its own hold does not count against it). Measured: the immediate
        # replacement lost −1,890 $ over 74 lots while the returner made +1,446 $.
        _slot_reason = self._slot_hold_reason(opportunity, num_positions)
        if _slot_reason:
            return (False, _slot_reason, None)

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
        # #3291: displacement master switch. OFF ⇒ a full book does NOT swap out a holding to
        # fund a new BUY; the new name waits for a slot to free via a consensus SELL or a risk
        # exit (capital-preservation posture, sibling of ROTATION_EXIT_ENABLED). Default ON ⇒
        # byte-identical. Config-read fallback logs at WARNING (§5.6), never silent.
        try:
            from config import get_config as _gc

            _disp_on = bool(getattr(_gc(), "DISPLACEMENT_ENABLED", True))
        except Exception:  # noqa: BLE001 — a config read must never break the decision
            logging.warning(
                "PortfolioManager: could not read DISPLACEMENT_ENABLED — defaulting ON "
                "(byte-identical).",
                exc_info=True,
            )
            _disp_on = True
        if not _disp_on:
            return (
                False,
                "displacement disabled — book full, waiting for a slot to free "
                "(consensus SELL or risk exit)",
                None,
            )
        # #3418 (Owner-Entscheid Variante B): Sitzungsdeckel. Er steht VOR der Debatte,
        # nicht darin — ``debate_position_swap`` oeffnet mit
        # ``if score_diff > 15: should_swap = True`` und ignoriert dort jedes
        # Gegenargument. Ein Deckel als Argument waere in genau dem Zweig wirkungslos,
        # in dem er am noetigsten ist. Dieselbe Lehre wie bei ADR-R14.
        _heute = engine_now(timezone.utc).date()
        _cap = self._displacement_session_cap()
        _budget_ok, _budget_grund = self._displacement_session_budget_ok(_cap, _heute)
        if not _budget_ok:
            logging.warning("PortfolioManager: %s (#3418)", _budget_grund)
            return False, _budget_grund, None

        weakest = self.get_weakest_position()
        should_swap, reasoning = self.debate_position_swap(opportunity, weakest)
        if should_swap:
            self._note_displacement(_heute)
            return True, reasoning, weakest.symbol if weakest else None
        return False, reasoning, None

    def _slot_hold_reason(self, opportunity, num_positions: int) -> Optional[str]:
        """#3655 capacity gate for NEW names only: ``cap - held - reserved`` must stay
        positive. A held name (top-up) is not a slot candidate and never waits (#3670:
        the sim showed CAH/DECK top-ups declined with ``slot_hold``). Returns the block
        reason or None."""
        if opportunity.symbol in self._position_scores:
            return None
        reserved = self._reserved_slots(opportunity.symbol)
        if (
            reserved > 0
            and num_positions < self.max_positions
            and num_positions + reserved >= self.max_positions
        ):
            return (
                f"slot_hold: {reserved} slot(s) held after stop-loss exits "
                f"({num_positions}+{reserved}/{self.max_positions}) — a new name waits "
                f"for the next trading day"
            )
        return None

    def _reserved_slots(self, symbol: str) -> int:
        """#3655: slots held after stop-loss exits (0 when the setting is off). The
        candidate's own hold never counts against it (returner). Fail-open."""
        try:
            from core.engine.reentry_lockout import reserved_slots

            return int(
                reserved_slots(
                    _now_utc(), set(self._position_scores.keys()), exclude=symbol
                )
            )
        except Exception as exc:  # noqa: BLE001 — never break a buy decision
            logging.warning(
                "SlotHold: reservation unavailable (%s) -> no slot held.",
                exc,
                exc_info=True,
            )
            return 0

    def _topup_target_pct(self, opportunity) -> float:
        """#3619/#3361/#3963: the ONE top-up target (%) — the clean-weight target the sizer
        applies (or the conviction target in conviction mode), times the regime factor the
        order path applies. Shared by the dead-band and the gap cap so they never disagree.
        """
        conv = self._blend_conviction(opportunity.symbol, opportunity.model_confidence)
        # #3619: in clean-weight mode the sizer targets 1/N x vol x skew x coverage
        # (capped at MAX) — NOT the conviction weight. Measured 16.-18.09.2026 (installed
        # app): ABNB target 6,020 $ was bought up to ~20,000 $ in five tranches because
        # this check still compared against the 25 % conviction target. One target
        # authority: the dead-band must see the same number the sizer applies.
        clean_target = self._clean_weight_target_pct(opportunity)
        target_pct = (
            clean_target
            if clean_target is not None
            else self._conviction_target_pct(conv)
        )
        # #3361: the regime throttle shrinks each BUY order, but positions are built in
        # tranches and the dead-band is the authority that ends the build. Without the
        # factor here a throttled position is merely topped up in smaller steps until it
        # reaches the unthrottled target. Dark / no fresh reading / error => 1.0 =>
        # byte-identical. Only ever lowers the target; never sells a held name down.
        return target_pct * self._regime_target_factor()

    def _topup_dead_band_reason(self, opportunity) -> Optional[str]:
        """#2714/#3963: shared top-up dead-band (Case 1 + Case 2). Returns the block reason
        or None. A NEW name has no holding and never matches.

        ADR-R18 (#3963): RELATIVE band — a held name is topped up only while it sits below
        ``target x (1 - rel)``. The former absolute 5 percentage points blocked every
        top-up once targets fell to <= 5 % (20 slots).
        """
        held = self._position_scores.get(opportunity.symbol)
        if held is None or self._topup_dead_band_rel <= 0 or self.total_capital <= 0:
            return None
        target_pct = self._topup_target_pct(opportunity)
        current_pct = (held.market_value / self.total_capital) * 100.0
        if current_pct >= target_pct * (1.0 - self._topup_dead_band_rel):
            regime_factor = self._regime_target_factor()
            regime_note = (
                f", regime throttle x{regime_factor:.2f}" if regime_factor < 1.0 else ""
            )
            target_kind = (
                "clean-weight"
                if self._clean_weight_target_pct(opportunity) is not None
                else "conviction"
            )
            return (
                f"{opportunity.symbol} within top-up dead-band "
                f"(held {current_pct:.1f}% vs {target_kind} target {target_pct:.1f}%, "
                f"band {self._topup_dead_band_rel * 100:.0f}% of target{regime_note})"
            )
        return None

    def topup_gap_value(self, opportunity) -> Optional[float]:
        """#3963: dollar gap between a held name and its top-up target — the most a top-up
        order may buy. ``None`` for a name not held (a new position is sized as before).

        Fail-closed: unconfirmed broker holdings (``_last_refresh_ok`` false) or no capital
        => 0.0, so the order path buys NOTHING for a held name instead of a full tranche.
        Never negative: a name at or above its target has a gap of 0 (never sells down).
        """
        held = self._position_scores.get(opportunity.symbol)
        if held is None:
            return None
        if not getattr(self, "_last_refresh_ok", True) or self.total_capital <= 0:
            logging.warning(
                "[%s] top-up gap: holdings unconfirmed or no capital — no top-up (#3963).",
                opportunity.symbol,
            )
            return 0.0
        target_value = self._topup_target_pct(opportunity) / 100.0 * self.total_capital
        return max(0.0, target_value - float(held.market_value))

    def _clean_weight_target_pct(self, opportunity) -> Optional[float]:
        """#3619: the clean-weight target (%) the sizer applies in mode "a"/"b", from the
        SAME helpers (``effective_max_positions``, ``vol_targeting_scaler``,
        ``skew_size_tilt``, ``coverage_size_discount``) — None in conviction mode. An
        unreadable config or helper falls back to the conviction target, as WARNING."""
        try:
            import config as _cfg
            from core.risk_manager import (
                coverage_size_discount,
                effective_max_positions,
                skew_size_tilt,
                vol_targeting_scaler,
            )

            mode = (
                str(getattr(_cfg, "CLEAN_WEIGHT_SIZING", "off") or "off")
                .strip()
                .lower()
            )
            if mode not in ("a", "b"):
                return None
            base_w = 1.0 / float(max(1, int(effective_max_positions())))
            if mode == "b":
                w = (
                    base_w
                    * vol_targeting_scaler(
                        getattr(opportunity, "forecast_vol", None), log_missing=False
                    )
                    * skew_size_tilt(
                        getattr(opportunity, "skew_percentile", None), log_missing=False
                    )
                    * coverage_size_discount(
                        getattr(opportunity, "vote_coverage", None), log_missing=False
                    )
                )
            else:
                w = base_w
            return min(w, self._max_position_pct) * 100.0
        except Exception as exc:  # noqa: BLE001 — never break a buy decision
            logging.warning(
                "clean-weight top-up target unavailable (%s) -> conviction target",
                exc,
                exc_info=True,
            )
            return None

    @staticmethod
    def _regime_target_factor() -> float:
        """#3361: the same factor the order path applies (one shared gate)."""
        try:
            from core.engine.regime_signal import active_throttle_factor

            return float(active_throttle_factor())
        except Exception as exc:  # noqa: BLE001 — never break a buy decision
            logging.warning(
                "RegimeThrottle: top-up target unthrottled (%s).", exc, exc_info=True
            )
            return 1.0
