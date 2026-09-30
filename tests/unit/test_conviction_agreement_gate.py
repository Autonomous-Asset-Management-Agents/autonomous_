# tests/unit/test_conviction_agreement_gate.py
# ADR-CONV-01 — a maximally BEARISH forecast outscores the mirror-image bullish one on a BUY.
#
# MEASURED on the real _calculate_conviction_score (rl_signal.py:97), real config,
# rsi_14=50, adx_14=45, macd=-1.0 < macd_signal=-0.5, vix=14:
#
#     pred      conviction
#    -5.764        0.950     <- maximally bearish
#    +5.764        0.800     <- maximally bullish
#
# The bearish forecast wins. On a BUY. That is incoherent by inspection and needs no
# validation harness to call wrong.
#
# TWO defects in one term (`rl_signal.py:114`, `min(abs(model_pred) / 1.2, 1.0)`):
#   1. SIGN discarded — abs() makes -1.2 and +1.2 identical.
#   2. MAGNITUDE discarded — it SATURATES at |pred| >= 1.2, so every real prediction
#      (portfolio_manager.py:337 documents the range as "-5 to +5") pins term 1 to a
#      constant 0.400. All five names measured live (CME -5.764, AMAT -3.662, MMM -3.219,
#      CVNA -2.920, BLK -2.817) saturate.
#
# ⚠ WHAT THIS DOES **NOT** CLAIM — and why the naive fix was rejected:
# Conviction's entire downstream effect on order size is ONE BIT, flipping at conv ~= 0.067.
# Above it, something else always binds first: COMPLIANCE_MAX_ORDER_VALUE (config.py:264),
# the stop-loss constraint (risk_manager.py:572-581), or the cash slot
# (risk_manager.py:485-486). Measured order notional across conv [0.0 .. 1.0]:
#     cap=100k cash=100k slots=10 -> 7500, then 9995 flat        DISTINCT = 2
#     cap=100k cash=100k slots=120 -> 833 flat                   DISTINCT = 1
# So merely SIGNING term 1 (0.950 -> 0.550) changes ZERO submitted orders in every measured
# configuration — both values sit above the step. Shipping that as "the conviction fix"
# would be false remediation.
#
# Only a gate that reaches **exactly 0.0** can cross the step. Hence multiplicative
# agreement, not a signed term.
#
# ⚠ AND IT IS NOT THE FIX FOR 120/120 BUY. That is the reward function:
# trading_environment.py:533-536 pays daily_return for holding a position and -0.00001 for
# holding cash, so cash is strictly dominated and "always be invested" is the CORRECT
# policy for that reward. Tracked separately (N5). Flipping the rsi/adx convention was
# measured to move BUY 234 -> 290 — the wrong direction — so the observation builder is
# not the cause either.

from __future__ import annotations

import pandas as pd
import pytest

from core.strategies.rl_signal import RLSignalMixin


class _Probe(RLSignalMixin):
    """Minimal host — _calculate_conviction_score touches no other state."""


@pytest.fixture
def probe():
    return _Probe()


def _features(rsi=50.0, adx=45.0, macd=-1.0, macd_signal=-0.5):
    return pd.Series(
        {"rsi_14": rsi, "adx_14": adx, "macd": macd, "macd_signal": macd_signal}
    )


_MD = {"vix": 14.0}


class TestTodaysBehaviourIsUnchangedWhenTheFlagIsOff:
    """The legacy body must execute verbatim. This is the default trading path
    (ACTIVE_STRATEGY=RLAgent) and it may not move on a merge."""

    def test_the_incoherence_is_still_there_flag_off(self, probe, monkeypatch):
        import config

        monkeypatch.setattr(config, "CONVICTION_SIGNED_ENABLED", False, raising=False)
        bear = probe._calculate_conviction_score(_features(), -5.764, _MD)
        bull = probe._calculate_conviction_score(_features(), +5.764, _MD)
        assert bear == pytest.approx(0.950), "legacy path moved — it must not"
        assert bull == pytest.approx(0.800), "legacy path moved — it must not"


