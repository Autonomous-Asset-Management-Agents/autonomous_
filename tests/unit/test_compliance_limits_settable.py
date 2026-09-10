# tests/unit/test_compliance_limits_settable.py
"""#3220 + #3221 — Orderdeckel und Tageslimit werden kundenseitig einstellbar.

Owner-Entscheidung 05.09.2026, gestuetzt auf den HITL-Praezedenzfall: fuenf der
sechs HITL-Werte sind laengst kundenseitig verstellbar, nur der An/Aus-Schalter
selbst bleibt boot-erzwungen. Genau dieses Muster gilt hier — die LEITPLANKE
(dass ein Deckel existiert und die ratifizierte Obergrenze haelt) bleibt
unantastbar, das BETRIEBSLIMIT darunter wird einstellbar und auditiert.

Der Blocker dafuer war ein stiller Reset: ``apply_policy`` laeuft bei jedem
``/start-live``; ohne gespeicherte Admin-Policy liefert ``load_policy({})``
den hartkodierten ``STRICT_DEFAULT`` und ueberschreibt damit den per
Registry/``setup.json``/env gesetzten Betriebswert — lautlos.

Die Korrektur trennt drei Ebenen sauber:

* **config** (env/``setup.json``) = Betriebs-Default, kundenseitig setzbar
* **gespeicherte Policy** = Admin-Uebersteuerung zur Laufzeit
* **Ceiling** = ratifizierte, unverruecktbare Obergrenze

Fail-closed bleibt unangetastet: ``None``, Muell und NaN liefern weiterhin
``STRICT_DEFAULT``. Nur der Fall "gueltiger Store, Key nicht gesetzt" faellt
jetzt auf den Betriebswert statt auf die Konstante zurueck.
"""

import logging
from unittest.mock import patch

import allure
import pytest

