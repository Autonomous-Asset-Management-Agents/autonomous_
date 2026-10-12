# risk_vorpruefung.py
# --- Vorprüfung einer neuen Order: Halt, VIX fail-closed, KI-Regeln (#4265, H-3c) ---

"""Vorprüfung des ``RiskManager`` — ``evaluate_new_trade`` als ``VorpruefungMixin``.

Umgezogen aus ``core/risk_manager.py`` (#4265, H-3c). Schnitt-Entscheidung #4185:
``docs/3738-arc-e6-gestalt/H3_SCHNITT_risk_manager.md`` §2 und §3.

Zugriffsregel (Entscheidung §3): Namen, die Tests am Modulobjekt ``core.risk_manager``
patchen, liest dieses Modul **nur** als ``_rm.<name>`` — hier ``_rm.tracer`` (der Tracer heißt
damit weiter ``core.risk_manager``) und ``_rm.resolve_vix``. Ein freier Name liefe am Patch
still vorbei. Der Kern wird erst am Dateiende importiert, nach der Klasse: Der Kern importiert
dieses Modul für seine Basisklasse (Zirkelimport). Das Mixin hat kein ``__init__``; den Zustand
(``trading_halted``, ``_halt``, ``ai_rules_singleton``, ``user_id``) liest es über ``self``.

Benannte Schritte (#4265, H-3d, Muster G-1b): Ein Schritt liefert ein Abbruch-Ergebnis statt
selbst zurückzukehren. Der Span ``risk.evaluate_trade`` und **alle** seine Attribute bleiben im
Dirigenten ``evaluate_new_trade``, ebenso die fail-open-Grenze je Regel (#1236).
"""

import logging
from typing import Any, Dict, Optional, Tuple

_Ergebnis = Tuple[bool, str, Dict[str, Any]]


