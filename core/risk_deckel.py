# risk_deckel.py
# --- Deckel der Bemessung: Position, Cash, Exposure, Kelly, Verlust, Compliance, Floor (#4267, H-3e) ---

"""Deckel des ``RiskManager`` — die fünf ``_step_*`` samt Helfern als ``DeckelMixin``.

Umgezogen aus ``core/risk_manager.py`` (#4267, H-3e). Schnitt-Entscheidung #4185:
``docs/3738-arc-e6-gestalt/H3_SCHNITT_risk_manager.md`` §2 und §3.

Zugriffsregel (Entscheidung §3): Namen, die Tests am Modulobjekt ``core.risk_manager``
patchen, liest dieses Modul **nur** als ``_rm.<name>`` — hier ``_rm.effective_max_positions``
(``tests/unit/test_vix_size_influence_3619.py``). Ein freier Name liefe am Patch still vorbei.
Der Kern wird erst am Dateiende importiert, nach der Klasse: Der Kern importiert dieses Modul
für seine Basisklasse (Zirkelimport). Das Mixin hat kein ``__init__``; den Zustand
(``total_capital``, ``client``, ``max_loss_per_trade``, ``_policy_max_order_value``) liest es
über ``self``.

Die ``config``-Zugriffe bleiben spät, im Rumpf der Methoden, wie vor dem Umzug. Jeder
``_step_<wert>`` schreibt ``_note("binding_limit", "<wert>")``; die Deckel-Spur
(``tests/unit/test_sizing_steps.py``) liest die Schreiber über die Methodentabelle.
"""

import logging
from typing import Tuple


