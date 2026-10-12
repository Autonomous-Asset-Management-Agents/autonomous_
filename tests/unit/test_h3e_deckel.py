"""#4267 (H-3e) — die Deckel wohnen in ``core/risk_deckel.py``.

Plan: ``docs/4267-*/implementation_plan.md`` §5. Schnitt-Entscheidung #4185:
``docs/3738-arc-e6-gestalt/H3_SCHNITT_risk_manager.md`` §2/§3, Abschnitt H-3e.

Die fünf ``_step_*`` samt ``_sizing_cash_demand_fraction`` und ``_sizing_order_floor`` ziehen
als ``DeckelMixin`` um. Der Kern-Import steht am Dateiende, ``effective_max_positions`` bleibt
Patch-Ziel am Modulobjekt ``core.risk_manager``.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc4]

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
MODUL = PAKET / "core" / "risk_deckel.py"
NUTZER = "nutzer-4267"

DECKEL = (
    "_step_position_cap",
    "_step_cash",
    "_sizing_cash_demand_fraction",
    "_step_total_exposure_cap",
    "_step_kelly",
    "_step_max_loss_per_trade",
    "_step_compliance_order_value",
    "_sizing_order_floor",
)


class _Uhr:
    def now(self, tz=None):
        import datetime

        return datetime.datetime(2026, 10, 8, tzinfo=datetime.timezone.utc)


def _rm():
    """Echter ``RiskManager`` mit lokalem Halt — der globale Kill-Switch bleibt unberührt."""
    from core.kill_switch import LokalerHalt
    from core.risk_manager import RiskManager

    return RiskManager(
        MagicMock(), 10_000.0, user_id=NUTZER, kill_switch=LokalerHalt(), clock=_Uhr()
    )


def test_deckel_wohnen_im_mixin():
    from core.risk_deckel import DeckelMixin
    from core.risk_manager import RiskManager

    for name in DECKEL:
        assert name in DeckelMixin.__dict__, f"{name} fehlt in DeckelMixin"
        assert name not in RiskManager.__dict__, f"{name} steht noch im Kern"
    assert issubclass(RiskManager, DeckelMixin)


def test_kern_import_am_ende():
    baum = ast.parse(MODUL.read_text(encoding="utf-8"))
    letzte_klasse = max(
        i for i, k in enumerate(baum.body) if isinstance(k, ast.ClassDef)
    )
    kern = [
        i
        for i, k in enumerate(baum.body)
        if (isinstance(k, ast.ImportFrom) and k.module == "core.risk_manager")
        or (
            isinstance(k, ast.ImportFrom)
            and k.module == "core"
            and any(a.name == "risk_manager" for a in k.names)
        )
        or (
            isinstance(k, ast.Import)
            and any(a.name == "core.risk_manager" for a in k.names)
        )
    ]
    assert len(kern) == 1, f"genau ein Kern-Import erwartet, gefunden: {kern}"
    knoten = baum.body[kern[0]]
    assert isinstance(knoten, ast.ImportFrom) and knoten.module == "core"
    assert [(a.name, a.asname) for a in knoten.names] == [("risk_manager", "_rm")]
    assert kern[0] > letzte_klasse, "Kern-Import steht vor der Klasse (Zirkelimport)"


@pytest.mark.parametrize("arm", ["a", "b"])
def test_patch_auf_kern_trifft_deckel(arm):
    # ``effective_max_positions`` liest nur der Clean-Weight-Zweig (#3284). Ziel 1.000 $ von
    # 10.000 $ = Gewicht 0,1: bei 20 Slots (1/N = 0,05) voller Slot 1,0, bei 5 Slots 0,5.
    rm = _rm()
    anteile = {}
    for slots in (5, 20):
        with patch(
            "core.risk_manager.effective_max_positions", return_value=slots
        ), patch("config.PROPORTIONAL_SLOT_CASH_ENABLED", True, create=True):
            anteile[slots] = rm._sizing_cash_demand_fraction(arm, 1_000.0, 0.30)
    assert anteile == {5: 0.5, 20: 1.0}
