# risk_bemessung.py
# --- Bemessung einer Position: Risiko-Skalierer, Zielgröße, Deckel-Kette (#4268, H-3f) ---

"""Bemessung des ``RiskManager`` — ``calculate_position_size`` samt Helfern als ``BemessungMixin``.

Umgezogen aus ``core/risk_manager.py`` (#4268, H-3f). Schnitt-Entscheidung #4185:
``docs/3738-arc-e6-gestalt/H3_SCHNITT_risk_manager.md`` §2 und §3.

Zugriffsregel (Entscheidung §3): Namen, die Tests am Modulobjekt ``core.risk_manager``
patchen, liest dieses Modul **nur** als ``_rm.<name>`` — hier ``_rm.resolve_vix``,
``_rm._bull_exposure_cfg``, ``_rm.vix_ladder_scaler``, ``_rm.vol_targeting_scaler``,
``_rm.skew_size_tilt``, ``_rm.coverage_size_discount`` und ``_rm.effective_max_positions``.
Ein freier Name liefe am Patch still vorbei. Der Kern wird erst am Dateiende importiert, nach
der Klasse: Der Kern importiert dieses Modul für seine Basisklasse (Zirkelimport). Das Mixin
hat kein ``__init__``; den Zustand (``total_capital``, ``trading_halted``, ``trading_reduced``,
``risk_per_trade_percent``, ``user_id``) liest es über ``self``, die Deckel (``_step_*``,
``_sizing_order_floor``) über die MRO aus ``DeckelMixin``.

Die ``config``-Zugriffe bleiben spät, im Rumpf der Methoden, wie vor dem Umzug.
"""

import logging
from typing import Any, Dict, Optional, Tuple

import pandas as pd


