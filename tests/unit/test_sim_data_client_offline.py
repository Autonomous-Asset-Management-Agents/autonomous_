"""#2548 C2/C3 — SimDataClient must serve bars OFFLINE (local corpus, never a live
StockHistoricalDataClient) and point-in-time (no look-ahead).

This is the root cause of "the sim didn't work yesterday": the old SimDataClient built a
real client with `"dummy"` keys → auth fails → no bars → no fills. Here we prove the new
client reads only from a local ReplayBarsProvider corpus and never emits the sim-day's own
(or a future) daily bar mid-session.
"""

from datetime import datetime

import pandas as pd
import pytest
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame


def _write_corpus(tmp_path):
    # AAPL daily bars 07-10..07-16; sim day = 07-15 (a mid-week session).
    idx = pd.to_datetime(
        ["2026-07-10", "2026-07-11", "2026-07-14", "2026-07-15", "2026-07-16"]
    )
    df = pd.DataFrame(
        {
            "open": [100, 101, 102, 103, 104],
            "high": [101, 102, 103, 104, 105],
            "low": [99, 100, 101, 102, 103],
            "close": [100.5, 101.5, 102.5, 103.5, 104.5],
            "volume": [1000, 1000, 1000, 1000, 1000],
        },
        index=idx,
    )
    df.to_parquet(tmp_path / "AAPL.parquet")


class _Clock:
    """Minimal SimClock stand-in: virtual-now is mid sim-day 07-15."""

    def __init__(self, t):
        self.current_time = t
        self.end_time = t


def test_sim_data_client_never_builds_a_real_client(tmp_path, monkeypatch):
    _write_corpus(tmp_path)
    # Guard: any attempt to construct a real StockHistoricalDataClient must blow up.
    import alpaca.data.historical as hist

    def _boom(*a, **k):
        raise AssertionError(
            "SimDataClient must not construct a real Alpaca data client"
        )

    monkeypatch.setattr(hist, "StockHistoricalDataClient", _boom)

    from core.sim.data_client import SimDataClient

    dc = SimDataClient(
        corpus_dir=str(tmp_path), clock=_Clock(datetime(2026, 7, 15, 14, 30))
    )
    assert dc is not None  # constructed offline, no real client


def test_daily_bars_are_point_in_time_no_lookahead(tmp_path):
    _write_corpus(tmp_path)
    from core.sim.data_client import SimDataClient

    dc = SimDataClient(
        corpus_dir=str(tmp_path), clock=_Clock(datetime(2026, 7, 15, 14, 30))
    )
    req = StockBarsRequest(
        symbol_or_symbols="AAPL",
        timeframe=TimeFrame.Day,
        start=datetime(2026, 7, 1),
        end=datetime(2026, 7, 15),
    )
    resp = dc.get_stock_bars(req)

    # .df present + lowercase OHLCV + DatetimeIndex
    df = resp.df
    assert not df.empty
    assert set(["open", "high", "low", "close", "volume"]) <= set(df.columns)

    # NO look-ahead: the sim-day (07-15) daily bar AND the future (07-16) are excluded
    # → the last served daily bar is 07-14.
    last_ts = pd.Timestamp(df.index.max())
    assert last_ts.normalize() <= pd.Timestamp(
        "2026-07-14"
    ), f"look-ahead leak: served a bar dated {last_ts} at virtual-now 07-15 14:30"

    # .data (dict) is present for the fill engine and carries Bar-likes with .close
    assert "AAPL" in resp.data
    assert float(resp.data["AAPL"][-1].close) == pytest.approx(102.5)  # 07-14 close


def test_multi_symbol_df_is_multiindex(tmp_path):
    _write_corpus(tmp_path)
    # add a second symbol
    idx = pd.to_datetime(["2026-07-11", "2026-07-14"])
    pd.DataFrame(
        {
            "open": [50, 51],
            "high": [51, 52],
            "low": [49, 50],
            "close": [50.5, 51.5],
            "volume": [500, 500],
        },
        index=idx,
    ).to_parquet(tmp_path / "MSFT.parquet")

    from core.sim.data_client import SimDataClient

    dc = SimDataClient(
        corpus_dir=str(tmp_path), clock=_Clock(datetime(2026, 7, 15, 14, 30))
    )
    req = StockBarsRequest(
        symbol_or_symbols=["AAPL", "MSFT"],
        timeframe=TimeFrame.Day,
        start=datetime(2026, 7, 1),
        end=datetime(2026, 7, 15),
    )
    df = dc.get_stock_bars(req).df
    assert isinstance(df.index, pd.MultiIndex)
    assert set(df.index.get_level_values(0).unique()) == {"AAPL", "MSFT"}
