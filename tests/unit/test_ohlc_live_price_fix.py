# tests/unit/test_ohlc_live_price_fix.py
# TDD — P0 Fix: Stale OHLC via daily_bar
#
# Root Cause: trading_loop.py nutzt daily_bar.close als 'close'-Preis.
# daily_bar = gestrige EOD-Bar → immer gleiche stale Werte.
# Fix: latest_trade.price als live 'close' verwenden.
#
# Policy: CODING_POLICY.md §11.5 TDD
from __future__ import annotations

from unittest.mock import MagicMock

import allure
import pytest


def _make_snapshot(
    trade_price: float,
    bar_open: float = 150.0,
    bar_high: float = 155.0,
    bar_low: float = 148.0,
    bar_close: float = 152.0,
    bar_volume: float = 1_000_000.0,
    has_bar: bool = True,
    has_trade: bool = True,
):
    """Alpaca Snapshot mit konfigurierbaren latest_trade und daily_bar."""
    snap = MagicMock()
    if has_trade:
        snap.latest_trade = MagicMock()
        snap.latest_trade.price = trade_price
        snap.latest_trade.p = trade_price
        snap.latest_trade.size = 500.0
    else:
        snap.latest_trade = None

    if has_bar:
        snap.daily_bar = MagicMock()
        snap.daily_bar.open = bar_open
        snap.daily_bar.o = bar_open
        snap.daily_bar.high = bar_high
        snap.daily_bar.h = bar_high
        snap.daily_bar.low = bar_low
        snap.daily_bar.l = bar_low  # noqa: E741 — Alpaca API shorthand for 'low'
        snap.daily_bar.close = (
            bar_close  # ← GESTRIG, darf NICHT als close verwendet werden!
        )
        snap.daily_bar.c = bar_close
        snap.daily_bar.volume = bar_volume
        snap.daily_bar.v = bar_volume
    else:
        snap.daily_bar = None

    return snap


def _extract_ohlc(snapshot_obj):
    """
    Hilfsfunktion: extrahiert OHLC wie trading_loop.py es tun SOLL (nach Fix).

    RED: Aktuell nutzt trading_loop daily_bar.close → stale Preis.
    GREEN: Nach Fix nutzt es latest_trade.price als 'close'.
    """
    from core.engine.trading_loop import _extract_ohlc_from_snapshot

    return _extract_ohlc_from_snapshot(snapshot_obj)


@allure.feature("VC-3 Trading & Execution")
@allure.story("Execution Engine")
class TestOHLCLivePriceFix:
    """
    Gherkin:
      Given: Alpaca Snapshot mit latest_trade.price=185.50 und daily_bar.close=152.00
      When:  OHLC wird aus Snapshot extrahiert
      Then:  ohlc['close'] == 185.50 (live Preis, NICHT stale 152.00)
             ohlc['high'] == max(daily_bar.high, 185.50) (H/L aus gestern als Referenz)
    """

    def test_close_uses_latest_trade_price(self):
        """
        RED: Aktuell ohlc['close'] = daily_bar.close = 152.00 (stale!)
        GREEN: ohlc['close'] = latest_trade.price = 185.50 (live!)
        """
        snapshot = _make_snapshot(trade_price=185.50, bar_close=152.00)
        ohlc, price = _extract_ohlc(snapshot)

        assert ohlc["close"] == pytest.approx(185.50), (
            f"KRITISCH: ohlc['close'] muss live latest_trade.price=185.50 sein, "
            f"nicht stale daily_bar.close=152.00. Got: {ohlc['close']}"
        )
        assert price == pytest.approx(185.50)

    def test_close_reflects_live_price_regardless_of_daily_bar(self):
        """Selbst wenn daily_bar existiert: close = live Preis."""
        snapshot = _make_snapshot(
            trade_price=200.00, bar_close=150.00, bar_high=155.00, bar_low=148.00
        )
        ohlc, price = _extract_ohlc(snapshot)

        assert ohlc["close"] == pytest.approx(
            200.00
        ), f"live price=200.00 muss als close genutzt werden. Got: {ohlc['close']}"

    def test_high_includes_live_price(self):
        """Wenn live Preis > daily_bar.high: high muss live Preis sein."""
        snapshot = _make_snapshot(trade_price=160.00, bar_high=155.00)
        ohlc, _ = _extract_ohlc(snapshot)

        assert (
            ohlc["high"] >= 160.00
        ), f"high muss mindestens live Preis (160.00) sein. Got: {ohlc['high']}"

    def test_low_includes_live_price_if_below_bar_low(self):
        """Wenn live Preis < daily_bar.low: low muss live Preis sein."""
        snapshot = _make_snapshot(trade_price=145.00, bar_low=148.00)
        ohlc, _ = _extract_ohlc(snapshot)

        assert (
            ohlc["low"] <= 145.00
        ), f"low muss mindestens so niedrig wie live Preis (145.00) sein. Got: {ohlc['low']}"

    def test_fallback_to_daily_bar_when_no_trade(self):
        """Kein latest_trade → daily_bar.close als Fallback (Markt geschlossen)."""
        snapshot = _make_snapshot(trade_price=0, has_trade=False, bar_close=152.00)
        ohlc, price = _extract_ohlc(snapshot)

        assert ohlc["close"] == pytest.approx(
            152.00
        ), f"Kein Trade → daily_bar.close=152.00 als Fallback. Got: {ohlc['close']}"

    def test_no_bar_no_trade_returns_zero(self):
        """Weder Bar noch Trade → OHLC alle 0.0."""
        snapshot = _make_snapshot(trade_price=0, has_trade=False, has_bar=False)
        ohlc, price = _extract_ohlc(snapshot)

        assert ohlc["close"] == 0.0
        assert price == 0.0

    def test_different_symbols_get_different_prices(self):
        """Verifiziert dass unterschiedliche Snapshots unterschiedliche Preise liefern."""
        snap1 = _make_snapshot(trade_price=185.50, bar_close=152.00)
        snap2 = _make_snapshot(trade_price=422.30, bar_close=152.00)

        ohlc1, _ = _extract_ohlc(snap1)
        ohlc2, _ = _extract_ohlc(snap2)

        assert ohlc1["close"] != ohlc2["close"], (
            "Unterschiedliche Symbole MÜSSEN unterschiedliche Preise liefern. "
            "Wenn beide 152.00, ist daily_bar.close der Bug."
        )