class DeckelMixin:
    """Deckel der Positionsbemessung, aufgerufen aus ``calculate_position_size``."""

    def _step_position_cap(
        self, num_shares, current_price, _note
    ) -> Tuple[float, bool]:
        """Cap `position_cap`: MAX_POSITION_PERCENT of total capital (#3829)."""
        # 4. Capital Constraint (configurable via config.MAX_POSITION_PERCENT)
        limited_by_cap = False
        if current_price > 0:
            try:
                from config import MAX_POSITION_PERCENT

                max_position_value_pct = MAX_POSITION_PERCENT
            except ImportError:
                max_position_value_pct = 0.25  # Default 25%
            max_position_value = self.total_capital * max_position_value_pct
            max_shares_by_cap = max_position_value / current_price
            if num_shares > max_shares_by_cap:
                num_shares = max_shares_by_cap
                limited_by_cap = True
                _note("binding_limit", "position_cap")
                _note("binding_cap_value", f"{max_position_value:.2f}")  # #3619
        return num_shares, limited_by_cap

    def _step_cash(
        self,
        num_shares,
        current_price,
        account_cash,
        num_stocks_in_strategy,
        clean_weight_mode,
        _sizing_target_value,
        max_pos_pct_sizing,
        _note,
    ) -> Tuple[float, bool]:
        """Cap `cash`: no order above the free cash slot (ADR-R09/R10, #3829)."""
        # 5. CASH CONSTRAINT — Kein Order über verfügbares Cash
        # ADR-R09: Slippage-Buffer = $50
        # Begründung: Alpaca Paper/Live hat bid/ask-Spread + Commissions; $50 Puffer deckt  # noqa: E501
        # bei typischen Aktienkursen ($10-$500) 0.01%-0.5% Slippage ab.
        # Bei sehr teuren Aktien (> $1000, z.B. NVDA) ggf. auf $100 erhöhen.
        # ADR-R10: Hebel-Verbot — eine Order darf den freien Cash nie übersteigen.  # noqa: E501
        # Basis: FINRA Reg-T erlaubt 2x Kaufkraft; Alpaca räumt sie Paper- wie Live-Konten  # noqa: E501
        # ein. Nichts im Code hat sie bisher abgelehnt.
        #
        # DER FEHLER: `account_cash > 0` hat diesen ganzen Block bewacht. Ein Konto, das  # noqa: E501
        # bereits im Minus steht (cash < 0, d.h. geliehen), übersprang den Cash-Deckel damit  # noqa: E501
        # KOMPLETT — der eine Zustand, in dem "nicht bezahlbar" sicher feststeht, war der  # noqa: E501
        # eine ungeprüfte. Die Klammer `max_shares_by_cash < 0 -> 0` unten war genau dafür  # noqa: E501
        # geschrieben und bis hierhin unerreichbar.
        #
        # ⚠ DEFAULT AN, UND ES ÄNDERT LIVE-VERHALTEN. Ein früherer Entwurf behauptete hier,  # noqa: E501
        # die Regel sei "ein No-op, solange MAX_TOTAL_EXPOSURE_PCT greift, weil cash < 0  # noqa: E501
        # gleichbedeutend mit Exposure > 100 % ist". Das ist WIDERLEGT (adversarialer Review,  # noqa: E501
        # ausgeführt): der Deckel misst gegen `self.total_capital` — einen Schnappschuss aus  # noqa: E501
        # der Konstruktion (:52 ist der einzige Schreibzugriff im ganzen Repo; ein  # noqa: E501
        # `update_total_capital` existiert nur im PortfolioManager) — während `cash` live ist.  # noqa: E501
        # Gemessenes Gegenbeispiel: RM bei 100k gebaut, Equity fällt auf 60k, Positionen 60k,  # noqa: E501
        # Cash 0.00, Client da, keine Exception → der Deckel gewährt 35.000 Spielraum:  # noqa: E501
        #     Flag AUS -> 99.99 Shares ($9.999)   Flag AN -> 0.0
        # Die Regel bindet also, WÄHREND der Deckel funktioniert. Das Blockieren ist dort  # noqa: E501
        # richtig (man kauft nicht für 9.999 $ mit 0 $ Cash) — aber es ist ein echter  # noqa: E501
        # Eingriff, kein No-op, und wird hier nicht schöngeredet.
        #
        # WAS DEN DEFAULT TRÄGT, ist allein die Einseitigkeit: die Regel kann eine Order nur  # noqa: E501
        # VERKLEINERN, nie vergrößern. Über 53.760 adversariale Kombinationen (Client an/aus ×  # noqa: E501
        # Positions-Sets inkl. negativer market_value × cash von -1e9 bis 1e6 inkl. ±inf/NaN ×  # noqa: E501
        # slots × Preise × ATR × Conviction × allow_fractional) verletzungsfrei; jede Stufe  # noqa: E501
        # NACH diesem Block ist monoton nicht-fallend in num_shares. Ihr schlimmster Fall ist  # noqa: E501
        # ein verpasster Trade, nie ein zusätzliches Risiko.
        # Beweis + Grid: tests/unit/test_risk_forbid_leverage.py (Sicherheitsaxiom).  # noqa: E501
        #
        # ⚠ NEBENWIRKUNG, absichtlich behalten: `rl_entscheidung.py:380-382` fängt einen  # noqa: E501
        # get_account-Fehler, loggt und setzt `cash = 0.0` — und fällt DURCH zum Sizer (beide  # noqa: E501
        # return-Wächter liegen innerhalb des try). Dort heißt cash=0.0 also auch  # noqa: E501
        # "Broker nicht erreichbar". Mit Flag AN wird daraus "kein neuer Kauf" statt bisher  # noqa: E501
        # "kaufe blind, gesized auf einen Kapital-Schnappschuss, ohne das Konto lesen zu  # noqa: E501
        # können". Das ist fail-CLOSED und damit CLAUDE.md 5.6 / BUG-AI-105 konform — aber es  # noqa: E501
        # IST eine Verhaltensänderung, und sie steht hier, weil ein früherer Entwurf sie  # noqa: E501
        # bestritten hat.
        #
        # NaN: `float("nan")` entkommt der Regel (jeder Vergleich ist False). Erreichbar nur,  # noqa: E501
        # wenn der Broker wörtlich "nan" liefert. Bekannt, nicht abgedeckt.
        # Der Import löst auf BEIDEN Editionen auf — auf der Desktop-Edition als  # noqa: E501
        # modul-level Name (seit #3553 settings.py::RISK_FORBID_LEVERAGE), auf der Enterprise-Edition über das  # noqa: E501
        # PEP-562 `__getattr__` (config.py::__getattr__), das auf `_config_state` proxyt.  # noqa: E501
        # Verifiziert durch Ausführung, nicht angenommen: ein früherer Entwurf behauptete  # noqa: E501
        # hier einen ImportError auf Enterprise — falsch, `from config import
        # RISK_FORBID_LEVERAGE` liefert dort True. Das `except` ist reine Gürtel-und-  # noqa: E501
        # Hosenträger für ein gänzlich fehlendes config-Modul, kein Editions-Unterschied.  # noqa: E501
        try:
            from config import RISK_FORBID_LEVERAGE

            forbid_leverage = bool(RISK_FORBID_LEVERAGE)
        except (ImportError, AttributeError):
            forbid_leverage = True

        limited_by_cash = False
        if current_price > 0 and (account_cash > 0 or forbid_leverage):
            # Diversified cash allocation: split available cash across the strategy's target  # noqa: E501
            # universe (equal-weight slots) instead of letting the first BUYs of a cycle consume  # noqa: E501
            # it all — N concurrent BUYs then collectively fit the budget rather than the tail  # noqa: E501
            # being silently dropped at the broker buying-power gate. slots=1 (single-symbol /  # noqa: E501
            # default) preserves the original full-cash behaviour.
            # ADR-R09 (revised 2026-07-27): the slippage/rounding buffer is RISK_CASH_BUFFER_USD (default  # noqa: E501
            # $1 = Alpaca's minimum), not a flat $50. The flat $50 was 25% of a $200 account and — split  # noqa: E501
            # across the slots — zeroed every small-account order below the dust floor. The real slippage  # noqa: E501
            # cushion is the 5% MAX_TOTAL_EXPOSURE headroom (§5b), not this buffer. Module read on both  # noqa: E501
            # editions (config.oss.py module-level / config.py PEP-562); fail-safe $1 if config is absent.  # noqa: E501
            slots = max(1, int(num_stocks_in_strategy or 1))
            try:
                from config import RISK_CASH_BUFFER_USD

                _cash_buffer = float(RISK_CASH_BUFFER_USD)
            except (ImportError, AttributeError):
                _cash_buffer = 1.0
            _demand_fraction = self._sizing_cash_demand_fraction(
                clean_weight_mode, _sizing_target_value, max_pos_pct_sizing
            )
            max_shares_by_cash = (
                ((account_cash - _cash_buffer) / slots) * _demand_fraction
            ) / current_price
            if max_shares_by_cash < 0:
                max_shares_by_cash = 0
            if num_shares > max_shares_by_cash:
                if (
                    forbid_leverage
                    and num_shares > 0
                    and max_shares_by_cash <= 0  # noqa: E501
                ):  # noqa: E501
                    # ADR-R10 / CLAUDE.md 5.6: the refusal MUST say so. The INFO log below is  # noqa: E501
                    # gated on `num_shares > 0` and therefore never fires for exactly the case  # noqa: E501
                    # this rule creates — a zeroed order. Without this line the guard would be  # noqa: E501
                    # a new SILENT producer of zeros, in a system whose orders already die  # noqa: E501
                    # unexplained (signal_desktop_entscheid.py:75 `if qty > 0:` has no else; its  # noqa: E501
                    # sibling at :631 logs the same event at DEBUG). Refusing a trade without  # noqa: E501
                    # saying why is how 1013 orders vanished on 2026-07-16.
                    logging.warning(
                        "[ADR-R10] Order refused: no leverage. cash=%.2f over %d slot(s) "  # noqa: E501
                        "buys %.4f shares at %.2f — requested %.4f. Set "
                        "RISK_FORBID_LEVERAGE=false to allow margin.",
                        account_cash,
                        slots,
                        max_shares_by_cash,
                        current_price,
                        num_shares,
                    )
                num_shares = max_shares_by_cash
                limited_by_cash = True
                # The single most common zero on small accounts: (cash − buffer) / slots  # noqa: E501
                # under one fractional share. Recorded HERE so the funnel exit below  # noqa: E501
                # cannot claim it as "below_order_floor".
                _note("binding_limit", "cash")
                # #3619: the dollar value of the slot that bound (audit; the record
                # shows why a vol-scaled target did not reach the order).
                _note("binding_cap_value", f"{max_shares_by_cash * current_price:.2f}")
                if num_shares <= 0 or (
                    current_price > 0 and num_shares < 1.0 / current_price
                ):
                    _note("zero_reason", "insufficient_cash")
        return num_shares, limited_by_cash

    def _sizing_cash_demand_fraction(
        self, clean_weight_mode, _sizing_target_value, max_pos_pct_sizing
    ) -> float:
        """Share of the equal cash slot this position may claim, in [0, 1] (#3829)."""
        # ADR-R11 (#3135): proportional slot-cash. The equal (cash-buffer)/slots split  # noqa: E501
        # caps every concurrent BUY at the SAME dollar amount, erasing the IV/conviction  # noqa: E501
        # size differentiation #3094 (VIXAware-IV -> forecast_vol -> vol-targeting) and the  # noqa: E501
        # conviction sizer produce upstream (measured live: 8 buys, risk_size_scaler  # noqa: E501
        # 0.50-1.089, all filled ~$1,005). When PROPORTIONAL_SLOT_CASH_ENABLED is on, scale  # noqa: E501
        # the slot by this position's own target value relative to the largest a single  # noqa: E501
        # position could target (total_capital * MAX_POSITION_PERCENT_SIZING). The fraction  # noqa: E501
        # is bounded to [0,1] so the cap can only SHRINK vs the equal split => the  # noqa: E501
        # no-leverage safety axiom (sum of concurrent budgets <= cash) holds a fortiori  # noqa: E501
        # (tests/unit/test_risk_forbid_leverage.py grid). Flag OFF or non-dynamic sizing  # noqa: E501
        # (_sizing_target_value is None) => 1.0 => byte-identical equal split. No gross  # noqa: E501
        # renormalisation (idle cash left when convictions < max) — consistent with #3094.  # noqa: E501
        # Basis: MiFID II Art. 17 sizing governance. Annual review.
        _demand_fraction = 1.0
        if clean_weight_mode in ("a", "b"):  # pragma: no cover
            # #3284: clean-weight couples cash to the 1/N[×vol] target — ALWAYS (not
            # gated on PROPORTIONAL_SLOT_CASH). Normalise the slot demand against the
            # EQUAL slot (1/N), NOT the conviction 30% scale: a ≤15% clean target
            # against the 0.30 denominator claims ≤0.5 of its slot, so the book
            # under-invests (measured 2-day probe: arm b filled ~$45k of $100k). With
            # the 1/N denominator: arm "a" (w=1/N) → fraction 1.0 = the full equal slot;
            # arm "b" calm (vol>1) clamps to 1.0 (fills its slot, no per-symbol up-tilt
            # without the concurrent set — Phase 1b); arm "b" volatile (vol<1) → fraction
            # vol < 1 (de-risked below the slot). Clamp [0,1] keeps the no-leverage axiom
            # (each budget ≤ the equal slot ⇒ Σ ≤ cash a fortiori — same proof as below).
            _n_slots = _rm.effective_max_positions()
            _slot_w = 1.0 / float(max(1, int(_n_slots)))
            if (
                _sizing_target_value is not None
                and _slot_w > 0
                and self.total_capital > 0
            ):
                _w_i = _sizing_target_value / self.total_capital
                _demand_fraction = max(0.0, min(1.0, _w_i / _slot_w))
        elif _sizing_target_value is not None and max_pos_pct_sizing > 0:
            try:
                from config import (  # noqa: E501
                    PROPORTIONAL_SLOT_CASH_ENABLED as _prop_slot,
                )
            except (ImportError, AttributeError):
                _prop_slot = False
            if _prop_slot:
                _ref_value = self.total_capital * max_pos_pct_sizing
                if _ref_value > 0:
                    _demand_fraction = max(
                        0.0, min(1.0, _sizing_target_value / _ref_value)
                    )
        return _demand_fraction

    def _step_total_exposure_cap(self, num_shares, current_price, _note) -> float:
        """Cap `total_exposure_cap`: MAX_TOTAL_EXPOSURE_PCT, fail-closed (#3829)."""
        # 5b. TOTAL EXPOSURE CAP (optional): sum of position values <= total_capital * MAX_TOTAL_EXPOSURE_PCT  # noqa: E501
        try:
            max_exposure_pct = getattr(
                __import__("config", fromlist=["MAX_TOTAL_EXPOSURE_PCT"]),
                "MAX_TOTAL_EXPOSURE_PCT",
                None,
            )
            if (
                max_exposure_pct is not None
                and current_price > 0
                and hasattr(self, "client")
                and self.client is not None
            ):
                try:
                    # BUG-AI-105 (part 2): this broker re-fetch is INTENTIONAL
                    # per sizing call - do NOT replace it with a cached / once-
                    # per-cycle snapshot. The cap must see positions opened
                    # earlier in the SAME cycle: otherwise N BUYs sized off one
                    # stale snapshot would each see full headroom and together
                    # breach MAX_TOTAL_EXPOSURE_PCT (re-opening the fail-open).
                    # The N+1 is the price of a correct aggregate cap.
                    positions = self.client.get_all_positions()
                    total_position_value = 0.0
                    for p in positions:
                        mv = (
                            p.get("market_value", None)
                            if isinstance(p, dict)
                            else getattr(p, "market_value", None)
                        )
                        if mv is not None:
                            total_position_value += float(mv)
                    max_new_exposure = (
                        self.total_capital * float(max_exposure_pct)
                        - total_position_value
                    )
                    if max_new_exposure < (num_shares * current_price):
                        num_shares = max(0.0, max_new_exposure / current_price)
                        _note("binding_limit", "total_exposure_cap")
                        _note(
                            "binding_cap_value", f"{max(0.0, max_new_exposure):.2f}"
                        )  # #3619
                        if num_shares <= 0 or (
                            current_price > 0
                            and num_shares < 1.0 / current_price  # noqa: E501
                        ):
                            _note("zero_reason", "total_exposure_cap")
                        # ADR-R12 (#2960): a clamped residue below the entry
                        # floor is dropped, not bought — headroom leftovers of  # noqa: E501
                        # $1-$5 became standalone dust positions. ONLY inside
                        # this clamp branch: a normal cash-/pct-bound order
                        # never reaches it (the #2784 small-account objection).
                        # Default 0.0 = OFF = today's behavior byte-identical.
                        try:
                            from config import (  # noqa: E501
                                EXPOSURE_CLAMP_MIN_NOTIONAL_USD as _clamp,
                            )

                            _entry_floor = float(_clamp)
                        except (
                            ImportError,
                            AttributeError,
                            TypeError,
                            ValueError,
                        ) as _exc:
                            # §5.6: a fallback substitution logs at WARNING,
                            # never silently (review apeldorn, PR #2961).
                            logging.warning(
                                "EXPOSURE_CLAMP_MIN_NOTIONAL_USD unreadable (%s) "  # noqa: E501
                                "— clamp entry floor OFF this sizing pass.",
                                _exc,
                            )
                            _entry_floor = 0.0  # fail-safe: floor OFF
                        if (
                            _entry_floor > 0.0
                            and current_price > 0
                            and 0.0
                            < (num_shares * current_price)
                            < _entry_floor  # noqa: E501
                        ):
                            logging.info(
                                "ADR-R12 clamp entry floor: dropping $%.2f "
                                "headroom residue (< $%.2f floor)",
                                num_shares * current_price,
                                _entry_floor,
                            )
                            num_shares = 0.0
                            _note("zero_reason", "exposure_clamp_entry_floor")
                except Exception as e:
                    # BUG-AI-105: fail CLOSED, not open. If the aggregate
                    # exposure cannot be verified (e.g. the broker positions
                    # query fails), do NOT silently skip the cap and let the
                    # order breach MAX_TOTAL_EXPOSURE_PCT - add no new exposure
                    # this sizing pass (CLAUDE.md 5.6).
                    logging.warning(
                        "Total-exposure cap check failed "
                        "(get_all_positions/valuation error): %s "
                        "- failing CLOSED: no new exposure added this sizing pass.",  # noqa: E501
                        e,
                        exc_info=True,
                    )
                    num_shares = 0.0
                    _note("zero_reason", "exposure_check_failed")
        except ImportError as _e:
            # CLAUDE.md §5.6: config is a core module — an ImportError here is a real anomaly.  # noqa: E501
            # The aggregate MAX_TOTAL_EXPOSURE_PCT cap is skipped this pass (per-order cash/max-%  # noqa: E501
            # caps above still apply); log rather than swallow silently.
            logging.warning(
                "config import failed (%s) — MAX_TOTAL_EXPOSURE_PCT aggregate cap NOT applied "  # noqa: E501
                "this sizing pass; per-order caps still apply.",
                _e,
            )
        return num_shares

    def _step_kelly(self, num_shares, _note) -> float:
        """Cap `kelly`: KELLY_FRACTION_CAP (#3829)."""
        # 5c. KELLY FRACTION CAP (optional): scale position size to avoid over-betting in hot streaks  # noqa: E501
        try:
            kelly_cap = getattr(
                __import__("config", fromlist=["KELLY_FRACTION_CAP"]),
                "KELLY_FRACTION_CAP",
                None,
            )
            if kelly_cap is not None and num_shares > 0:
                old_shares = num_shares
                num_shares = num_shares * float(kelly_cap)
                if num_shares < old_shares:
                    _note("binding_limit", "kelly")
        except ImportError:
            pass
        return num_shares

    def _step_max_loss_per_trade(
        self, num_shares, atr, stop_loss_atr_multiplier, current_price, _note
    ) -> float:
        """Cap `max_loss_per_trade`: loss at the stop (ADR-R06, #3829)."""
        # 6. STOP-LOSS CONSTRAINT (Limit max loss per trade)
        if stop_loss_atr_multiplier > 0 and atr > 0 and current_price > 0:
            dollar_loss_at_sl = num_shares * atr * stop_loss_atr_multiplier

            if dollar_loss_at_sl > self.max_loss_per_trade:
                max_shares_by_sl = self.max_loss_per_trade / (
                    atr * stop_loss_atr_multiplier
                )
                if max_shares_by_sl < num_shares:
                    _note("binding_limit", "max_loss_per_trade")
                    num_shares = max(0, max_shares_by_sl)
        return num_shares

    def _step_compliance_order_value(self, num_shares, current_price, _note) -> float:
        """Cap `compliance_order_value`: max order value (ADR-C01, #3829)."""
        # 7. COMPLIANCE MAX-ORDER-VALUE CAP (ADR-C01)
        # A single order's notional (qty × price) must never exceed COMPLIANCE_MAX_ORDER_VALUE  # noqa: E501
        # (default 10,000; ADR-C01 revidiert #3219: operativer Fat-Finger-Deckel, KEIN  # noqa: E501
        # regulatorisches Gebot — MiFID II Art. 57 betrifft Warenderivate) — the SAME hard  # noqa: E501
        # limit the ComplianceGuardian enforces post-sizing (core/compliance.py _check_risk_limits:  # noqa: E501
        # ``value > max_order_value``). Without capping here, a risk-sized order above the limit is  # noqa: E501
        # built and then HARD-BLOCKED downstream → NO trade ever executes (observed: every desktop  # noqa: E501
        # BUY 🛡️ BLOCKED "Order exceeds Max Order Value"). Sizing to fit lets the order pass.  # noqa: E501
        # Applied LAST (after the multiplicative Kelly step) so nothing can re-inflate it.  # noqa: E501
        if current_price > 0:
            try:
                if self._policy_max_order_value is not None:
                    # DEF-1 (#3217): eine angewandte Iron-Dome-Policy hat Vorrang —  # noqa: E501
                    # derselbe Wert, den ComplianceGuardian blockt. Ohne Policy
                    # (None) bleibt der Config-Read unten byte-identisch, damit
                    # die bestehenden Tests, die ``config.COMPLIANCE_MAX_ORDER_VALUE``  # noqa: E501
                    # patchen, unverändert gelten.
                    _max_order_value = float(self._policy_max_order_value)
                else:
                    # module-level read (same pattern as the MAX_TOTAL_EXPOSURE_PCT / KELLY caps  # noqa: E501
                    # above); in practice identical to compliance.py's get_config() value.  # noqa: E501
                    _max_order_value = float(
                        getattr(
                            __import__(
                                "config",
                                fromlist=["COMPLIANCE_MAX_ORDER_VALUE"],  # noqa: E501
                            ),  # noqa: E501
                            "COMPLIANCE_MAX_ORDER_VALUE",
                            0.0,
                        )
                        or 0.0
                    )
            except Exception as _e:
                # CLAUDE.md §5.6: a fallback that changes money behaviour MUST log at WARNING.  # noqa: E501
                # 0.0 disables the sizing-time compliance cap (ComplianceGuardian still HARD-blocks  # noqa: E501
                # downstream); surface that the cap went inactive this pass.
                logging.warning(
                    "COMPLIANCE_MAX_ORDER_VALUE unreadable (%s) — per-order compliance size-cap "  # noqa: E501
                    "NOT applied this sizing pass (ComplianceGuardian still enforces downstream).",  # noqa: E501
                    _e,
                )
                _max_order_value = 0.0
            if _max_order_value > 0:
                # 0.9999 = a hair of headroom: the Guardian rejects on a STRICT ``>``, so the  # noqa: E501
                # capped notional must stay just UNDER the limit (float / fee tolerance).  # noqa: E501
                max_shares_by_compliance = (
                    _max_order_value * 0.9999
                ) / current_price  # noqa: E501
                if num_shares > max_shares_by_compliance:
                    num_shares = max_shares_by_compliance
                    _note("binding_limit", "compliance_order_value")
                    _note(
                        "binding_cap_value", f"{_max_order_value * 0.9999:.2f}"
                    )  # #3619
        return num_shares

    def _sizing_order_floor(
        self, num_shares, current_price, allow_fractional, sizing_trace, _note
    ) -> float:
        """Order floors at the funnel exit, then rounding (#3829)."""
        # ADR-R10: Mindestpositionswert = $1 (Alpaca Fractional Shares Minimum)
        # Basis: Alpaca API-Dokumentation — kleinste handelbare Einheit bei Fractional Shares  # noqa: E501
        # Begründung: Unter $1 würde Alpaca die Order ablehnen (API Error 422);  # noqa: E501
        # 0.001-Shares-Fallback greift bei current_price=0 (Datenfehler) als Sicherheitsnetz.  # noqa: E501
        if current_price > 0:
            min_fractional_shares = 1.0 / current_price
            if num_shares < min_fractional_shares:
                # FUNNEL EXIT (#2811): every upstream clamp leaves through here. Only  # noqa: E501
                # claim the label when no clamp recorded a cause — otherwise this exit  # noqa: E501
                # would rename a cash/exposure zero to "below_order_floor".
                if (  # pragma: no cover
                    sizing_trace is not None
                    and "zero_reason" not in sizing_trace  # noqa: E501
                ):  # noqa: E501
                    _note("zero_reason", "below_order_floor")
                return 0.0
        elif (
            num_shares < 0.001
        ):  # Fallback: Datenfehler-Schutz (current_price nicht verfügbar)
            if sizing_trace is not None and "zero_reason" not in sizing_trace:
                _note("zero_reason", "invalid_price")
            return 0.0

        # Round to 6 decimal places (Alpaca supports up to 9)
        num_shares = round(num_shares, 6)

        # If fractional not allowed, convert to int
        if not allow_fractional:
            num_shares = int(num_shares)
            if num_shares < 1:
                if (  # pragma: no cover
                    sizing_trace is not None
                    and "zero_reason" not in sizing_trace  # noqa: E501
                ):  # noqa: E501
                    _note("zero_reason", "below_one_whole_share")
                return 0.0

        # ADR-R11 (revised 2026-07-27): Dust-Filter Threshold = MIN_ORDER_VALUE_USD (default $1).  # noqa: E501
        # Basis: Alpaca supports fractional/notional orders at $0 commission — the old $50 rationale  # noqa: E501
        # (spread + commission drag on sub-$50 orders) is void for the liquid trading universe. $1 is  # noqa: E501
        # Alpaca's real notional minimum (orders < $1 → API 422; also guarded at :858-861). A flat $50  # noqa: E501
        # zeroed EVERY order on a funded small account (observed live: €220 account, all BUYs blocked).  # noqa: E501
        # DUST-FILTER (EXC-1): Block nominal values < MIN_ORDER_VALUE_USD
        if current_price > 0 and num_shares > 0:
            nominal_value = num_shares * current_price
            try:
                from config import MIN_ORDER_VALUE_USD

                min_value = float(MIN_ORDER_VALUE_USD)
            except (ImportError, AttributeError):
                min_value = 1.0  # Fail-safe = Alpaca's $1 minimum (config defines it explicitly)  # noqa: E501

            if nominal_value < min_value:
                logging.info(
                    f"Dust-Filter: Rejected trade. Nominal value ${nominal_value:.2f} "  # noqa: E501
                    f"is below minimum of ${min_value:.2f}"
                )
                if (  # pragma: no cover
                    sizing_trace is not None
                    and "zero_reason" not in sizing_trace  # noqa: E501
                ):  # noqa: E501
                    _note("zero_reason", "below_order_floor")
                return 0.0

        return float(num_shares)


from core import risk_manager as _rm  # noqa: E402