from core.governance.iron_dome_policy import (
    MAX_DAILY_TRADES_CEILING,
    MAX_ORDER_VALUE_CEILING,
    STRICT_DEFAULT,
    load_policy,
)


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Risk & Compliance")
class TestPolicyFallsBackToOperatingValue:
    def test_r1_empty_store_uses_the_configured_order_value(self):
        """R1: kein Admin-Override ⇒ der konfigurierte Betriebswert gilt."""
        with patch("config.COMPLIANCE_MAX_ORDER_VALUE", 25_000.0):
            assert load_policy({}).max_order_value == pytest.approx(25_000.0)

    def test_r2_empty_store_uses_the_configured_daily_trades(self):
        """R2: dasselbe fuer das Tageslimit (#3221)."""
        with patch("config.COMPLIANCE_MAX_DAILY_TRADES", 24):
            assert load_policy({}).max_daily_trades == 24

    def test_r3_stored_admin_value_still_wins(self):
        """R3: eine gespeicherte Policy uebersteuert den Betriebswert."""
        with patch("config.COMPLIANCE_MAX_ORDER_VALUE", 25_000.0):
            assert load_policy({"max_order_value": 4_000.0}).max_order_value == (
                pytest.approx(4_000.0)
            )

    def test_r4_ceiling_still_clamps_the_configured_value(self):
        """R4: die ratifizierte Obergrenze bleibt unverrueckbar — auch aus config."""
        with patch("config.COMPLIANCE_MAX_ORDER_VALUE", 5_000_000.0):
            assert load_policy({}).max_order_value == pytest.approx(
                MAX_ORDER_VALUE_CEILING
            )
        with patch("config.COMPLIANCE_MAX_DAILY_TRADES", 9_999):
            assert load_policy({}).max_daily_trades == MAX_DAILY_TRADES_CEILING

    def test_r5_fail_closed_paths_are_untouched(self):
        """R5: None und Muell liefern weiterhin STRICT_DEFAULT (Regression)."""
        with patch("config.COMPLIANCE_MAX_ORDER_VALUE", 25_000.0):
            assert load_policy(None) == STRICT_DEFAULT
            assert load_policy("garbage") == STRICT_DEFAULT

    def test_r6_unreadable_config_falls_back_to_strict_default(self):
        """R6: ist der Betriebswert unlesbar, gilt wieder die strenge Konstante."""
        with patch("config.COMPLIANCE_MAX_ORDER_VALUE", "kaputt"):
            assert load_policy({}).max_order_value == pytest.approx(
                STRICT_DEFAULT.max_order_value
            )


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Risk & Compliance")
class TestLimitsAreInTheSettingsRegistry:
    def test_r7_both_limits_are_settable(self):
        """R7: die Registry nimmt beide Keys an (bisher 422)."""
        from core import trading_settings as ts

        assert "COMPLIANCE_MAX_ORDER_VALUE" in ts.REGISTRY
        assert "COMPLIANCE_MAX_DAILY_TRADES" in ts.REGISTRY

        applied = ts.apply_updates(
            {"COMPLIANCE_MAX_ORDER_VALUE": 25_000.0, "COMPLIANCE_MAX_DAILY_TRADES": 24}
        )
        assert applied["COMPLIANCE_MAX_ORDER_VALUE"].startswith("25000")
        assert applied["COMPLIANCE_MAX_DAILY_TRADES"] == "24"

    def test_r8_registry_bounds_are_the_ratified_ceilings(self):
        """R8: die Registry-Grenzen sind exakt die ratifizierten Ceilings."""
        from core import trading_settings as ts

        assert ts.REGISTRY["COMPLIANCE_MAX_ORDER_VALUE"].hi == pytest.approx(
            MAX_ORDER_VALUE_CEILING
        )
        assert ts.REGISTRY["COMPLIANCE_MAX_DAILY_TRADES"].hi == MAX_DAILY_TRADES_CEILING

    def test_r9_a_value_above_the_ceiling_is_clamped_not_accepted(self):
        """R9: ueber dem Ceiling wird geklemmt — die Antwort zeigt den Ist-Wert.

        Kein stilles Durchwinken: der Aufrufer bekommt den TATSAECHLICH
        angewandten Wert zurueck und sieht damit, dass geklemmt wurde.
        """
        from core import trading_settings as ts

        applied = ts.apply_updates({"COMPLIANCE_MAX_ORDER_VALUE": 5_000_000.0})
        assert float(applied["COMPLIANCE_MAX_ORDER_VALUE"]) == pytest.approx(
            MAX_ORDER_VALUE_CEILING
        )


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Risk & Compliance")
class TestGuardianSurfacesDivergence:
    def test_r10_guardian_logs_when_policy_overrides_config(self, caplog):
        """R10: uebersteuert die Policy den Betriebswert, wird das sichtbar (§5.6).

        Der RiskManager tut das seit #3217; der Guardian schwieg bisher.
        """
        from core.compliance import ComplianceGuardian

        guardian = ComplianceGuardian()
        with caplog.at_level(logging.WARNING):
            with patch("config.COMPLIANCE_MAX_ORDER_VALUE", 25_000.0):
                guardian.reload_policy({"max_order_value": 4_000.0})

        joined = " ".join(r.getMessage() for r in caplog.records).lower()
        assert "max_order_value" in joined

    def test_r11_unreadable_config_in_the_diagnostic_is_logged(self, caplog):
        """R11: auch der Ausfall der DIAGNOSE selbst wird laut (§5.6).

        Review-Befund POLICY-01 zu #3232: der ``except``-Zweig schluckte still.
        Damit war eine defekte Config nicht davon zu unterscheiden, dass Policy
        und Config übereinstimmen — beides blieb ruhig, und der Vergleich fiel
        unbemerkt aus.
        """
        from core.compliance import ComplianceGuardian

        guardian = ComplianceGuardian()
        with caplog.at_level(logging.WARNING):
            with patch(
                "core.compliance.__import__",
                side_effect=RuntimeError("config kaputt"),
                create=True,
            ):
                guardian.reload_policy({"max_order_value": 4_000.0})

        joined = " ".join(r.getMessage() for r in caplog.records).lower()
        # BEWUSST auf den Wortlaut AUS compliance.py geprüft: iron_dome_policy
        # loggt eine ähnliche "unlesbar"-Meldung, ein blosses "unlesbar" wäre
        # also auch ohne diesen Fix grün gewesen — ein falsch-positiver Test.
        assert "policy-vergleich" in joined, joined
