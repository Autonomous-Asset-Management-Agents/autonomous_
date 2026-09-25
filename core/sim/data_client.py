"""Offline, point-in-time data client for the Alpaca Sim-Day (#2548 C2/C3).

Root cause of "the sim didn't work": the old SimDataClient built a real
``StockHistoricalDataClient`` with ``"dummy"`` keys → auth failed → no bars → no
fills. This version reads bars **only** from a local ``ReplayBarsProvider`` corpus
(``AAA_SIM_CORPUS_DIR``, a dir of ``{SYMBOL}.parquet|.csv``) — never a live client —
so a sim day is **offline + reproducible**, and it suppresses look-ahead:

  * DAILY bars are served **strictly before the sim-day midnight** (the completed
    current-day daily bar carries the EOD close + full-day high/low and would leak
    into ``create_live_features`` mid-session — spec §5 leak #1).
  * intraday bars are served only up to virtual-now.

Duck-types ``StockHistoricalDataClient.get_stock_bars`` — the returned object exposes
both ``.df`` (alpaca ``BarSet.df`` shape, read by ``data_provider``) and ``.data``
(dict of Bar-likes, read by the fill engine).
"""

import logging

import pandas as pd

try:
    from config import get_config
    from core.analysis.attribution.replay import ReplayBarsProvider
    from core.sim.clock import get_sim_clock
except ImportError:  # pragma: no cover - packaged import path
    from ai_trading_bot.config import get_config
    from ai_trading_bot.core.analysis.attribution.replay import ReplayBarsProvider
    from ai_trading_bot.core.sim.clock import get_sim_clock

logger = logging.getLogger(__name__)

_OHLCV = ["open", "high", "low", "close", "volume"]


class _SimBar:
    """A single Bar-like row (attribute access) for the fill engine / snapshot paths."""

    __slots__ = ("timestamp", "open", "high", "low", "close", "volume")

    def __init__(self, ts, row):
        self.timestamp = pd.Timestamp(ts).to_pydatetime()
        self.open = float(row["open"])
        self.high = float(row["high"])
        self.low = float(row["low"])
        self.close = float(row["close"])
        self.volume = float(row["volume"])


class _SimBarSet:
    """Duck-types alpaca ``BarSet``: ``.df`` (DataFrame) + ``.data`` (dict[sym → list[Bar]])."""

    def __init__(self, frames: dict, single: bool):
        self._frames = (
            frames  # dict symbol -> DataFrame(index=DatetimeIndex, cols=_OHLCV)
        )
        self._single = single

    @property
    def data(self) -> dict:
        return {
            sym: [_SimBar(ts, row) for ts, row in df.iterrows()]
            for sym, df in self._frames.items()
        }

    @property
    def df(self) -> pd.DataFrame:
        if self._single:
            for df in self._frames.values():
                out = df.copy()
                out.index.name = "timestamp"
                return out
            return pd.DataFrame(columns=_OHLCV)
        parts = []
        for sym, df in self._frames.items():
            if df.empty:
                continue
            d = df.copy()
            d.index.name = "timestamp"
            d = d.assign(symbol=sym).set_index("symbol", append=True)
            d = d.reorder_levels(["symbol", "timestamp"])
            parts.append(d)
        if not parts:
            return pd.DataFrame(columns=_OHLCV)
        return pd.concat(parts).sort_index()


class _SimTrade:
    """Bar-like latest trade (``.price``/``.timestamp``/``.size``) for the snapshot path."""

    __slots__ = ("price", "timestamp", "size")

    def __init__(self, price, ts, size=0.0):
        self.price = float(price)
        self.timestamp = pd.Timestamp(ts).to_pydatetime()
        self.size = float(size)