class VorpruefungMixin:
    """Vorprüfung einer neuen Order gegen Halt, VIX und KI-Regeln."""

    def _vorpruefung_halt(self) -> Optional[_Ergebnis]:
        """Schritt Halt: Abbruch-Ergebnis, wenn gehalten — sonst ``None``.

        Liegt vor dem Span: Ein Halt erzeugt keinen ``risk.evaluate_trade``-Span."""
        # === EARLY EXIT: If halted (locally or via KillSwitch), no new trades ===  # noqa: E501
        if self.trading_halted or self._halt().is_halted(self.user_id):
            return (
                False,
                "System or User HALTED by Kill Switch / Risk Manager",
                {},
            )  # noqa: E501
        return None

    def _vorpruefung_vix(
        self, symbol: str, side: str, market_data: Dict[str, Any]
    ) -> Tuple[Optional[_Ergebnis], float]:
        """Schritt VIX: ``(Abbruch-Ergebnis oder None, loop_vix)``.

        Bei Abbruch ist ``loop_vix`` bedeutungslos (``0.0``). Die WARNING trägt mit
        ``stacklevel=2`` den Funktionsnamen des Dirigenten, wie vor der Zerlegung — das
        H-3a-Netz schreibt ``funcName`` mit."""
        # #2980 (ADR-R09): fail-closed VIX gate. When the volatility gauge is
        # UNKNOWN, protective rules keyed on `vix_gt` can no longer fire (the old  # noqa: E501
        # 0.0 default silently disarmed every "block buys when VIX > N" rule).
        # We therefore block NEW BUYS outright while VIX is unconfirmed — but
        # SELL / de-risk paths stay open ("only reduce, never enlarge"): a book
        # must always be reducible even when it cannot be grown.
        _vix_value, vix_confirmed, _vix_source = _rm.resolve_vix(market_data)
        if not vix_confirmed and side.lower() == "buy":
            logging.warning(
                "[VIX] evaluate_new_trade(%s): VIX unconfirmed — new BUY blocked "  # noqa: E501
                "(fail-closed §5.6). SELL / de-risk unaffected.",
                symbol,
                stacklevel=2,
            )
            return (
                False,
                "Blocked (fail-closed, §5.6): VIX unconfirmed — new buys blocked",  # noqa: E501
                {},
            ), 0.0
        # For a confirmed VIX use the real value in the rule loop; for an
        # UNCONFIRMED SELL keep the benign 0.0 read so the fail-closed sentinel
        # (45.0) does not NEWLY trip a `vix_gt` block on a de-risk order.
        loop_vix = _vix_value if vix_confirmed else 0.0
        return None, loop_vix

    @staticmethod
    def _regel_passt(
        trigger: Dict[str, Any],
        side: str,
        market_data: Dict[str, Any],
        loop_vix: float,
        features: Dict[str, Any],
    ) -> bool:
        """Schritt Match: ob der Trigger einer Regel auf diese Order passt.

        Wertet jede Bedingung in der festen Reihenfolge aus, ohne früher zurückzukehren —
        eine Ausnahme aus einer späteren Prüfung (etwa ``vix_gt`` als String) muss die
        fail-open-WARNING im Dirigenten auslösen. Nur die Feature-Schleife endet beim ersten
        Fehlschlag."""
        rule_matches = True

        # --- Match Logic ---
        if (
            trigger.get("side")
            and trigger["side"].lower() != side.lower()  # noqa: E501
        ):  # noqa: E501
            rule_matches = False
        if trigger.get("strategy") and trigger["strategy"] not in market_data.get(
            "strategy_name", ""
        ):
            rule_matches = False

        # VIX Checks — #2980: use the resolver-provided value
        # (confirmed real VIX, or benign 0.0 for an unconfirmed SELL;  # noqa: E501
        # unconfirmed BUYs already returned blocked above).
        current_vix = loop_vix
        if trigger.get("vix_gt") is not None and current_vix <= trigger["vix_gt"]:
            rule_matches = False
        if trigger.get("vix_lt") is not None and current_vix >= trigger["vix_lt"]:
            rule_matches = False

        # Dynamic Feature Checks (RSI, ADX, etc.)
        for key, val in trigger.items():
            if key.startswith("indicators.features."):
                parts = key.split(".")
                feature_name = parts[-2]
                condition = parts[-1]
                curr_val = float(features.get(feature_name, 0.0))

                if condition == "gt" and curr_val <= float(val):
                    rule_matches = False
                    break
                elif condition == "lt" and curr_val >= float(val):
                    rule_matches = False
                    break

        return rule_matches

    @staticmethod
    def _regel_anwenden(
        rule: Dict[str, Any],
        action: str,
        status: str,
        action_mods: Dict[str, Any],
    ) -> Optional[str]:
        """Schritt Aktion einer passenden Regel: Ablehngrund bei ``block_trade``, sonst ``None``.

        ``probation`` bleibt ohne Wirkung; die vier Modifikatoren ändern ``action_mods``.
        Die Span-Attribute der Ablehnung setzt der Dirigent."""
        # --- Execute Action ---
        if status == "probation":
            return None  # Skip probation rules

        if action == "block_trade":
            return f"Blocked by AI Rule: {rule.get('reason')}"

        elif action == "reduce_size":
            scaler = float(rule.get("value", 0.5))
            if scaler < action_mods["size_scaler"]:
                action_mods["size_scaler"] = scaler

        elif action == "tighten_sl":
            multiplier = float(rule.get("value", 1.5))
            if multiplier < action_mods["sl_multiplier"]:
                action_mods["sl_multiplier"] = multiplier

        elif action == "increase_size":
            scaler = float(rule.get("value", 1.5))
            if scaler > action_mods["size_scaler"]:
                action_mods["size_scaler"] = scaler

        elif action == "widen_sl":
            multiplier = float(rule.get("value", 3.0))
            if multiplier > action_mods["sl_multiplier"]:
                action_mods["sl_multiplier"] = multiplier

        return None

    def evaluate_new_trade(
        self,
        symbol: str,
        side: str,
        market_data: Dict[str, Any],
        current_sl_multiplier: float,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Evaluates a trade against AI rules.
        Returns: (is_allowed, reason, action_mods)
        """
        abbruch = self._vorpruefung_halt()
        if abbruch is not None:
            return abbruch

        abbruch, loop_vix = self._vorpruefung_vix(symbol, side, market_data)
        if abbruch is not None:
            return abbruch

        active_rules = self.ai_rules_singleton.get_rules()
        action_mods: Dict[str, Any] = {
            "size_scaler": 1.0,
            "sl_multiplier": current_sl_multiplier,
        }

        features = market_data.get("indicators", {}).get("features", {})

        with _rm.tracer.start_as_current_span("risk.evaluate_trade") as span:
            span.set_attribute("symbol", symbol)
            span.set_attribute("trade.side", side)
            span.set_attribute("risk.approved", True)

            for rule in active_rules:
                try:
                    trigger = rule.get("trigger", {})
                    action = rule.get("action", "")
                    status = rule.get("status", "active")

                    # Skip proactive signals (handled in engine.py)
                    if action == "proactive_signal":
                        continue

                    if not self._regel_passt(
                        trigger, side, market_data, loop_vix, features
                    ):
                        continue

                    grund = self._regel_anwenden(rule, action, status, action_mods)
                    if grund is not None:
                        span.set_attribute("risk.approved", False)
                        span.set_attribute("risk.reason", grund)
                        return (False, grund, {})

                except Exception as e:
                    # Fail-open per rule: a single malformed AI rule must not
                    # abort evaluation of the rest — but it must NOT be silent  # noqa: E501
                    # (#1236, CLAUDE.md §5.6).
                    logging.warning(
                        "AI rule evaluation failed (rule=%s): %s — skipping this rule",  # noqa: E501
                        rule.get("id", rule.get("reason", "<unknown>")),
                        e,
                        exc_info=True,
                    )

        return True, "Approved", action_mods


from core import risk_manager as _rm  # noqa: E402
