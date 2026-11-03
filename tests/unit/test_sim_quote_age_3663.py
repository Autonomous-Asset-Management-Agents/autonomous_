"""#3663 — Der Sim-Datenclient stempelt zeitzonenbewusst; der Kursalter-Guard (#3381) misst im
Sim dasselbe Alter wie live (Übergabe Datenclient → Snapshot-Extraktion → is_stale_quote).

Vorher: naiver ET-Stempel, vom Guard als UTC gelesen ⇒ 14 400 s > 900 s ⇒ jedes Symbol enthielt sich.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

pytestmark = [pytest.mark.vc0]

from core.engine.time_budget import is_stale_quote  # noqa: E402
from core.engine.trading_loop import _extract_ohlc_from_snapshot  # noqa: E402

_ET = ZoneInfo("America/New_York")


def _corpus(tmp_path):
    idx = pd.to_datetime(["2026-06-02", "2026-06-03", "2026-06-04", "2026-06-05"])
    df = pd.DataFrame(
        {
            "open": [100, 101, 102, 103],
            "high": [101, 102, 103, 104],
            "low": [99, 100, 101, 102],
            "close": [100.5, 101.5, 102.5, 103.5],
            "volume": [1000] * 4,
        },
        index=idx,
    )
    df.to_parquet(tmp_path / "AAPL.parquet")


class _Clock:
    def __init__(self, t):
        self.current_time = t
        self.end_time = t


def _client(tmp_path, t):
    from core.sim.data_client import SimDataClient

    _corpus(tmp_path)
    return SimDataClient(corpus_dir=str(tmp_path), clock=_Clock(t))


class TestSimHandeltMitAuslieferungsGuard:
    def test_snapshot_stempel_ist_tz_bewusst_und_gleich_der_sim_uhr(self, tmp_path):
        t = datetime(2026, 6, 4, 9, 30, tzinfo=_ET)
        snap = _client(tmp_path, t).get_stock_snapshot("AAPL")["AAPL"]
        ts = snap.latest_trade.timestamp
        assert ts.tzinfo is not None
        assert ts.astimezone(timezone.utc) == t.astimezone(timezone.utc)

    def test_naive_test_uhr_wird_als_et_gelesen(self, tmp_path):
        t = datetime(2026, 6, 4, 9, 30)
        snap = _client(tmp_path, t).get_stock_snapshot("AAPL")["AAPL"]
        ts = snap.latest_trade.timestamp
        assert ts.tzinfo is not None
        assert ts.astimezone(timezone.utc).hour == 13

    def test_kursalter_im_sim_ist_null_mit_auslieferungs_900s(self, tmp_path):
        t = datetime(2026, 6, 4, 9, 30, tzinfo=_ET)
        snap = _client(tmp_path, t).get_stock_snapshot("AAPL")["AAPL"]
        _ohlc, _price, quote_ts = _extract_ohlc_from_snapshot(snap)
        engine_now_utc = t.astimezone(timezone.utc)
        assert is_stale_quote(quote_ts, engine_now_utc, 900.0) is False

    def test_absichtlich_alter_stempel_bleibt_stale(self, tmp_path):
        t = datetime(2026, 6, 4, 9, 30, tzinfo=_ET)
        snap = _client(tmp_path, t).get_stock_snapshot("AAPL")["AAPL"]
        _ohlc, _price, quote_ts = _extract_ohlc_from_snapshot(snap)
        later = t.astimezone(timezone.utc) + timedelta(hours=3)
        assert is_stale_quote(quote_ts, later, 900.0) is True

    def test_tages_bars_enden_weiter_am_vortag(self, tmp_path):
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        t = datetime(2026, 6, 4, 9, 30, tzinfo=_ET)
        dc = _client(tmp_path, t)
        req = StockBarsRequest(
            symbol_or_symbols="AAPL",
            timeframe=TimeFrame.Day,
            start=datetime(2026, 1, 1),
        )
        df = dc.get_stock_bars(req).df
        assert str(df.index.max().date()) == "2026-06-03"