class _SimSnapshot:
    """Duck-types alpaca ``Snapshot`` for ``_extract_ohlc_from_snapshot`` (trading_loop.py:96):
    ``.latest_trade`` (live price) + ``.daily_bar`` (the completed PRIOR-day O/H/L reference).
    """

    __slots__ = (
        "latest_trade",
        "daily_bar",
        "minute_bar",
        "latest_quote",
        "previous_daily_bar",
    )

    def __init__(self, price, ts, daily_bar):
        self.latest_trade = _SimTrade(price, ts)
        self.daily_bar = daily_bar
        self.minute_bar = None
        self.latest_quote = None
        self.previous_daily_bar = daily_bar


class SimDataClient:
    """Offline point-in-time duck-type of ``StockHistoricalDataClient`` (#2548 C2/C3)."""

    def __init__(self, corpus_dir: str = None, clock=None):
        self.clock = clock or get_sim_clock()
        cdir = corpus_dir or getattr(get_config(), "SIM_CORPUS_DIR", "sim_corpus")
        # ReplayBarsProvider = the repo's existing PIT bar server over {SYMBOL}.parquet|.csv.
        self._provider = ReplayBarsProvider(cdir)
        logger.info(
            "SimDataClient (offline) initialized from corpus '%s' (%d symbols).",
            cdir,
            len(self._provider.symbols),
        )

    @staticmethod
    def _is_daily(request) -> bool:
        return "day" in str(getattr(request, "timeframe", "")).lower()

    def _now(self) -> pd.Timestamp:
        now = pd.Timestamp(self.clock.current_time)
        return now.tz_localize(None) if now.tzinfo is not None else now

    def get_stock_bars(self, request) -> _SimBarSet:
        symbols = request.symbol_or_symbols
        single = isinstance(symbols, str)
        syms = [symbols] if single else list(symbols)
        now = self._now()

        if self._is_daily(request):
            # C3 leak #1: never emit the sim-day's own (or a future) completed daily bar.
            boundary = now.normalize()  # midnight of the sim date
            frames = {}
            for s in syms:
                df = self._provider.get_data(s, boundary, days=100_000)
                frames[s] = df[df.index < boundary]
        else:
            frames = {s: self._provider.get_data(s, now, days=100_000) for s in syms}
        return _SimBarSet(frames, single)

    def get_stock_snapshot(self, request):
        """Duck-types ``StockHistoricalDataClient.get_stock_snapshot`` → ``dict[SYM → _SimSnapshot]``.

        Without this the trading loop's ``_fetch_snapshots_chunked`` (trading_loop.py:441) hit
        ``AttributeError`` every cycle → empty panel → zero decisions (the "sim ran but did nothing").

        No-look-ahead (spec §5): ``daily_bar`` = the last COMPLETED prior-day bar (yesterday's EOD,
        the O/H/L reference the extractor expects); ``latest_trade.price`` = the sim-day OPEN when the
        sim-day bar is in the corpus (known at market open, never the completed EOD close), else
        yesterday's close. With a daily corpus the open-hour mark is static within the window — v1 has
        no intraday microstructure (design non-goal), which is fine for decision-logic A/B.
        """
        symbols = getattr(request, "symbol_or_symbols", request)
        syms = [symbols] if isinstance(symbols, str) else list(symbols)
        now = self._now()
        boundary = now.normalize()  # midnight of the sim date
        out = {}
        for s in syms:
            s_u = str(s).upper()
            full = self._provider.get_data(s_u, now, days=100_000)
            if full is None or full.empty:
                continue
            prior = full[full.index < boundary]
            today = full[full.index >= boundary]
            if prior.empty and today.empty:
                continue
            if not prior.empty:
                daily_bar = _SimBar(prior.index[-1], prior.iloc[-1])
                price = (
                    float(today.iloc[-1]["open"])
                    if not today.empty
                    else float(prior.iloc[-1]["close"])
                )
            else:
                o = float(today.iloc[-1]["open"])
                daily_bar = _SimBar(
                    today.index[-1],
                    {"open": o, "high": o, "low": o, "close": o, "volume": 0.0},
                )
                price = o
            out[s_u] = _SimSnapshot(price, now, daily_bar)
        return out
