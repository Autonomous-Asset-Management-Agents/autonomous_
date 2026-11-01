"""#4993 (Epic #4983) — Conviction-Spanne ab Kaufschwelle, Faktoren wie im Verfahren "b".

Plan: docs/4993-conviction-spanne-ab-kaufschwelle-faktoren-wie-i/implementation_plan.md

Vorher begann die Spanne bei Conviction 0: Ein Kauf an der Schwelle 0,60 bekam 20 % statt des
Minimums, und jeder Kauf wurde mit VIX-Leiter, festem Faktor 1,5 und Warnphase multipliziert.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from core.risk_manager import RiskManager, conviction_gewicht

pytestmark = pytest.mark.vc4

KAPITAL = 20_000.0
PREIS = 100.0


@pytest.fixture(autouse=True)
def _verfahren_off(monkeypatch):
    """Die Lage aus der Abnahme: Schwelle 0,60, Minimum 5 %, Sizing cap 30 %, Maximum 25 %."""
    import config

    for name, wert in {
        "CLEAN_WEIGHT_SIZING": "off",
        "SIGNAL_BUY_THRESHOLD": 0.60,
        "MIN_POSITION_PERCENT": 0.05,
        "MAX_POSITION_PERCENT_SIZING": 0.30,
        "MAX_POSITION_PERCENT": 0.25,
        "VOL_TARGETING_SIZING_ENABLED": True,
    }.items():
        monkeypatch.setattr(config, name, wert, raising=False)


def _rm() -> RiskManager:
    client = MagicMock()
    client.get_all_positions.return_value = []
    uhr = MagicMock()
    uhr.now.return_value = datetime(2026, 10, 30, 14, 0, tzinfo=timezone.utc)
    return RiskManager(client=client, total_capital=KAPITAL, clock=uhr)


def _bemiss(rm=None, conviction=0.8, **kw):
    spur: dict = {}
    argumente = {
        "stop_loss_atr_multiplier": 3.0,
        "atr": 0.2,
        "confidence": "high",
        "size_scaler": 1.0,
        "market_data": {"vix": 15.0},
        "num_stocks_in_strategy": 1,
        "current_price": PREIS,
        "account_cash": 1_000_000.0,
        "allow_fractional": True,
        "conviction_score": conviction,
        "sizing_trace": spur,
    }
    argumente.update(kw)
    with patch("core.kill_switch.kill_switch") as ks:
        ks.is_halted.return_value = False
        menge = (rm or _rm()).calculate_position_size(**argumente)
    return menge, spur


class TestSpanne:
    @pytest.mark.parametrize(
        "conviction, ziel",
        [
            (0.60, 0.05),  # die Schwelle selbst: Minimum, nicht 20 %
            (0.80, 0.175),  # Mitte der Spanne
            (1.00, 0.25),  # Sizing cap 30 %, geklemmt auf das Maximum
            (0.40, 0.05),  # unter der Schwelle, etwa nach Daempfung: Minimum
            (0.00, 0.05),
        ],
    )
    def test_ziel_vor_den_tilts(self, conviction, ziel):
        _, spur = _bemiss(conviction=conviction)
        assert spur["sizing_mode"] == "conviction"
        assert spur["sizing_target_weight"] == pytest.approx(ziel)

    def test_kappe_unter_dem_minimum_gewinnt(self):
        assert conviction_gewicht(0.9, 0.05, 0.30, 0.03) == pytest.approx(0.03)

    @pytest.mark.parametrize("schwelle", [1.0, 0.0, float("nan"), "kaputt"])
    def test_unbrauchbare_schwelle_faellt_auf_den_default_und_warnt(
        self, monkeypatch, caplog, schwelle
    ):
        import config

        monkeypatch.setattr(config, "SIGNAL_BUY_THRESHOLD", schwelle, raising=False)
        with caplog.at_level("WARNING"):
            gewicht = conviction_gewicht(0.73, 0.05, 0.30, 0.25)
        assert gewicht == pytest.approx(0.05)
        assert any("SIGNAL_BUY_THRESHOLD" in r.getMessage() for r in caplog.records)


class TestFaktorenWieVerfahrenB:
    def test_kein_fester_faktor_auf_dem_ziel(self):
        """Ziel 17,5 % von 20.000 $ = 3.500 $, frueher mit Faktor 1,5 am Deckel."""
        menge, _ = _bemiss()
        assert menge * PREIS == pytest.approx(3_500.0)

    def test_vix_leiter_wirkt_nicht(self):
        ruhig, _ = _bemiss(market_data={"vix": 15.0})
        krise, _ = _bemiss(market_data={"vix": 45.0})
        assert krise == pytest.approx(ruhig)

    def test_confidence_und_size_scaler_wirken_nicht(self):
        basis, _ = _bemiss()
        menge, _ = _bemiss(confidence="low", size_scaler=0.5)
        assert menge == pytest.approx(basis)

    def test_warnphase_halbiert_nicht(self):
        basis, _ = _bemiss()
        rm = _rm()
        rm.trading_reduced = True
        menge, _ = _bemiss(rm=rm)
        assert menge == pytest.approx(basis)

    def test_vol_prognose_wirkt_wie_in_b(self, monkeypatch):
        import config

        monkeypatch.setattr(config, "VOL_TARGET_DAILY_VOL", 0.015, raising=False)
        monkeypatch.setattr(config, "VOL_SIZE_SCALER_LO", 0.5, raising=False)
        basis, _ = _bemiss()
        menge, spur = _bemiss(forecast_vol=0.03)
        assert menge == pytest.approx(basis * 0.5)
        assert spur["sizing_target_weight"] == pytest.approx(0.175)

    def test_coverage_abschlag_wirkt_wie_in_b(self, monkeypatch):
        import config

        monkeypatch.setattr(config, "COVERAGE_SIZING_STRENGTH", 1.0, raising=False)
        basis, _ = _bemiss()
        menge, _ = _bemiss(coverage=0.25)
        assert menge == pytest.approx(basis * 0.5)

    def test_skew_tilt_wirkt_wie_in_b(self, monkeypatch):
        import config

        monkeypatch.setattr(config, "SKEW_SIZE_TILT_ENABLED", True, raising=False)
        monkeypatch.setattr(config, "SKEW_SIZE_TILT_CAP", 0.2, raising=False)
        basis, _ = _bemiss()
        menge, _ = _bemiss(rr_percentile=1.0)
        assert menge == pytest.approx(basis * 1.2)

    def test_tilt_nach_oben_bleibt_unter_dem_positionsdeckel(self, monkeypatch):
        import config

        monkeypatch.setattr(config, "VOL_TARGET_DAILY_VOL", 0.015, raising=False)
        monkeypatch.setattr(config, "VOL_SIZE_SCALER_HI", 1.5, raising=False)
        menge, spur = _bemiss(conviction=1.0, forecast_vol=0.01)
        assert menge * PREIS == pytest.approx(KAPITAL * 0.25)
        assert spur["binding_limit"] == "position_cap"

    def test_ohne_dynamische_bemessung_bleibt_der_atr_rueckfall(self, monkeypatch):
        """Plan §7: Das Literal "high" bedient weiter den ATR-Rueckfall."""
        import config

        monkeypatch.setattr(config, "ENABLE_DYNAMIC_SIZING", False, raising=False)
        lage = {"atr": 5.0, "num_stocks_in_strategy": 4}  # unter allen Deckeln
        hoch, _ = _bemiss(confidence="high", **lage)
        tief, _ = _bemiss(confidence="low", **lage)
        assert hoch == pytest.approx(tief * 3.0)


class TestVerfahrenB:
    def test_b_liest_die_spanne_nicht(self, monkeypatch):
        """ "Default unberuehrt": Das Gewicht von "b" haengt nicht an der Conviction."""
        import config

        monkeypatch.setattr(config, "CLEAN_WEIGHT_SIZING", "b", raising=False)
        with patch("core.risk_bemessung._rm.conviction_gewicht") as spanne:
            niedrig, _ = _bemiss(conviction=0.60)
            hoch, _ = _bemiss(conviction=1.0)
        spanne.assert_not_called()
        assert hoch == pytest.approx(niedrig)