class TestEvidenceAgainstTheActionCannotArgueForIt:
    @pytest.fixture(autouse=True)
    def _on(self, monkeypatch):
        import config

        monkeypatch.setattr(config, "CONVICTION_SIGNED_ENABLED", True, raising=False)

    def test_a_bearish_forecast_gives_a_buy_no_conviction(self, probe):
        """The whole point. -5.764 must not argue for buying."""
        assert probe._calculate_conviction_score(
            _features(), -5.764, _MD, action="BUY"
        ) == pytest.approx(0.0)

    def test_a_bullish_forecast_still_argues_for_a_buy(self, probe):
        """The default fixture carries a BEARISH macd (-1.0 < -0.5), so a BUY correctly
        earns no macd credit: 0.10 (rsi) + 0.20 (adx) + 0.10 (vix) = 0.40. That is the
        point of the gate — contradicting evidence does not pay."""
        assert probe._calculate_conviction_score(
            _features(), +5.764, _MD, action="BUY"
        ) == pytest.approx(0.40)

    def test_agreeing_macd_pays_on_a_buy(self, probe):
        """With the macd agreeing, the same bullish forecast earns its full 0.55."""
        f = _features(macd=1.0, macd_signal=0.5)
        assert probe._calculate_conviction_score(
            f, +5.764, _MD, action="BUY"
        ) == pytest.approx(0.55)

    def test_the_macd_is_keyed_on_the_action_not_the_forecast(self, probe):
        """⚠ The subtler half of the defect. The legacy macd term (rl_signal.py:144-149)
        asks 'does macd agree with the FORECAST' — so a bearish macd + bearish forecast
        earned +0.15 toward a BUY. Here it must agree with what we are DOING."""
        bearish_macd = _features(macd=-1.0, macd_signal=-0.5)
        assert probe._calculate_conviction_score(
            bearish_macd, +5.764, _MD, action="BUY"
        ) == pytest.approx(0.40), "a bearish macd must not pay on a BUY"
        assert probe._calculate_conviction_score(
            bearish_macd, -5.764, _MD, action="SELL"
        ) == pytest.approx(0.55), "a bearish macd SHOULD pay on a SELL"

    def test_the_bearish_buy_no_longer_outscores_the_bullish_one(self, probe):
        """The defect, stated as an invariant."""
        bear = probe._calculate_conviction_score(_features(), -5.764, _MD, action="BUY")
        bull = probe._calculate_conviction_score(_features(), +5.764, _MD, action="BUY")
        assert bull > bear

    def test_a_bearish_forecast_argues_for_a_sell(self, probe):
        """Agreement is about the ACTION, not about optimism."""
        assert (
            probe._calculate_conviction_score(_features(), -5.764, _MD, action="SELL")
            > 0.5
        )

    def test_magnitude_survives_below_saturation(self, probe):
        """Term 1 saturated at |pred| >= 1.2, discarding magnitude. Below the old
        saturation point a stronger forecast must still score higher."""
        weak = probe._calculate_conviction_score(_features(), 0.3, _MD, action="BUY")
        strong = probe._calculate_conviction_score(_features(), 1.0, _MD, action="BUY")
        assert strong > weak


class TestTheGateCanOnlyEverShrinkAPosition:
    """⚠ THE SAFETY AXIOM. Flipping this flag must be incapable of increasing exposure —
    that is what makes it safe to enable without a validation harness. The workflow
    verified it over 200,000 random draws against the real legacy function
    (violations=0, max delta 2.2e-16); this pins the property in CI."""

    @pytest.fixture(autouse=True)
    def _on(self, monkeypatch):
        import config

        monkeypatch.setattr(config, "CONVICTION_SIGNED_ENABLED", True, raising=False)

    @pytest.mark.parametrize("pred", [-5.0, -1.2, -0.3, 0.0, 0.3, 1.2, 5.0])
    @pytest.mark.parametrize("rsi", [25.0, 50.0, 75.0])
    @pytest.mark.parametrize("adx", [15.0, 35.0, 45.0])
    def test_never_increases(self, probe, monkeypatch, pred, rsi, adx):
        import config

        f = _features(rsi=rsi, adx=adx)
        monkeypatch.setattr(config, "CONVICTION_SIGNED_ENABLED", False, raising=False)
        legacy = probe._calculate_conviction_score(f, pred, _MD)
        monkeypatch.setattr(config, "CONVICTION_SIGNED_ENABLED", True, raising=False)
        gated = probe._calculate_conviction_score(f, pred, _MD, action="BUY")
        assert gated <= legacy + 1e-9, (
            f"the gate INCREASED conviction (pred={pred}, rsi={rsi}, adx={adx}): "
            f"{legacy} -> {gated}. It must only ever shrink a position."
        )


class TestTheContractHolds:
    @pytest.fixture(autouse=True)
    def _on(self, monkeypatch):
        import config

        monkeypatch.setattr(config, "CONVICTION_SIGNED_ENABLED", True, raising=False)

    def test_no_features_still_returns_the_neutral_default(self, probe):
        """rl_signal.py:109 returns 0.2 when features are absent. Unchanged."""
        assert probe._calculate_conviction_score(None, 1.0, _MD, action="BUY") == 0.2

    def test_the_action_defaults_to_buy(self, probe):
        """Existing callers pass three args. They must keep working."""
        assert probe._calculate_conviction_score(
            _features(), 1.0, _MD
        ) == pytest.approx(
            probe._calculate_conviction_score(_features(), 1.0, _MD, action="BUY")
        )

    @pytest.mark.parametrize("pred", [-9.9, 9.9, 0.0])
    def test_the_score_stays_in_range(self, probe, pred):
        v = probe._calculate_conviction_score(_features(), pred, _MD, action="BUY")
        assert 0.0 <= v <= 1.0
