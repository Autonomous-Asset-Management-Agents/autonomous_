# portfolio_verdraengung.py
# #4187 (H-5h/H-5i, #4289): Gelegenheit und Verdraengung des Portfolio-Kerns, als Mixin von
# PortfolioManager.
"""Bewertung einer Gelegenheit und Verdraengung einer gehaltenen Position (``VerdraengungMixin``).

Schnitt: ``docs/3738-arc-e6-gestalt/H5_SCHNITT_portfolio_manager.md`` (#4187), Abschnitte H-5h
und H-5i; Umsetzung #4289. Das Handelsbuch (``_can_trade_symbol``) liest die Debatte ueber
``self``.

Zugriffsregel (Entscheidung §3): Kein Zielmodul importiert ``core.portfolio_manager``. Wer am
Modulobjekt patcht, patcht dort, wo der Name gelesen wird — hier also in
``core.portfolio_verdraengung``. Spaete Importe (``config.get_config``,
``core.consensus_retention.consensus_retention_veto``) bleiben spaet.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from core.portfolio_typen import OpportunityScore, PositionScore

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


class VerdraengungMixin:
    """Gelegenheit und Verdraengung von ``PortfolioManager`` (#4187, H-5h).

    Der Zustand entsteht in ``PortfolioManager.__init__``: ``_debate_history``. Die Zaehler des
    Sitzungsdeckels (``_displacement_session_date``, ``_displacements_session``) werden defensiv
    per ``getattr`` gelesen. ``_can_trade_symbol`` kommt aus ``HandelsbuchMixin``.
    """

    def score_opportunity(
        self,
        symbol: str,
        current_price: float,
        rl_action: int,
        model_confidence: float,
        features: Optional[Dict] = None,
        forecast_vol: Optional[float] = None,
        skew_percentile: Optional[float] = None,
        vote_coverage: Optional[float] = None,
    ) -> OpportunityScore:
        """Score a potential new position opportunity.

        #3619: ``forecast_vol`` / ``skew_percentile`` / ``vote_coverage`` are the size
        inputs the sizer will apply to this name; the dead-band uses them for the
        clean-weight target. Optional — None keeps the plain 1/N target.
        """

        opp = OpportunityScore(
            symbol=symbol,
            current_price=current_price,
            rl_action=rl_action,
            model_confidence=model_confidence,
            forecast_vol=forecast_vol,
            skew_percentile=skew_percentile,
            vote_coverage=vote_coverage,
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
        # in +/-1 (Schwellen im Code bei +/-0,6, siehe signal_uebergabe.py:596), womit die
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
        # (signal_desktop_entscheid.py:128, absendung_vorlauf.py:334), die Pauschale wurde also UNBEDINGT vergeben — und der
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

    # ------------------------------------------------------------------
    # #3418: Sitzungsdeckel der Verdraengung (Owner-Entscheid Variante B)
    # ------------------------------------------------------------------

    def _displacement_session_cap(self) -> int:
        """Obergrenze der Verdraengungen je Handelstag, zur AUFRUFZEIT gelesen.

        Nie in eine Modulkonstante eingefroren — genau so sind vom Bediener gesetzte
        Werte in dieser Codebasis schon inert geworden (siehe ``_displacement_min_hold_days``
        und ADR-R14). Gibt 0 zurueck, wenn die Konfiguration nicht lesbar ist: der Deckel
        blockiert ausschliesslich Handel, also faellt er im Zweifel OFFEN aus — dieselbe
        Richtung wie das Haltefrist-Veto.
        """
        try:
            from config import get_config  # local import — the module's own pattern

            return max(
                0, int(getattr(get_config(), "DISPLACEMENT_MAX_PER_SESSION", 0) or 0)
            )
        except (
            Exception
        ):  # noqa: BLE001 — nie das Buch wegen der Konfiguration einfrieren
            logging.warning(
                "PortfolioManager: Sitzungsdeckel der Verdraengung nicht lesbar — "
                "Verdraengung laeuft diesen Zyklus ungedeckelt (fail-open).",
                exc_info=True,
            )
            return 0

    def _displacement_session_budget_ok(self, cap: int, heute) -> Tuple[bool, str]:
        """Ist heute noch eine Verdraengung frei?

        Der Zaehler ist tagesbezogen und setzt sich beim Datumswechsel zurueck — ein
        Deckel ohne Ruecksetzung waere ein Einmal-Verbot. Beide Zustandsfelder werden
        defensiv gelesen: der Manager kann ueber ``__new__`` gebaut werden, es darf also
        nie angenommen werden, dass ``__init__`` gelaufen ist.
        """
        if getattr(self, "_displacement_session_date", None) != heute:
            self._displacement_session_date = heute
            self._displacements_session = 0

        if cap <= 0:
            return True, ""  # unbegrenzt = heutiges Verhalten, byte-identisch

        bisher = int(getattr(self, "_displacements_session", 0) or 0)
        if bisher < cap:
            return True, ""
        return False, (
            f"displacement session cap reached ({bisher}/{cap} today) — the book waits "
            "for a slot to free via a consensus SELL or a risk exit; protective exits "
            "are unaffected"
        )

    def _note_displacement(self, heute) -> None:
        """Haelt eine beschlossene Verdraengung fest.

        Gezaehlt wird der **Beschluss**, nicht die ausgefuehrte Order. Das ist die
        konservative Richtung: ein fehlgeschlagener Swap-Versuch verbraucht Budget, und
        der Deckel bindet dadurch frueher. Fuer eine Churn-Grenze ist das richtig herum —
        sie soll die ABSICHT bremsen, den Bestand umzuschlagen.
        """
        if getattr(self, "_displacement_session_date", None) != heute:
            self._displacement_session_date = heute
            self._displacements_session = 0
        self._displacements_session = (
            int(getattr(self, "_displacements_session", 0) or 0) + 1
        )

    def debate_position_swap(
        self, opportunity: OpportunityScore, weakest_position: Optional[PositionScore]
    ) -> Tuple[bool, str]:
        """
        Self-debate: Should we swap the weakest position for this new opportunity?

        Returns: (should_swap, reasoning)
        """
        if weakest_position is None:
            return True, "No existing positions - proceed with new position"

        # ADR-R14 (#2696): die Haltefrist wird HIER, zur Aufrufzeit, aufgeloest — Veto und
        # "lange gehalten, wenig Gewinn" lesen denselben Wert (Begruendung am Veto-Schritt).
        min_hold_days = self._displacement_min_hold_days()
        reason = self._debatte_haltefrist_veto(
            opportunity, weakest_position, min_hold_days
        )
        if reason is not None:
            return False, reason

        reason = self._debatte_konsens_veto(weakest_position)
        if reason is not None:
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

        score_diff = opportunity.total_score - weakest_position.total_score
        args_for = self._debatte_argumente_dafuer(
            opportunity, weakest_position, score_diff, min_hold_days
        )
        args_against = self._debatte_argumente_dagegen(
            opportunity, weakest_position, score_diff
        )

        debate_log["arguments_for_swap"] = args_for
        debate_log["arguments_against_swap"] = args_against

        should_swap, reasoning = self._debatte_entscheid(
            opportunity, weakest_position, score_diff, args_for, args_against
        )

        debate_log["decision"] = "SWAP" if should_swap else "HOLD"
        debate_log["reasoning"] = reasoning
        self._debatte_protokollieren(debate_log)

        return should_swap, reasoning

    # ------------------------------------------------------------------
    # #4289 (H-5i): die Schritte der Debatte, in der Reihenfolge des Dirigenten
    # ------------------------------------------------------------------

    def _debatte_haltefrist_veto(
        self,
        opportunity: OpportunityScore,
        weakest_position: PositionScore,
        min_hold_days: float,
    ) -> Optional[str]:
        """Haltefrist-Veto vor der Debatte; schreibt die Debattenzeile ``HOLD`` selbst.

        Gibt den Grund zurueck, wenn das Veto greift, sonst ``None``.
        """
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
        #
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
            self._debatte_protokollieren(
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
            return reason
        return None

    def _debatte_konsens_veto(self, weakest_position: PositionScore) -> Optional[str]:
        """Konsens-Rueckhalt (#3291); schreibt **keine** Debattenzeile."""
        # #3291: consensus retention — reuse the shared gate (CONSENSUS_RETENTION_THRESHOLD).
        # A held name the live round table still rates AT/ABOVE the threshold is NOT displaced;
        # the incoming candidate waits for a slot. Default threshold 0 ⇒ off ⇒ byte-identical;
        # fail-open (unknown live consensus ⇒ never retains). This extends the same veto that
        # already guards rotation/book-overflow to the opportunity-swap path (previously uncovered).
        from core.consensus_retention import consensus_retention_veto

        if consensus_retention_veto(weakest_position.symbol, "displacement", self):
            reason = (
                f"Keeping {weakest_position.symbol}: live round-table consensus still "
                f"retains it (#3180) — displacement waits; risk exits are unaffected"
            )
            return reason
        return None

    def _debatte_argumente_dafuer(
        self,
        opportunity: OpportunityScore,
        weakest_position: PositionScore,
        score_diff: float,
        min_hold_days: float,
    ) -> List[str]:
        """Argumente fuer den Tausch: Score-Vergleich, Verlust, lange gehalten mit wenig Gewinn."""
        # Arguments FOR swapping
        args_for = opportunity.arguments_for.copy()

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
        return args_for

    def _debatte_argumente_dagegen(
        self,
        opportunity: OpportunityScore,
        weakest_position: PositionScore,
        score_diff: float,
    ) -> List[str]:
        """Argumente gegen den Tausch, einschliesslich der Handelbarkeit beider Symbole."""
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
        return args_against

    def _debatte_entscheid(
        self,
        opportunity: OpportunityScore,
        weakest_position: PositionScore,
        score_diff: float,
        args_for: List[str],
        args_against: List[str],
    ) -> Tuple[bool, str]:
        """Entscheid und Begruendung; die Zweige in fester Reihenfolge, ``> 15`` zuerst."""
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
        return should_swap, reasoning

    def _debatte_protokollieren(self, eintrag: Dict) -> None:
        """Haengt eine Debattenzeile an und haelt die Historie bei den letzten 100."""
        self._debate_history.append(eintrag)

        # Keep last 100 debates
        if len(self._debate_history) > 100:
            self._debate_history = self._debate_history[-100:]
