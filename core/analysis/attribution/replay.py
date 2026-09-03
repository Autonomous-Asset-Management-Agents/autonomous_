"""
replay.py — RTR-0 (#1947) deterministic offline vote replay.

Replays every offline-replayable Round-Table agent ``vote()`` over historical
daily bars, one ``SymbolEvalState`` per (symbol, bar), exactly as the
production graph builds it (core/orchestration/graph.py:66-97) — OHLC scalars
only, ISO ``current_time``, optional ``vix``.

The agents that resolve data through ``get_global_registry()`` (DrawdownGuard
30d history, Momentum 12-1M history, LSTM/RL strategy inference —
agents.py:157-172/:385-401/:653-679) are served by a replay-scoped
``AgentRegistry`` whose active strategy exposes the SAME ``get_data``
contract as ``HistoricalDataProvider`` (core/data_provider.py:283) backed by
on-disk bar files. The prior global registry is restored afterwards — the
replay never leaks state into a live process.

NOT replayable offline, by construction (plan §2.5 / §12.2):
  * NewsSentimentAgent   — external LLM, non-deterministic, no PIT news
                           history; it is NEVER invoked here (an offline
                           replay must not fire network calls).
  * SpecialistAlphaAgent — DORMANT (weight 0, registry-dependent).
Both are reported as EXCLUDED with a reason — visible, never silently absent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from core.agent_registry import AgentRegistry, get_global_registry, set_global_registry
from core.round_table.agents import ALL_AGENTS
from core.round_table.base_agent import VotingAgent

logger = logging.getLogger(__name__)

# Agents that can NOT be replayed offline — name -> honest reason. These are
# never invoked (no LLM/network calls from an offline benchmark) and always
# surface in the exclusion report (plan Gherkin §7: "explizit ausgewiesen,
# nicht still weggelassen").
EXCLUDED_AGENTS: Dict[str, str] = {
    "NewsSentimentAgent": (
        "external LLM (Gemini/Ollama), non-deterministic, no point-in-time "
        "news history — not offline-attributable; activation needs a "
        "separate evidence source (plan §12.2)"
    ),
    "SpecialistAlphaAgent": (
        "DORMANT (default_weight 0.0, SpecialistRegistry-dependent) — "
        "excluded from consensus in production; nothing to attribute offline"
    ),
}

_OHLCV_COLS = ["open", "high", "low", "close", "volume"]


# ---------------------------------------------------------------------------
# Data provider — same get_data contract as HistoricalDataProvider
# ---------------------------------------------------------------------------


class ReplayBarsProvider:
    """Point-in-time bar server over a directory of ``{SYMBOL}.parquet|.pkl``.

    Implements the ``get_data(symbol, end_date, days)`` contract the agents
    consume through the registry (core/data_provider.py:283), sliced strictly
    at ``end_date`` — the replay can never look ahead.
    """

    def __init__(self, data_dir: str | Path, symbols: Optional[List[str]] = None):
        self.data_dir = Path(data_dir)
        self.frames: Dict[str, pd.DataFrame] = {}
        wanted = {s.upper() for s in symbols} if symbols else None
        for path in sorted(self.data_dir.glob("*")):
            if path.suffix.lower() not in (".parquet", ".csv"):
                continue
            symbol = path.stem.upper()
            if wanted is not None and symbol not in wanted:
                continue
            try:
                if path.suffix.lower() == ".parquet":
                    df = pd.read_parquet(path)
                else:
                    df = pd.read_csv(path, parse_dates=True, index_col=0)
            except Exception as exc:  # noqa: BLE001 — skip unreadable, loudly
                logger.warning("ReplayBarsProvider: cannot read %s: %s", path, exc)
                continue
            df = self._normalise(df)
            if df is not None and not df.empty:
                self.frames[symbol] = df
        self.symbols: List[str] = sorted(self.frames)

    @staticmethod
    def _normalise(df: pd.DataFrame) -> Optional[pd.DataFrame]:
        """Lowercase OHLCV columns, naive sorted DatetimeIndex."""
        df = df.copy()
        df.columns = [str(c).lower() for c in df.columns]
        if not set(_OHLCV_COLS) <= set(df.columns):
            return None
        if not isinstance(df.index, pd.DatetimeIndex):
            try:
                df.index = pd.to_datetime(df.index)
            except (TypeError, ValueError):
                return None
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        return df[_OHLCV_COLS].sort_index()

    def get_data(
        self,
        symbol: str,
        end_date: datetime,
        days: int = 365,
        **_: Any,
    ) -> pd.DataFrame:
        """PIT slice: bars within ``[end_date - days, end_date]`` inclusive."""
        frame = self.frames.get(str(symbol).upper())
        if frame is None:
            return pd.DataFrame()
        end = pd.Timestamp(end_date)
        if end.tzinfo is not None:
            end = end.tz_localize(None)
        start = end - pd.Timedelta(days=days)
        return frame.loc[(frame.index >= start) & (frame.index <= end)].copy()

    def get_bars(self, symbol: str, timeframe: str = "1Day", limit: int = 100):
        """Legacy alias, mirroring HistoricalDataProvider.get_bars."""
        return self.get_data(symbol, datetime.now(), days=limit)


class _ReplayStrategy:
    """Registry strategy stub for the replay.

    Exposes ``data_provider`` (DrawdownGuard/Momentum path) and an
    ``evaluate_for_symbol`` that returns ``None`` — LSTM/RL then abstain with
    weight 0 ("strategy returned None (abstention)", agents.py:711-714),
    which is the HONEST outcome when no model artifact is loadable offline.
    A model-backed strategy can be injected via ``replay_votes(strategy=...)``
    once the LSTM/RL artifacts are present locally.
    """

    def __init__(self, data_provider: ReplayBarsProvider):
        self.data_provider = data_provider

    async def evaluate_for_symbol(self, symbol, ohlc, market_data, current_time):
        return None


# ---------------------------------------------------------------------------
# State construction — the exact contract agents.py reads (R7)
# ---------------------------------------------------------------------------


def build_symbol_eval_state(
    symbol: str, bar: pd.Series, vix: Optional[float] = None
) -> Dict[str, Any]:
    """One ``SymbolEvalState`` dict for one historical bar.

    Mirrors core/orchestration/graph.py:66-97: ``ohlc`` holds ONLY the five
    scalars, ``current_time`` is an ISO string, ``vix`` stays ``None`` when no
    VIX series is available so VIXAwareRiskAgent abstains honestly
    (agents.py:486-497) instead of voting on a fabricated quote.
    """
    ts = pd.Timestamp(bar.name)
    return {
        "symbol": symbol,
        "ohlc": {
            "open": float(bar["open"]),
            "high": float(bar["high"]),
            "low": float(bar["low"]),
            "close": float(bar["close"]),
            "volume": float(bar["volume"]),
        },
        "market_data_keys": [],
        "current_time": ts.isoformat(),
        "signal": None,
        "error": None,
        "round_table_scores": None,
        "consensus_ranking": None,
        "ml": None,
        "_portfolio_context": None,
        "vix": None if vix is None else float(vix),
    }


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


@dataclass
class ReplayResult:
    """Vote panel + explicit exclusions of one replay run."""

    votes: pd.DataFrame
    exclusions: Dict[str, str] = field(default_factory=dict)
    n_symbols: int = 0
    n_bars: int = 0


def _replayable_agents() -> List[VotingAgent]:
    """Fresh instances of every ALL_AGENTS class that is offline-replayable.

    Derived from the canonical roster (agents.py:1418-1432) so a future 12th
    agent automatically enters the benchmark — or must be explicitly excluded
    with a reason.
    """
    return [
        type(agent)()
        for agent in ALL_AGENTS
        if type(agent).__name__ not in EXCLUDED_AGENTS
    ]


async def replay_votes(
    provider: ReplayBarsProvider,
    *,
    start: str,
    end: str,
    vix_series: Optional[pd.Series] = None,
    strategy: Optional[Any] = None,
) -> ReplayResult:
    """Deterministic vote replay over ``[start, end]`` for every provider symbol.

    Installs a replay-scoped ``AgentRegistry`` (restored in ``finally``) whose
    active strategy serves the on-disk bars, then awaits every replayable
    agent's real ``vote()`` per (symbol, bar).

    Returns a ``ReplayResult`` whose ``votes`` frame has one row per
    (symbol, bar, agent): ``ts, symbol, agent, score, weight, vetoed``.
    Abstentions stay in the panel with ``weight == 0.0`` — the metric layer
    decides how to treat them (abstention != neutral vote).
    """
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    agents = _replayable_agents()
    prior_registry = get_global_registry()
    registry = AgentRegistry()
    registry.register(
        "RTR0ReplayStrategy",
        strategy if strategy is not None else _ReplayStrategy(provider),
        set_active=True,
    )
    set_global_registry(registry)

    rows: List[tuple] = []
    n_bars = 0
    try:
        for symbol in provider.symbols:
            frame = provider.frames[symbol]
            window = frame.loc[(frame.index >= start_ts) & (frame.index <= end_ts)]
            for ts, bar in window.iterrows():
                n_bars += 1
                vix = None
                if vix_series is not None:
                    hit = vix_series.get(pd.Timestamp(ts).normalize())
                    vix = None if hit is None or pd.isna(hit) else float(hit)
                state = build_symbol_eval_state(symbol, bar, vix=vix)
                for agent in agents:
                    vote = await agent.vote(state)
                    rows.append(
                        (
                            pd.Timestamp(ts),
                            symbol,
                            vote.agent_name,
                            float(vote.score),
                            float(vote.weight),
                            bool(vote.vetoed),
                        )
                    )
    finally:
        set_global_registry(prior_registry)  # type: ignore[arg-type]

    votes = pd.DataFrame(
        rows, columns=["ts", "symbol", "agent", "score", "weight", "vetoed"]
    ).reset_index(drop=True)
    return ReplayResult(
        votes=votes,
        exclusions=dict(EXCLUDED_AGENTS),
        n_symbols=len(provider.symbols),
        n_bars=n_bars,
    )


# ---------------------------------------------------------------------------
# Target — forward returns
# ---------------------------------------------------------------------------


def forward_returns(provider: ReplayBarsProvider, horizon: int = 5) -> pd.DataFrame:
    """``horizon``-bar forward mark-to-market close returns per (ts, symbol).

    Target definition of the gate (plan §12.6): ``close[t+h] / close[t] - 1``.
    Rows whose forward window runs past the end of the frame are DROPPED —
    never padded — so the target can not peek beyond the data.
    """
    frames = []
    for symbol in provider.symbols:
        close = provider.frames[symbol]["close"]
        fwd = close.shift(-horizon) / close - 1.0
        fwd = fwd.dropna()
        frames.append(
            pd.DataFrame({"ts": fwd.index, "symbol": symbol, "fwd_ret": fwd.to_numpy()})
        )
    if not frames:
        return pd.DataFrame(columns=["ts", "symbol", "fwd_ret"])
    return pd.concat(frames, ignore_index=True)
