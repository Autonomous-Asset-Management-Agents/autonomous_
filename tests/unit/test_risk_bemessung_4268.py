"""#4268 (H-3f) — die Bemessung wohnt in ``core/risk_bemessung.py``.

Plan: ``docs/4268-*/implementation_plan.md`` §5. Schnitt-Entscheidung #4185:
``docs/3738-arc-e6-gestalt/H3_SCHNITT_risk_manager.md`` §2/§3, Abschnitt H-3f.

``calculate_position_size``, ``_sizing_risk_scaler`` und ``_sizing_target`` ziehen als
``BemessungMixin`` um. Der Kern-Import steht am Dateiende; ``effective_max_positions`` bleibt
Patch-Ziel am Modulobjekt ``core.risk_manager``. Die Skalierer liest die Bemessung über ``_rm.``;
seit #4271 (H-3i) sind sie im Kern ein Re-Export aus ``core.risk_skalierer`` und kein Patch-Ziel.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc4]

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
NUTZER = "nutzer-4268"

BEMESSUNG = ("calculate_position_size", "_sizing_risk_scaler", "_sizing_target")


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


def _ziel(rm, modus):
    """``_sizing_target`` im Clean-Weight-Zweig: Stückzahl vor dem ersten Deckel."""
    with patch("config.CLEAN_WEIGHT_SIZING", modus, create=True), patch(
        "config.ENABLE_DYNAMIC_SIZING", True, create=True
    ), patch("config.MAX_POSITION_PERCENT", 0.25, create=True):
        sized = rm._sizing_target(
            100.0, 0.5, 1.0, 1.0, 1.0, 1.0, 1, 2.0, 2.0, None, lambda *_: None
        )
    return sized[0]


def _vol_targeting():
    """Vol-Targeting an, Ziel 1,5 % Tagesvolatilität: Prognose 0,03 → Faktor 0,5, 0,015 → 1,0.
    Skew und Coverage aus, damit nur dieser Faktor wirkt."""
    stapel = ExitStack()
    for name, wert in {
        "VOL_TARGETING_SIZING_ENABLED": True,
        "VOL_TARGET_DAILY_VOL": 0.015,
        "VOL_SIZE_SCALER_LO": 0.5,
        "VOL_SIZE_SCALER_HI": 1.5,
        "VIX_SIZE_INFLUENCE": 1.0,
        "SKEW_SIZE_TILT_ENABLED": False,
        "COVERAGE_SIZING_STRENGTH": 0.0,
    }.items():
        stapel.enter_context(patch(f"config.{name}", wert, create=True))
    return stapel


def test_bemessung_steht_im_mixin():
    from core.risk_bemessung import BemessungMixin
    from core.risk_manager import RiskManager

    for name in BEMESSUNG:
        assert name in BemessungMixin.__dict__, f"{name} fehlt in BemessungMixin"
        assert name not in RiskManager.__dict__, f"{name} steht noch im Kern"
    assert issubclass(RiskManager, BemessungMixin)
    assert "__init__" not in BemessungMixin.__dict__


def test_bemessung_zuerst_importierbar():
    code = textwrap.dedent(
        """
        import core.risk_bemessung
        from unittest.mock import MagicMock
        from core.kill_switch import LokalerHalt
        from core.risk_manager import RiskManager

        RiskManager(
            MagicMock(),
            10_000.0,
            user_id="nutzer-4268",
            kill_switch=LokalerHalt(),
            clock=MagicMock(),
        )
        """
    )
    lauf = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PAKET,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert lauf.returncode == 0, lauf.stderr


def test_patch_am_kern_wirkt_auf_bemessung():
    # Charakterisierung: vor dem Umzug geschrieben und dort grün. Fiele eine ``_rm.``-Lesestelle
    # weg, liefe der Patch still am Mixin vorbei und dieser Test würde rot.
    rm = _rm()

    # effective_max_positions → _sizing_target, Clean-Weight "a": 1/N von 10.000 $ bei 100 $.
    stueck = {}
    for slots in (5, 20):
        with patch("core.risk_manager.effective_max_positions", return_value=slots):
            stueck[slots] = _ziel(rm, "a")
    assert stueck == {5: pytest.approx(20.0), 20: pytest.approx(5.0)}

    # vol_targeting_scaler → _sizing_risk_scaler: der Faktor steht im Ergebnis-Tupel. #4271
    # (H-3i): über die Konfiguration statt über einen Patch — der Name ist im Kern seitdem ein
    # Re-Export aus ``core.risk_skalierer``, ein Patch dort träfe den Audit-Spiegel nicht.
    with _vol_targeting():
        final, vol, _skew, _cov = rm._sizing_risk_scaler(
            {"vix": 15.0}, "medium", 1.0, 0.03, None, None
        )
    assert vol == pytest.approx(0.5)

    # Und durch die öffentliche Methode: der halbierte Faktor ändert die Stückzahl.
    def _groesse(prognose):
        with _vol_targeting(), patch("config.CLEAN_WEIGHT_SIZING", "off", create=True):
            return rm.calculate_position_size(
                2.0,
                1.0,
                market_data={"vix": 15.0},
                current_price=100.0,
                account_cash=10_000.0,
                conviction_score=0.0,
                forecast_vol=prognose,
            )

    voll, halb = _groesse(0.015), _groesse(0.03)
    assert voll > 0
    assert halb == pytest.approx(voll * 0.5)
