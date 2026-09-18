# tests/unit/test_risk_manager_policy_cap.py
"""#3217 (A1) — der Sizing-Deckel muss der angewandten Iron-Dome-Policy folgen.

DEF-1: ``ComplianceGuardian.reload_policy`` setzt ``max_order_value``,
``RiskManager.reload_policy`` bisher NICHT — der Sizer las bei jedem Aufruf
das Config-Modul. ``apply_policy`` erreicht bei jedem ``/start`` beide Ziele
(api_routes ADR-SEC-06 §1), beim RiskManager verpuffte der Anteil.

Folge, beide Richtungen defekt:
- Policy anheben  → Guardian laesst mehr durch, Sizer schneidet weiter zu.
- Policy senken   → Sizer baut zu gross, Guardian blockt hart (Ausfall 14.07.).

Hier gepinnt: der Override wirkt, klemmt auf das ratifizierte Ceiling, laesst
das Verhalten OHNE Policy byte-identisch und macht eine Abweichung vom
Config-Wert sichtbar (CLAUDE.md §5.6).
"""

import logging
from unittest.mock import MagicMock, patch

import allure
import pytest

from core.compliance import ComplianceGuardian
from core.governance.iron_dome_policy import MAX_ORDER_VALUE_CEILING, apply_policy
from core.risk_manager import RiskManager

PRICE = 100.0


@pytest.fixture()
def rm():
    """Grosses Buch, viel Cash — damit ausschliesslich der Order-Deckel bindet."""
    client = MagicMock()
    client.get_all_positions.return_value = []
    with patch("core.risk_manager.CLOUD_LOGGING_AVAILABLE", False):
        return RiskManager(client=client, total_capital=1_000_000.0)


def _notional(rm_obj, price=PRICE):
    """Ordergegenwert bei voller Conviction; nur der Compliance-Cap soll binden."""
    with patch("config.COMPLIANCE_MAX_ORDER_VALUE", 10_000.0):
        shares = rm_obj.calculate_position_size(
            stop_loss_atr_multiplier=3.0,
            atr=0.01,
            market_data={"vix": 15.0},
            current_price=price,
            account_cash=5_000_000.0,
            conviction_score=1.0,
        )
    return shares * price


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Risk & Compliance")
class TestSizerFollowsPolicyCap:
    def test_r1_policy_tightens_the_cap(self, rm):
        """R1: Policy 4.000 schneidet zu, obwohl die Config 10.000 sagt."""
        rm.reload_policy({"max_order_value": 4_000.0})
        assert _notional(rm) == pytest.approx(4_000.0 * 0.9999, rel=1e-6)

    def test_r2_policy_widens_the_cap(self, rm):
        """R2: Policy 40.000 erlaubt Orders oberhalb des Config-Werts."""
        rm.reload_policy({"max_order_value": 40_000.0})
        assert _notional(rm) == pytest.approx(40_000.0 * 0.9999, rel=1e-6)

    def test_r3_without_policy_behaviour_is_unchanged(self, rm):
        """R3: ohne reload_policy bindet weiterhin der Config-Wert.

        Regression fuer die bestehenden Tests, die ``config.COMPLIANCE_MAX_ORDER_VALUE``
        direkt patchen (test_risk_manager.py:142/422/492, clamp_floor:42).
        """
        assert _notional(rm) == pytest.approx(10_000.0 * 0.9999, rel=1e-6)

    def test_r4_policy_value_is_clamped_to_the_ratified_ceiling(self, rm):
        """R4: 500.000 wird auf MAX_ORDER_VALUE_CEILING (100.000) geklemmt."""
        rm.reload_policy({"max_order_value": 500_000.0})
        assert _notional(rm) == pytest.approx(
            MAX_ORDER_VALUE_CEILING * 0.9999, rel=1e-6
        )

    def test_r5_guardian_and_sizer_cannot_diverge(self, rm):
        """R5: die Kern-Invariante — ein apply_policy, ein wirksamer Wert."""
        guardian = ComplianceGuardian()
        apply_policy({"max_order_value": 7_500.0}, [guardian, rm])

        assert guardian.max_order_value == pytest.approx(7_500.0)
        assert _notional(rm) == pytest.approx(7_500.0 * 0.9999, rel=1e-6)

    def test_r6_divergence_from_config_is_logged(self, rm, caplog):
        """R6: weicht die Policy vom Config-Wert ab, wird das sichtbar (§5.6)."""
        with caplog.at_level(logging.WARNING):
            with patch("config.COMPLIANCE_MAX_ORDER_VALUE", 10_000.0):
                rm.reload_policy({"max_order_value": 4_000.0})

        joined = " ".join(r.getMessage() for r in caplog.records)
        assert "max_order_value" in joined.lower()
        assert "4000" in joined.replace(".0", "").replace(",", "")

    def test_r6b_matching_value_stays_quiet(self, rm, caplog):
        """R6b: stimmt die Policy mit der Config ueberein, kein Rauschen."""
        with caplog.at_level(logging.WARNING):
            with patch("config.COMPLIANCE_MAX_ORDER_VALUE", 10_000.0):
                rm.reload_policy({"max_order_value": 10_000.0})

        joined = " ".join(r.getMessage() for r in caplog.records)
        assert "max_order_value" not in joined.lower()
