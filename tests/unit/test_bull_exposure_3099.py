"""#3099 (Hebel 2) — gebeckeltes Bull-Exposure ohne Leverage.

Die ADR-R08-VIX-Ladder bleibt down-only; ein flag-gated Risk-on-Schritt
hebt den Scaler NUR im ruhigen Regime (VIX<=18) und ist der EINZIGE
>1.0-Schritt. Die harte Decke (MAX_TOTAL_EXPOSURE_PCT/RISK_FORBID_LEVERAGE)
wirkt downstream und ist hier nicht Gegenstand — geprüft wird die
Ladder-Funktion selbst.
"""

import pytest

from core.risk_manager import vix_ladder_scaler


def test_ladder_down_only_bands_unchanged():
    assert vix_ladder_scaler(45) == 0.3
    assert vix_ladder_scaler(36) == 0.4
    assert vix_ladder_scaler(26) == 0.65
    assert vix_ladder_scaler(20) == 0.9


def test_calm_regime_default_is_one_when_bull_off():
    assert vix_ladder_scaler(15, bull_enabled=False) == 1.0
    assert vix_ladder_scaler(18, bull_enabled=False) == 1.0


def test_bull_on_lifts_only_calm_regime():
    # calm (<=18) -> calm_scaler
    assert vix_ladder_scaler(
        15, bull_enabled=True, calm_scaler=1.15
    ) == pytest.approx(  # noqa: E501
        1.15
    )
    assert vix_ladder_scaler(
        18, bull_enabled=True, calm_scaler=1.15
    ) == pytest.approx(  # noqa: E501
        1.15
    )


def test_bull_on_does_not_touch_down_only_bands():
    # any VIX > 18 stays down-only even with bull enabled
    # (fast de-risk preserved)
    assert vix_ladder_scaler(19, bull_enabled=True, calm_scaler=1.15) == 0.9
    assert vix_ladder_scaler(26, bull_enabled=True, calm_scaler=1.15) == 0.65
    assert vix_ladder_scaler(45, bull_enabled=True, calm_scaler=1.15) == 0.3


def test_scaler_never_exceeds_calm_scaler():
    for vix in (5, 15, 18, 19, 26, 36, 45):
        assert (
            vix_ladder_scaler(vix, bull_enabled=True, calm_scaler=1.2) <= 1.2
        )  # noqa: E501