class BemessungMixin:
    """Positionsbemessung des ``RiskManager``: Skalierer, Zielgröße, dann die Deckel."""

    def calculate_position_size(
        self,
        stop_loss_atr_multiplier: float,
        atr: float,
        confidence: str = "medium",
        size_scaler: float = 1.0,
        market_data: Optional[Dict[str, Any]] = None,
        num_stocks_in_strategy: int = 1,
        current_price: float = 0.0,
        account_cash: float = 0.0,
        allow_fractional: bool = True,
        conviction_score: float = 0.5,
        forecast_vol: Optional[float] = None,
        rr_percentile: Optional[float] = None,
        coverage: Optional[float] = None,
        sizing_trace: Optional[Dict[str, Any]] = None,
    ) -> float:
        """
        Calculates position size with DYNAMIC CONVICTION-BASED SCALING.
        - conviction_score: 0.0-1.0, higher = bigger position (5% to 25% of portfolio)  # noqa: E501
        - trading_reduced=True → 50% position size
        - trading_halted=True → 0 shares (no new positions)
        - allow_fractional=True → Returns fractional shares (e.g., 0.5 shares)  # noqa: E501
        - forecast_vol: #1953 HAR-RV forward-vol (daily stdev) for the flag-gated  # noqa: E501
          vol-targeting factor; None (default) or flag OFF → exact old behaviour  # noqa: E501
        - sizing_trace (#2811): OPTIONAL per-call sink. When a dict is passed, the sizer  # noqa: E501
          records WHICH limit bound (`binding_limit`) and, on a zero, WHY
          (`zero_reason`) — the cause behind 85.5 % of the chain's skipped orders,  # noqa: E501
          previously logged to a log file the pythonw desktop engine does not have.  # noqa: E501
          Pure observation: never changes the returned size (pinned bit-for-bit by  # noqa: E501
          tests/unit/test_sizing_zero_reason.py against golden values generated from  # noqa: E501
          the unchanged code), and a broken sink never raises into the order path.  # noqa: E501
          The reason is recorded AT THE CLAMP, not at the funnel exit — every clamp  # noqa: E501
          (cash/caps/conviction) leaves through the same `< min_fractional` return,  # noqa: E501
          so labelling the exit would call every cause "below_order_floor".
        """

        def _note(key: str, value: str) -> None:
            # Observation must never break sizing: a read-only/broken sink is ignored.  # noqa: E501
            if sizing_trace is None:
                return
            try:
                sizing_trace[key] = value
            except Exception:  # noqa: BLE001 — see docstring; deliberate
                logging.warning("Audit sink sizing traceback", exc_info=True)

        if atr is None or pd.isna(atr) or atr <= 0:
            _note("zero_reason", "invalid_atr")
            return 0.0

        # === EARLY EXIT: If halted (locally or via KillSwitch), no new trades ===  # noqa: E501
        if self.trading_halted or self._halt().is_halted(self.user_id):
            _note("zero_reason", "trading_halted")
            return 0.0

        # #3829 (G-5): a sequence of named steps, in the original order (code moved
        # verbatim). A cap step is `_step_<binding_limit>`: the record names the code.
        final_risk_scaler, vol_scaler, skew_tilt, coverage_discount = (
            self._sizing_risk_scaler(
                market_data,
                confidence,
                size_scaler,
                forecast_vol,
                rr_percentile,
                coverage,
            )
        )
        sized = self._sizing_target(
            current_price,
            conviction_score,
            final_risk_scaler,
            vol_scaler,
            skew_tilt,
            coverage_discount,
            num_stocks_in_strategy,
            atr,
            stop_loss_atr_multiplier,
            sizing_trace,
            _note,
        )
        if sized is None:  # legacy risk path without dollar risk per share
            return 0
        num_shares, _sizing_conv, _sizing_target_pct, _sizing_target_value = sized[:4]
        clean_weight_mode, max_pos_pct_sizing = sized[4:]

        num_shares, limited_by_cap = self._step_position_cap(
            num_shares, current_price, _note
        )
        num_shares, limited_by_cash = self._step_cash(
            num_shares,
            current_price,
            account_cash,
            num_stocks_in_strategy,
            clean_weight_mode,
            _sizing_target_value,
            max_pos_pct_sizing,
            _note,
        )

        # INFO log: show conviction -> target% -> value and which cap applied (helps debug "strictly 10k" issue)  # noqa: E501
        if _sizing_conv is not None and current_price > 0 and num_shares > 0:
            final_value = num_shares * current_price
            cap_reason = "capped by max%"
            if limited_by_cash:
                cap_reason = "capped by cash"
            elif limited_by_cap:
                cap_reason = "capped by max%"
            logging.info(
                f"Position sizing: conviction={_sizing_conv:.2f} -> target {_sizing_target_pct * 100:.1f}% (${_sizing_target_value:,.0f}) -> "  # noqa: E501
                f"final ${final_value:,.0f} ({num_shares:.2f} shares) [{cap_reason}]"  # noqa: E501
            )

        num_shares = self._step_total_exposure_cap(num_shares, current_price, _note)
        num_shares = self._step_kelly(num_shares, _note)
        num_shares = self._step_max_loss_per_trade(
            num_shares, atr, stop_loss_atr_multiplier, current_price, _note
        )
        num_shares = self._step_compliance_order_value(num_shares, current_price, _note)
        return self._sizing_order_floor(
            num_shares, current_price, allow_fractional, sizing_trace, _note
        )

    def _sizing_risk_scaler(
        self,
        market_data,
        confidence,
        size_scaler,
        forecast_vol,
        rr_percentile,
        coverage,
    ) -> Tuple[float, float, float, float]:
        """Factors applied BEFORE every cap: (final, vol, skew, coverage) (#3829)."""
        # 1. Dynamic Volatility Risk Scaling (AGGRESSIVE REDUCTION)
        # ADR-R08: VIX-basierte Risikosteuerung (Volatility Regime Scaling)
        # Schwellenwerte abgeleitet aus CBOE VIX-Perzentilverteilung 2010-2024:
        #   VIX > 40 → 99. Perzentil (Crash-Regime, z.B. COVID März 2020): Risk -70%  # noqa: E501
        #   VIX > 35 → 97. Perzentil (Stress): Risk -60%
        #   VIX > 25 → 85. Perzentil (Erhöht, Pre-Earnings-Season): Risk -35%  # noqa: E501
        #   VIX > 18 → 55. Perzentil (Leicht erhöht): Risk -10%
        #   VIX <= 18 → Normal: kein Scaling
        # Begründung: Lineare VIX-Skalierung würde Cliff-Effekte erzeugen; diese Stufen  # noqa: E501
        # sind bewusst grob, um häufiges Regime-Wechseln (Churn) zu vermeiden.
        # #2980 (ADR-R09): resolve VIX through the central fail-closed helper.
        # Missing/invalid VIX → VIX_FAILCLOSED_SENTINEL (crash band) → 0.3 scaler  # noqa: E501
        # (max de-risk), NOT the old benign 20.0 (0.9, a 10% trim). A blind gauge  # noqa: E501
        # must size as if the market were in crisis, never as if it were calm.
        current_vix, _vix_confirmed, _vix_source = _rm.resolve_vix(market_data)

        # #3099 (H2): bounded calm-regime risk-on step (dark flag). Flag OFF =
        # pure down-only ladder (byte-identical). The >1.0 step is capped downstream  # noqa: E501
        # by MAX_TOTAL_EXPOSURE_PCT + per-name caps + RISK_FORBID_LEVERAGE.
        _bull_en, _bull_scaler = _rm._bull_exposure_cfg()
        vix_risk_scaler = _rm.vix_ladder_scaler(
            current_vix, bull_enabled=_bull_en, calm_scaler=_bull_scaler
        )

        # 2. Confidence Scaling
        confidence_scaler = 1.0
        if confidence == "high":
            confidence_scaler = 1.5
        elif confidence == "low":
            confidence_scaler = 0.5

        # NEW: PROGRESSIVE REDUCTION scaling
        reduction_scaler = 1.0
        if self.trading_reduced:
            reduction_scaler = (
                0.50  # 50% position size when warning phase active  # noqa: E501
            )

        # #1953 TRD-2: inverse-vol risk-parity factor (VOL_TARGETING_SIZING_ENABLED,  # noqa: E501
        # DEFAULT ON — owner waiver 2026-08-14; OFF => vol_scaler == 1.0 exactly; #3261). Applied here —  # noqa: E501
        # BEFORE every hard cap below (max-% / cash / exposure / compliance), so  # noqa: E501
        # risk parity can only resize WITHIN the existing safety ceilings, never  # noqa: E501
        # bypass one. Separate factor: the size_scaler semantics stay untouched.  # noqa: E501
        vol_scaler = _rm.vol_targeting_scaler(forecast_vol)

        # #3199: bounded 25Δ RR skew tilt (SKEW_SIZE_TILT_ENABLED, default OFF =>  # noqa: E501
        # skew_tilt == 1.0 exactly, byte-identical). Applied here — like vol_scaler,  # noqa: E501
        # BEFORE every hard cap below — so the asymmetry tilt can only resize WITHIN  # noqa: E501
        # the safety ceilings, never bypass one. Folded into the SAME audit mirror  # noqa: E501
        # (apply_vol_targeting_audit) so context.risk_size_scaler logs vol*skew
        # (applied == logged; MiFID II Art. 17).
        skew_tilt = _rm.skew_size_tilt(rr_percentile)

        # #3210: coverage prudence discount (COVERAGE_SIZING_STRENGTH, default 0.0  # noqa: E501
        # => 1.0 exactly, byte-identical). Applied here — like vol_scaler / skew_tilt,  # noqa: E501
        # BEFORE every hard cap below — so the prudence discount can only resize DOWN  # noqa: E501
        # within the safety ceilings, never bypass one. Folded into the SAME audit  # noqa: E501
        # mirror (apply_vol_targeting_audit) so context.risk_size_scaler logs
        # vol*skew*coverage (applied == logged; MiFID II Art. 17). PRUDENCE, not alpha.  # noqa: E501
        coverage_discount = _rm.coverage_size_discount(coverage)

        final_risk_scaler = (
            vix_risk_scaler
            * confidence_scaler
            * size_scaler
            * reduction_scaler
            * vol_scaler
            * skew_tilt
            * coverage_discount
        )
        return final_risk_scaler, vol_scaler, skew_tilt, coverage_discount

    def _sizing_target(
        self,
        current_price,
        conviction_score,
        final_risk_scaler,
        vol_scaler,
        skew_tilt,
        coverage_discount,
        num_stocks_in_strategy,
        atr,
        stop_loss_atr_multiplier,
        sizing_trace,
        _note,
    ):
        """Size before the first cap (#3829). None where the legacy path returned 0."""
        # 3. DYNAMIC CONVICTION-BASED POSITION SIZING
        # Position scales from MIN to MAX based on conviction (wide range so not "strictly 10k each")  # noqa: E501
        try:
            import config as _cfg

            dynamic_enabled = getattr(_cfg, "ENABLE_DYNAMIC_SIZING", True)
            # Fallback MUST equal the config default (#2784) — 0.02 silently halved the  # noqa: E501
            # relative entry floor whenever _cfg was a partial namespace.
            min_pos_pct = getattr(_cfg, "MIN_POSITION_PERCENT", 0.05)
            max_pos_pct_sizing = getattr(
                _cfg, "MAX_POSITION_PERCENT_SIZING", 0.30
            )  # noqa: E501
            max_pos_cap_pct = getattr(_cfg, "MAX_POSITION_PERCENT", 0.25)
            # #3284 (Epic #3086): clean-weight sizing mode (Variant 2, per-symbol).
            # "off" (default) => the conviction path below, byte-identical.
            clean_weight_mode = (
                str(getattr(_cfg, "CLEAN_WEIGHT_SIZING", "off") or "off")
                .strip()
                .lower()
            )
        except ImportError:
            dynamic_enabled = True
            min_pos_pct = 0.02
            max_pos_pct_sizing = 0.30
            max_pos_cap_pct = 0.25
            clean_weight_mode = "off"

        # Track for INFO log: what limited the size (cap vs cash)
        _sizing_conv, _sizing_target_pct, _sizing_target_value = (
            None,
            None,
            None,
        )  # noqa: E501

        if dynamic_enabled and current_price > 0:
            # Clamp conviction to 0-1 range
            conv = max(0.0, min(1.0, conviction_score))
            if clean_weight_mode in ("a", "b"):
                # #3284 Variant 2 (per-symbol): ONE target-weight authority. Conviction
                # LEAVES the size lever (it stays in direction/admission — Grinold-Kahn:
                # signal once). final_risk_scaler is DELIBERATELY NOT applied here: the
                # vix-ladder / confidence / size_scaler / reduction factors are dropped as
                # correlated double-counters. Arm "a" = strict 1/N (equal-DOLLAR, DeMiguel
                # baseline). Arm "b" = 1/N x the ONE retained risk input (vol-targeting) x
                # the live switchable tilts (skew/coverage) — which already carry their own
                # flag gating (== 1.0 when off), so they compose here and a flipped switch
                # genuinely moves the order value (no dead control; MiFID II Art. 17).
                n_slots = _rm.effective_max_positions()
                base_w = 1.0 / float(max(1, int(n_slots)))
                if clean_weight_mode == "b":
                    clean_w = base_w * vol_scaler * skew_tilt * coverage_discount
                else:  # "a"
                    clean_w = base_w
                # Hard MAX cap only. NO MIN floor after the vol-tilt: flooring the tilted
                # weight back up to MIN_POSITION_PERCENT would neuter vol de-risking at
                # N=20 (base 1/N == MIN). The base 1/N >= MIN is guaranteed by the N<=20
                # book cap (portfolio_shape.py); the downstream dust/min-order floor bounds
                # negligible orders. (#3284 plan §8)
                target_position_pct = min(clean_w, max_pos_cap_pct)
                target_position_value = self.total_capital * target_position_pct
                num_shares = target_position_value / current_price
                logging.debug(
                    "Clean-weight sizing (%s): 1/N=%.4f -> %.1f%% = $%.2f"
                    % (
                        clean_weight_mode,
                        base_w,
                        target_position_pct * 100,
                        target_position_value,
                    )
                )
            else:
                # Linear interpolation: low conviction = small (e.g. 2%), high = large (e.g. 30%)  # noqa: E501
                target_position_pct = (
                    min_pos_pct + (max_pos_pct_sizing - min_pos_pct) * conv
                )
                target_position_pct = min(
                    target_position_pct, max_pos_cap_pct
                )  # Cap by MAX_POSITION_PERCENT
                target_position_value = (
                    self.total_capital * target_position_pct * final_risk_scaler
                )
                num_shares = target_position_value / current_price
                logging.debug(
                    f"Dynamic sizing: conviction={conv:.2f} -> {target_position_pct * 100:.1f}% = ${target_position_value:.2f}"  # noqa: E501
                )
            _sizing_conv, _sizing_target_pct, _sizing_target_value = (
                conv,
                target_position_pct,
                target_position_value,
            )
            # #3284 Punkt 3 (MiFID II Art. 17 „applied == logged"): record HOW the size
            # was formed so the decision log is unambiguous now that clean-weight "b" is
            # the default. "conviction" for the legacy path, "a"/"b" for the clean modes;
            # sizing_target_weight = the target base weight (pre-cap). Observation-only
            # (the sink never changes the returned size; a broken sink is ignored).
            _note(  # pragma: no cover
                "sizing_mode",
                clean_weight_mode if clean_weight_mode in ("a", "b") else "conviction",
            )
            if sizing_trace is not None:  # pragma: no cover
                try:
                    sizing_trace["sizing_target_weight"] = float(target_position_pct)
                except Exception:  # noqa: BLE001 — audit sink must never break sizing
                    logging.warning("Audit sink sizing traceback", exc_info=True)
        else:
            # Fallback to old risk-based calculation
            if num_stocks_in_strategy <= 0:
                num_stocks_in_strategy = 1
            capital_to_risk_total = (
                self.total_capital * self.risk_per_trade_percent
            )  # noqa: E501
            capital_to_risk_per_stock = (
                capital_to_risk_total / num_stocks_in_strategy
            ) * final_risk_scaler
            dollar_risk_per_share = atr * stop_loss_atr_multiplier
            if dollar_risk_per_share <= 0:
                return None  # caller returns 0 (int), as before #3829
            num_shares = capital_to_risk_per_stock / dollar_risk_per_share
        return (
            num_shares,
            _sizing_conv,
            _sizing_target_pct,
            _sizing_target_value,
            clean_weight_mode,
            max_pos_pct_sizing,
        )


from core import risk_manager as _rm  # noqa: E402
