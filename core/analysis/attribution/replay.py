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

import bisect
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
    symbol: str,
    bar: pd.Series,
    vix: Optional[float] = None,
    implied_vol: Optional[float] = None,
    implied_vol_reference: Optional[List[float]] = None,
    implied_vol_reference_date: Optional[str] = None,
    risk_reversal: Optional[float] = None,
    risk_reversal_reference: Optional[List[float]] = None,
    risk_reversal_reference_date: Optional[str] = None,
) -> Dict[str, Any]:
    """One ``SymbolEvalState`` dict for one historical bar.

    Mirrors core/orchestration/graph.py:66-97: ``ohlc`` holds ONLY the five
    scalars, ``current_time`` is an ISO string, ``vix`` stays ``None`` when no
    VIX series is available so VIXAwareRiskAgent abstains honestly
    (agents.py:486-497) instead of voting on a fabricated quote.

    #3228 Phase 1: ``implied_vol`` (this symbol's ATM-30 IV today) and
    ``implied_vol_reference`` (the PREVIOUS session's IV cross-section across all
    symbols — the distribution VIXAware ranks against, agents.py:1141-1176) are
    injected from the corpus IV series when available; all default ``None`` so a
    run without an IV file stays byte-identical (VIXAware abstains honestly).

    #3248 Phase 2: ``risk_reversal`` (this symbol's 25Δ RR today) and
    ``risk_reversal_reference`` (the PREVIOUS session's RR cross-section — the
    distribution UpsideSkewAgent ranks against, agents.py:2328-2334, AC-7) are
    injected from the corpus RR series when available; all default ``None`` so a run
    without an RR file stays byte-identical (UpsideSkew — dark by default — abstains).
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
        "implied_vol": None if implied_vol is None else float(implied_vol),
        "implied_vol_reference": implied_vol_reference,
        "implied_vol_reference_date": implied_vol_reference_date,
        "risk_reversal": None if risk_reversal is None else float(risk_reversal),
        "risk_reversal_reference": risk_reversal_reference,
        "risk_reversal_reference_date": risk_reversal_reference_date,
    }


# ---------------------------------------------------------------------------
# #3228 Phase 1 — corpus IV series (VIXAware offline attribution)
# ---------------------------------------------------------------------------


def load_iv_table(iv_path: str):
    """Load the corpus ATM-30 IV series (columns ``sym``, ``tag``, ``iv``).

    Returns ``(iv_by_date, sorted_dates)`` where ``iv_by_date[normalized_ts][SYMBOL]
    = iv`` and ``sorted_dates`` is the ascending list of IV-carrying dates. FAIL-SAFE:
    a missing/unreadable/mis-columned file returns ``({}, [])`` so the caller degrades
    to the no-IV path (VIXAware abstains honestly) — never raises into the run.
    """
    try:
        df = pd.read_parquet(iv_path)
    except Exception:
        return {}, []
    cols = {str(c).lower(): c for c in df.columns}
    if not all(c in cols for c in ("sym", "tag", "iv")):
        return {}, []
    # Vectorised (the corpus IV file has ~100k rows; iterrows would dominate runtime).
    t = pd.DataFrame(
        {
            "sym": df[cols["sym"]].astype(str).str.upper(),
            "tag": pd.to_datetime(df[cols["tag"]], errors="coerce").dt.normalize(),
            "iv": pd.to_numeric(df[cols["iv"]], errors="coerce"),
        }
    ).dropna(subset=["tag", "iv"])
    t = t[t["iv"] > 0.0]
    by_date: Dict[pd.Timestamp, Dict[str, float]] = {
        d: dict(zip(g["sym"], g["iv"].astype(float))) for d, g in t.groupby("tag")
    }
    return by_date, sorted(by_date)


def iv_reference_for(sorted_dates, iv_by_date, ts):
    """``(reference_list, reference_date_iso)`` = the IV cross-section of the LAST IV
    session STRICTLY BEFORE ``ts``. VIXAware ranks today's IV against the previous
    session's distribution (agents.py:1141, AC-7 no-look-ahead). ``(None, None)`` if
    no earlier session exists.
    """
    day = pd.Timestamp(ts).normalize()
    i = bisect.bisect_left(sorted_dates, day)
    if i == 0:
        return None, None
    prev = sorted_dates[i - 1]
    ref = [v for v in iv_by_date.get(prev, {}).values() if v and v > 0]
    return (ref or None), prev.date().isoformat()


# ---------------------------------------------------------------------------
# #3248 Phase 2 — corpus RR series (UpsideSkewAgent offline attribution)
# ---------------------------------------------------------------------------


def load_rr_table(rr_path: str):
    """Load the corpus 25Δ risk-reversal series (columns ``sym``, ``tag``, ``rr``).

    Returns ``(rr_by_date, sorted_dates)`` where ``rr_by_date[normalized_ts][SYMBOL]
    = rr`` and ``sorted_dates`` is the ascending list of RR-carrying dates. Mirrors
    :func:`load_iv_table`, with ONE deliberate difference: RR is a SIGNED skew
    measure, so negative values are legitimate (downside skew) and are KEPT — only
    non-finite values are dropped. FAIL-SAFE: a missing/unreadable/mis-columned file
    returns ``({}, [])`` so the caller degrades to the no-RR path (UpsideSkew abstains
    honestly) — never raises into the run.
    """
    try:
        df = pd.read_parquet(rr_path)
    except Exception:
        return {}, []
    cols = {str(c).lower(): c for c in df.columns}
    if not all(c in cols for c in ("sym", "tag", "rr")):
        return {}, []
    # Vectorised (the corpus RR file is large; iterrows would dominate runtime).
    t = pd.DataFrame(
        {
            "sym": df[cols["sym"]].astype(str).str.upper(),
            "tag": pd.to_datetime(df[cols["tag"]], errors="coerce").dt.normalize(),
            "rr": pd.to_numeric(df[cols["rr"]], errors="coerce"),
        }
    ).dropna(subset=["tag", "rr"])
    by_date: Dict[pd.Timestamp, Dict[str, float]] = {
        d: dict(zip(g["sym"], g["rr"].astype(float))) for d, g in t.groupby("tag")
    }
    return by_date, sorted(by_date)


def rr_reference_for(sorted_dates, rr_by_date, ts):
    """``(reference_list, reference_date_iso)`` = the RR cross-section of the LAST RR
    session STRICTLY BEFORE ``ts``. UpsideSkew ranks today's RR against the previous
    session's distribution (agents.py:2328, AC-7 no-look-ahead). ``(None, None)`` if
    no earlier session exists. Signed values are kept (only non-finite dropped).
    """
    day = pd.Timestamp(ts).normalize()
    i = bisect.bisect_left(sorted_dates, day)
    if i == 0:
        return None, None
    prev = sorted_dates[i - 1]
    ref = [
        float(v)
        for v in rr_by_date.get(prev, {}).values()
        if v is not None and pd.notna(v)
    ]
    return (ref or None), prev.date().isoformat()


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
    iv_table: Optional[tuple] = None,
    rr_table: Optional[tuple] = None,
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
    # #3228 Phase 2: with ROUND_TABLE_DISTINCT_ML_SOURCES (config default TRUE) the
    # LSTMSignalAgent resolves its producer via registry.get("LSTMDynamic") — NOT
    # get_active() — and abstains ("LSTMDynamic not registered yet") BEFORE reading the
    # panel. Register the offline LSTM strategy under that name too so the agent's
    # producer resolves; the panel it then reads is populated below.
    if strategy is not None and hasattr(strategy, "cross_section_for"):
        registry.register("LSTMDynamic", strategy)
    set_global_registry(registry)

    # #3228 Phase 2: the REAL LSTMSignalAgent votes on a cross-sectional standing
    # read from the LstmPanelStore AS OF engine_now() (agents.py:_vote_on_rank) — NOT
    # from the strategy's evaluate_for_symbol. To attribute it offline we (1) record
    # each replay date's offline cross-section into that store and (2) drive
    # engine_now() to the replay date via the sim clock (SIM_MODE). ALL global-state
    # changes are restored in `finally`. FAIL-SAFE: any setup error degrades to the
    # pre-#3228 path (LSTM abstains honestly), never breaks the run.
    _lstm_panel = strategy is not None and hasattr(strategy, "cross_section_for")
    _clock = None
    _sim_restore = None
    _store_restore = None
    if _lstm_panel:
        try:
            import config as _cfg
            from core.report.lstm_panel_store import get_store
            from core.sim.clock import get_sim_clock, reset_sim_clock

            _dates = sorted(
                {
                    pd.Timestamp(ix).normalize()
                    for s in provider.symbols
                    for ix in provider.frames[s]
                    .loc[
                        (provider.frames[s].index >= start_ts)
                        & (provider.frames[s].index <= end_ts)
                    ]
                    .index
                }
            )
            _store = get_store()
            # The store keeps only the last SNAPSHOT_HISTORY_LEN (~20) daily snapshots
            # (FIFO eviction, lstm_panel_store.py). Recording the whole window up-front
            # would evict all but the last ~20 dates → the agent finds a panel only
            # there. Bump the retention to cover the window so EVERY recorded date
            # survives for the agent to read; restored in `finally`.
            _need = len(_dates) + 5
            _orig_snap_len, _orig_hist_len = _store._snapshot_len, _store._history_len
            _store._snapshot_len = max(_orig_snap_len, _need)
            _store._history_len = max(_orig_hist_len, _need)
            _store_restore = (_store, _orig_snap_len, _orig_hist_len)
            for _d in _dates:
                _xsec = strategy.cross_section_for(_d)
                _store.record_cycle(
                    _d, sorted(_xsec.items(), key=lambda kv: kv[1], reverse=True)
                )
            _cfg_obj = _cfg.get_config()
            _orig_sim_mode = getattr(_cfg_obj, "SIM_MODE", False)
            _cfg_obj.SIM_MODE = True
            _clock = get_sim_clock()
            _sim_restore = (_cfg_obj, _orig_sim_mode, reset_sim_clock)
        except Exception as exc:  # noqa: BLE001 — degrade to no-panel path
            # Ohne Trace ist ein Rueckfall auf die 100-%-Abstention (genau der Bug,
            # den dieser PR behebt) nicht zu diagnostizieren. §5.6: WARNING behalten,
            # Stack-Trace ergaenzen.
            logger.warning(
                "LSTM panel/sim-clock setup failed — LSTM abstains offline: %s",
                exc,
                exc_info=True,
            )
            _lstm_panel = False
            _clock = None
            _sim_restore = None

    rows: List[tuple] = []
    n_bars = 0
    try:
        for symbol in provider.symbols:
            frame = provider.frames[symbol]
            window = frame.loc[(frame.index >= start_ts) & (frame.index <= end_ts)]
            for ts, bar in window.iterrows():
                n_bars += 1
                if _clock is not None:
                    # drive engine_now() -> this replay date so the LSTM agent reads
                    # the panel snapshot recorded for `ts` (no look-ahead).
                    try:
                        _clock.current_time = pd.Timestamp(ts).to_pydatetime()
                    except Exception as exc:  # noqa: BLE001 — never break the vote loop
                        # §5.6: Fortsetzen ist richtig, Schweigen nicht. Bleibt die Uhr
                        # stehen, liest der LSTM-Agent den Panel-Snapshot eines ANDEREN
                        # Datums weiter — stiller Look-ahead, der die Attribution
                        # unbemerkt entwertet. Genau das muss im Log stehen.
                        logger.warning(
                            "Replay-Uhr konnte nicht auf %s gesetzt werden (%s) — die "
                            "folgenden Votes koennen einen Panel-Snapshot mit falschem "
                            "Datum lesen (Look-ahead-Risiko).",
                            ts,
                            exc,
                            exc_info=True,
                        )
                vix = None
                if vix_series is not None:
                    hit = vix_series.get(pd.Timestamp(ts).normalize())
                    vix = None if hit is None or pd.isna(hit) else float(hit)
                imp_vol = imp_ref = imp_ref_date = None
                if iv_table is not None:
                    _iv_by_date, _iv_dates = iv_table
                    _day = pd.Timestamp(ts).normalize()
                    imp_vol = _iv_by_date.get(_day, {}).get(symbol.upper())
                    imp_ref, imp_ref_date = iv_reference_for(_iv_dates, _iv_by_date, ts)
                rr_val = rr_ref = rr_ref_date = None
                if rr_table is not None:
                    _rr_by_date, _rr_dates = rr_table
                    _day = pd.Timestamp(ts).normalize()
                    rr_val = _rr_by_date.get(_day, {}).get(symbol.upper())
                    rr_ref, rr_ref_date = rr_reference_for(_rr_dates, _rr_by_date, ts)
                state = build_symbol_eval_state(
                    symbol,
                    bar,
                    vix=vix,
                    implied_vol=imp_vol,
                    implied_vol_reference=imp_ref,
                    implied_vol_reference_date=imp_ref_date,
                    risk_reversal=rr_val,
                    risk_reversal_reference=rr_ref,
                    risk_reversal_reference_date=rr_ref_date,
                )
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
        # #3228 Phase 2: restore the global SIM_MODE + drop the sim-clock singleton so
        # the replay leaves no state behind (mirrors the registry restore above).
        if _sim_restore is not None:
            _cfg_obj2, _orig, _reset = _sim_restore
            try:
                _cfg_obj2.SIM_MODE = _orig
            except Exception as exc:  # noqa: BLE001 — Aufraeumen darf nie abbrechen
                # Bleibt SIM_MODE gesetzt, wirkt der Simulationszustand in
                # NACHFOLGENDE Laeufe hinein - der Replay laesst dann doch Spuren.
                logger.warning(
                    "SIM_MODE konnte nach dem Replay nicht auf %s zurueckgesetzt "
                    "werden (%s) - der Simulationszustand kann in weitere Laeufe "
                    "hineinwirken.",
                    _orig,
                    exc,
                    exc_info=True,
                )
            try:
                _reset()
            except Exception as exc:  # noqa: BLE001 — Aufraeumen darf nie abbrechen
                # Die Sim-Uhr ist ein Singleton: bleibt sie stehen, liest der
                # naechste Lauf eine fremde Zeit.
                logger.warning(
                    "Sim-Uhr konnte nach dem Replay nicht verworfen werden (%s) - "
                    "ein nachfolgender Lauf kann eine fremde Zeit lesen.",
                    exc,
                    exc_info=True,
                )
        # restore the panel store's retention window (bumped for the replay)
        if _store_restore is not None:
            _st, _snap0, _hist0 = _store_restore
            try:
                _st._snapshot_len, _st._history_len = _snap0, _hist0
            except Exception as exc:  # noqa: BLE001 — Aufraeumen darf nie abbrechen
                # Bleibt das fuer den Replay hochgesetzte Aufbewahrungsfenster
                # stehen, haelt der Panel-Store dauerhaft mehr Daten als vorgesehen.
                logger.warning(
                    "Aufbewahrungsfenster des Panel-Stores konnte nicht auf "
                    "(%s, %s) zurueckgesetzt werden (%s) - der Store behaelt das "
                    "fuer den Replay hochgesetzte Fenster.",
                    _snap0,
                    _hist0,
                    exc,
                    exc_info=True,
                )

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
