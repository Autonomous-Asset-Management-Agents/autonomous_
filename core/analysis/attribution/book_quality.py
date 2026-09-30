"""
book_quality.py — RTR-0 Inc 2 (#2402): Top-N book-quality metrics.

The 55% forensics (models-archive/rl-lineage/V2_EVIDENCE.md) showed the
proven alpha mechanism was PICK QUALITY — a handful of names, held — not
5-day timing. Inc 1 (replay/metrics/report, PR #2398) measures timing ICs;
this module answers the actual question: does the cross-sectional ranking
reproducibly select good BOOKS?

Option B of the #2402 plan: a separate module that consumes Inc-1 outputs
READ-ONLY (the replay vote panel and/or the live LSTM panel store) and
contributes an ADDITIVE ``book_quality`` field to the gate JSON —
``report.py`` and the Inc-1 truth table stay untouched.

Method (per ranking source, per N in {5,10}, per horizon in {4,8,12} weeks):
  * every ``step_bars``-th panel date is a rebalance date; the cross-section
    is ranked by score (deterministic tie-break: symbol name) -> Top-N book;
  * forward book return = equal-weight mean of ``close[t+h]/close[t]-1``
    over the book, NET of one-way transaction costs (``cost_bps``, harness
    convention #1969: 10 bps) on every unit of weight turnover
    (entry from flat = 1.0; between books = sum |w_new - w_old|);
  * baselines: SPY buy-and-hold, equal-weight universe, and a Random-N
    bootstrap (>= 1000 random books, fixed seed, SAME dates / SAME cost
    model). Rev 2 (PR #2405 review F1): the null is PERSISTENCE-MATCHED —
    each bootstrap iteration draws ONE static random score per symbol and
    pushes it through the same Top-N pipeline, so random books hold like
    the quasi-static system book (turnover arises only from universe
    entry/exit). The Rev-1 i.i.d.-per-date null churned ~2(N-1)/N per
    window, diversified the held-book drift away and was size-inflated
    (zero-skill static rankings reached pctile >= 95 in ~33% of seeds);
    ``vs_random_pctile`` = percentile of the system's mean net return
    within this held-random-book distribution;
  * precision@N = share of Top-N picks whose forward return beats the
    universe MEDIAN over the same window (strictly greater), averaged
    across rebalance dates — exact on a known ranking.

Honesty rules (M1 discipline carried over from Inc 1):
  * overlapping windows (step < horizon) are NOT independent samples:
    ``n_effective = n_windows * step / horizon`` is reported, and the mean
    net return carries a HAC/Newey-West t over the window series with
    lag = overlap factor - 1 (reuses ``metrics.hac_mean_test``);
  * a cross-section with a CONSTANT score is unrankable — the date is
    skipped, never turned into an alphabetical book; the same rule applies
    to a DEGENERATE Top-N cut (every book slot tied at the cut score with
    more candidates outside — a purely alphabetical selection): the window
    is skipped and the skip is counted in the artifact
    (``skipped_degenerate_cut_windows``); partial ties at the cut are
    reported as ``tie_share`` (review F3);
  * NO forward-survival filtering (review F2): a symbol whose price series
    ends INSIDE the forward window (delisting) stays in the book with its
    return truncated at the last available bar — a delisting crash burdens
    the book, it never silently vanishes from the cross-section at the
    ranking date. ``truncated_count``/``excluded_count`` surface in the
    artifact (excluded = no bar at the ranking date -> untradeable).
    Windows where NO symbol has a full forward window (the sample edge)
    are dropped entirely, exactly as before — horizons stay honest;
  * a universe below ``MIN_UNIVERSE_BREADTH`` yields
    ``verdict="insufficient_universe"`` and gates NOTHING — the 10-symbol
    smoke MUST end this way (full statement requires the #2403/#1907 panel);
  * a missing SPY series yields ``vs_spy = None`` — never fabricated, and
    without it the activation criterion cannot be met;
  * the LSTM ranking (primary) and the consensus ranking (comparison) are
    reported SIDE BY SIDE — the delta is the Round Table's added value
    (#1968/#1951); an all-abstaining LSTM surfaces as available=False with
    a reason, never silently dropped.

Activation criterion (issue #2402 §2, evaluated per ranking source): the
Top-N book beats the Random-N bootstrap at percentile >= 95 AND SPY
net-of-cost over >= 2 horizons for at least one N — otherwise no #2388 arm.

OFFLINE ONLY: nothing in the product imports this module (Airlock §5.7).
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from core.analysis.attribution.metrics import (
    consensus_positions,
    hac_mean_test,
    opinion_mask,
)

# ADR-RTR-02: minimum universe breadth = 100 symbols (plan #2402 §3 / V-1).
# Rationale: (1) a Top-10 book from a 10-name universe IS the universe —
# selection is unmeasurable and the Random-N baseline degenerates to the
# system book (pctile pinned at 50); meaningful dispersion of the random
# baseline needs a selection quota of at most ~10% (10/100). (2) The 55%
# forensics picked ~6 names from a ~100-name universe — this metric must
# measure that mechanism at comparable breadth. Reviewed with every run.
MIN_UNIVERSE_BREADTH = 100

# Plan #2402 §1 defaults — CLI smoke and tests may narrow them, the gate
# artifact records the actually-used values in ``params``.
DEFAULT_NS: Tuple[int, ...] = (5, 10)
DEFAULT_HORIZONS_WEEKS: Tuple[int, ...] = (4, 8, 12)
BARS_PER_WEEK = 5  # daily bars, trading week
DEFAULT_STEP_BARS = 5  # weekly rebalance grid
DEFAULT_N_BOOTSTRAP = 1000  # plan §1: >= 1000 random books

LSTM_PANEL_UNAVAILABLE_REASON = (
    "LSTMSignalAgent abstained on every replayed bar (no offline model "
    "artifact) — no LSTM ranking in this run; the full statement requires "
    "the #2403 universe replay / #1907 LSTM panel"
)

LSTM_OFFLINE_PANEL_EMPTY_REASON = (
    "offline LSTM scorer (#2409) produced no cross-section on any replay "
    "date (abstention/collapse contract) — no LSTM ranking in this run"
)


def parse_book_bootstrap(value: str) -> int:
    """``argparse`` type for ``--book-bootstrap`` — hard floor 1000.

    Plan #2402 §1 demands >= 1000 random books; an under-powered gate run
    must fail LOUDLY at the CLI instead of recording an honest-but-ignored
    ``params`` value (PR #2405 review F4).
    """
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid int value: {value!r}") from exc
    if parsed < DEFAULT_N_BOOTSTRAP:
        raise argparse.ArgumentTypeError(
            f"--book-bootstrap must be >= {DEFAULT_N_BOOTSTRAP} "
            f"(plan #2402 §1), got {parsed}"
        )
    return parsed


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RankingSpec:
    """One ranking source: ``panel`` columns ``ts, symbol, score``.

    An empty panel means the source is UNAVAILABLE in this run;
    ``unavailable_reason`` then carries the honest, human-readable why.
    ``basis`` states the score provenance (Rev 3, PR #2421 — e.g.
    "offline scorer, parity-pinned") and surfaces in the artifact.
    """

    source: str
    panel: pd.DataFrame
    unavailable_reason: str = ""
    basis: str = ""


def lstm_panel_from_votes(votes_df: pd.DataFrame) -> pd.DataFrame:
    """LSTM score panel from the Inc-1 replay votes (read-only).

    Only real OPINIONS count (``opinion_mask`` — the abstention contract is
    score 0.5 at weight 0). An LSTM that abstained everywhere yields an
    EMPTY panel — the caller must surface it as unavailable, never rank on
    fabricated neutrals.
    """
    if votes_df.empty:
        return pd.DataFrame(columns=["ts", "symbol", "score"])
    lstm = votes_df[votes_df["agent"] == "LSTMSignalAgent"]
    opinions = lstm[opinion_mask(lstm)]
    return opinions[["ts", "symbol", "score"]].reset_index(drop=True)


def consensus_panel_from_votes(votes_df: pd.DataFrame) -> pd.DataFrame:
    """Consensus score panel via the replicated REAL engine (Inc-1 reuse).

    ``consensus_positions`` (metrics.py) calls the real
    ``ConsensusEngine.aggregate`` per (ts, symbol) — this panel can not
    drift from production aggregation.
    """
    if votes_df.empty:
        return pd.DataFrame(columns=["ts", "symbol", "score"])
    positions = consensus_positions(votes_df)
    panel = positions[["ts", "symbol", "consensus_score"]].rename(
        columns={"consensus_score": "score"}
    )
    return panel.reset_index(drop=True)


def build_ranking_specs(
    votes_df: pd.DataFrame,
    *,
    lstm_panel: Optional[pd.DataFrame] = None,
    lstm_basis: str = "",
) -> List[RankingSpec]:
    """The #2402 dual ranking: LSTM (primary) + consensus (comparison).

    Rev 3 (PR #2421): when the replay ran the offline LSTM scorer (#2409),
    the caller passes its RAW per-date cross-sections as ``lstm_panel``
    (``OfflineLstmStrategy.score_panel()``) plus a ``lstm_basis`` provenance
    label — the ``lstm`` book ranking then consumes the scorer directly
    instead of the tanh-mapped vote scores (monotone-equal order, but raw
    scores cannot saturate into artificial ties). Without an override the
    vote-derived panel is used, exactly as before.
    """
    if lstm_panel is not None:
        lstm = lstm_panel
        empty_reason = LSTM_OFFLINE_PANEL_EMPTY_REASON
    else:
        lstm = lstm_panel_from_votes(votes_df)
        empty_reason = LSTM_PANEL_UNAVAILABLE_REASON
    return [
        RankingSpec(
            source="lstm",
            panel=lstm,
            unavailable_reason="" if not lstm.empty else empty_reason,
            basis=lstm_basis,
        ),
        RankingSpec(source="consensus", panel=consensus_panel_from_votes(votes_df)),
    ]


def lstm_panel_from_store(store, dates: Sequence) -> pd.DataFrame:
    """Score panel from the live ``LstmPanelStore`` via ``cross_section_at``
    (lstm_panel_store.py:156) — the primary ranking source once a live-
    captured panel exists (#1907). PIT per the store's contract: the latest
    snapshot at/before each requested date; the store is live-only, so an
    offline replay run normally has nothing here (documented limit).
    """
    rows = []
    for d in dates:
        ts = pd.Timestamp(d)
        for symbol, score in store.cross_section_at(ts).items():
            rows.append((ts, symbol, float(score)))
    return pd.DataFrame(rows, columns=["ts", "symbol", "score"])


def closes_from_provider(provider) -> Dict[str, pd.Series]:
    """Close series per symbol from a ``ReplayBarsProvider`` (read-only)."""
    return {sym: provider.frames[sym]["close"] for sym in provider.symbols}


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BookQualityEntry:
    """One (N, horizon) line of the book-quality table."""

    n: int
    horizon_weeks: int
    horizon_bars: int
    n_windows: int
    n_effective: int
    net_return: Optional[float]  # mean per-window net book return
    hac_t: Optional[float]
    hac_p: Optional[float]
    spy_return: Optional[float]
    vs_spy: Optional[float]
    ew_return: Optional[float]
    vs_ew_universe: Optional[float]
    vs_random_pctile: Optional[float]
    precision_at_n: Optional[float]
    avg_turnover: Optional[float]
    # Rev 2 (PR #2405): F2 delisting/tradeability + F3 tie diagnostics
    excluded_count: Optional[int]  # scored but no bar at t (untradeable)
    truncated_count: Optional[int]  # in book, series ends inside the window
    tie_share: Optional[float]  # mean share of book slots decided by tie-break
    skipped_degenerate_cut_windows: int  # whole cut tied -> window skipped
    verdict: str


@dataclass
class RankingBookQuality:
    """All entries of one ranking source + its source-level verdict."""

    source: str
    available: bool
    reason: str = ""
    basis: str = ""  # score provenance of the spec (Rev 3, PR #2421)
    universe_breadth: int = 0
    entries: List[BookQualityEntry] = field(default_factory=list)
    verdict: str = "unavailable"


@dataclass
class BookQualityResult:
    """The additive ``book_quality`` gate contribution."""

    params: Dict[str, object]
    rankings: List[RankingBookQuality]
    verdict: str


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _normalise_closes(closes: Mapping[str, pd.Series]) -> Dict[str, pd.Series]:
    out: Dict[str, pd.Series] = {}
    for sym, series in closes.items():
        s = series.copy()
        idx = pd.to_datetime(s.index)
        if getattr(idx, "tz", None) is not None:
            idx = idx.tz_localize(None)
        s.index = idx.normalize()
        out[str(sym)] = s.sort_index()
    return out


def _panel_by_date(panel: pd.DataFrame) -> Dict[pd.Timestamp, Dict[str, float]]:
    df = panel.copy()
    ts = pd.to_datetime(df["ts"])
    if getattr(ts.dt, "tz", None) is not None:
        ts = ts.dt.tz_localize(None)
    df["ts"] = ts.dt.normalize()
    df = df.sort_values(["ts", "symbol"])
    df = df.groupby(["ts", "symbol"], as_index=False)["score"].last()
    return {
        d: dict(zip(g["symbol"], g["score"].astype(float))) for d, g in df.groupby("ts")
    }


def _window_return(
    series: pd.Series, date: pd.Timestamp, horizon_bars: int
) -> Optional[float]:
    """``close[t+h]/close[t] - 1`` positionally on the symbol's own bars.

    ``None`` when the date is not a bar of this symbol or the forward
    window runs past the end of the series — dropped, never padded.
    """
    idx = series.index
    pos = int(idx.searchsorted(date))
    if pos >= len(idx) or idx[pos] != date:
        return None
    end = pos + int(horizon_bars)
    if end >= len(idx):
        return None
    base = float(series.iloc[pos])
    if base == 0.0 or not math.isfinite(base):
        return None
    fwd = float(series.iloc[end]) / base - 1.0
    return fwd if math.isfinite(fwd) else None


def _turnover(prev: Optional[Sequence[str]], curr: Sequence[str]) -> float:
    """Sum of absolute equal-weight changes; entry from flat = 1.0."""
    if prev is None:
        return 1.0
    wp, wc = 1.0 / len(prev), 1.0 / len(curr)
    prev_set, curr_set = set(prev), set(curr)
    return float(
        sum(
            abs((wc if s in curr_set else 0.0) - (wp if s in prev_set else 0.0))
            for s in prev_set | curr_set
        )
    )


def _truncated_window_return(
    series: pd.Series, date: pd.Timestamp, horizon_bars: int
) -> Optional[Tuple[float, bool]]:
    """``close[min(t+h, last)] / close[t] - 1`` positionally on the symbol's
    own bars — the delisting-honest window return (PR #2405 review F2).

    ``None`` only when the date is not a bar of this symbol (no position is
    possible — counted as excluded by the caller). Otherwise
    ``(return, truncated)``: a symbol whose series ends inside the forward
    window stays investable at ``t`` and its position goes flat at the LAST
    available bar — a delisting crash burdens the book instead of being
    survival-filtered out of the cross-section at the ranking date.
    ``truncated=False`` means the full ``horizon_bars`` window existed.
    """
    idx = series.index
    pos = int(idx.searchsorted(date))
    if pos >= len(idx) or idx[pos] != date:
        return None
    end = pos + int(horizon_bars)
    truncated = end >= len(idx)
    if truncated:
        end = len(idx) - 1
    base = float(series.iloc[pos])
    if base == 0.0 or not math.isfinite(base):
        return None
    fwd = float(series.iloc[end]) / base - 1.0
    if not math.isfinite(fwd):
        return None
    return fwd, truncated


@dataclass(frozen=True)
class _Window:
    """One scored rebalance date with its valid, ranked cross-section."""

    date: pd.Timestamp
    symbols: List[str]  # tradeable universe (has score + a bar at t)
    scores: np.ndarray  # aligned to ``symbols``
    fwd: np.ndarray  # aligned to ``symbols`` (truncated at delisting, F2)
    excluded_count: int  # scored but untradeable at t (no bar / bad score)
    truncated_count: int  # members whose series ends inside the window


def _build_windows(
    panel_by_date: Mapping[pd.Timestamp, Mapping[str, float]],
    rebalance_dates: Sequence[pd.Timestamp],
    closes: Mapping[str, pd.Series],
    horizon_bars: int,
    min_book: int,
) -> List[_Window]:
    windows: List[_Window] = []
    for d in rebalance_dates:
        xsec = panel_by_date[d]
        symbols: List[str] = []
        scores: List[float] = []
        fwd: List[float] = []
        excluded = 0
        truncated_count = 0
        any_full = False
        for sym in sorted(xsec):
            series = closes.get(sym)
            score = float(xsec[sym])
            if series is None or not math.isfinite(score):
                excluded += 1
                continue
            res = _truncated_window_return(series, d, horizon_bars)
            if res is None:
                # no bar at t -> the book could not have bought it (this is
                # tradeability at t, NOT forward-survival — review F2)
                excluded += 1
                continue
            ret, truncated = res
            symbols.append(sym)
            scores.append(score)
            fwd.append(ret)
            truncated_count += int(truncated)
            any_full = any_full or not truncated
        if not any_full:
            # EVERY tradeable symbol is truncated -> the window runs past
            # the sample edge (no forward calendar at all, not a delisting)
            # — dropped exactly as in Rev 1, horizons stay honest
            continue
        if len(symbols) < min_book:
            continue
        if len(set(scores)) < 2:
            # constant cross-section = no ranking information — an
            # alphabetical "book" would be a fabricated selection (honesty)
            continue
        windows.append(
            _Window(
                date=d,
                symbols=symbols,
                scores=np.asarray(scores, dtype=float),
                fwd=np.asarray(fwd, dtype=float),
                excluded_count=excluded,
                truncated_count=truncated_count,
            )
        )
    return windows


def _top_n_with_cut_info(window: _Window, n: int) -> Tuple[List[str], float, bool]:
    """Top-N book + tie diagnostics at the cut (PR #2405 review F3).

    Returns ``(book, tie_share, degenerate)``. ``tie_share`` is the fraction
    of book slots whose membership was decided by the alphabetical tie-break
    (a partial tie across the Top-N boundary). ``degenerate`` is True when
    EVERY book slot carries the cut score while more candidates outside the
    book share it — the book would be a purely alphabetical selection (the
    honesty rule's forbidden case); the caller must skip the window and
    count the skip in the artifact.
    """
    order = np.lexsort((np.asarray(window.symbols), -window.scores))
    book = [window.symbols[i] for i in order[:n]]
    if n >= len(window.symbols):
        return book, 0.0, False
    cut_score = float(window.scores[order[n - 1]])
    tied_total = int(np.sum(window.scores == cut_score))
    tied_in = int(sum(1 for i in order[:n] if float(window.scores[i]) == cut_score))
    if tied_total <= tied_in:
        # the whole tie group made it into the book — no boundary ambiguity
        return book, 0.0, False
    return book, tied_in / float(n), tied_in == n


def _series_net(
    windows: Sequence[_Window],
    books: Sequence[Sequence[str]],
    cost_rate: float,
) -> Tuple[List[float], List[float]]:
    """Net window returns + turnovers of a book sequence over ``windows``."""
    nets: List[float] = []
    turns: List[float] = []
    prev: Optional[Sequence[str]] = None
    for window, book in zip(windows, books):
        idx = [window.symbols.index(s) for s in book]
        gross = float(window.fwd[np.asarray(idx, dtype=int)].mean())
        to = _turnover(prev, book)
        nets.append(gross - to * cost_rate)
        turns.append(to)
        prev = book
    return nets, turns


def _spy_net(
    windows: Sequence[_Window],
    closes: Mapping[str, pd.Series],
    spy_symbol: str,
    horizon_bars: int,
    cost_rate: float,
) -> Optional[List[float]]:
    """SPY buy-and-hold window returns, entry cost once — None when SPY is
    missing or lacks any window (no partial, silently-shifted baseline)."""
    series = closes.get(spy_symbol)
    if series is None or not windows:
        return None
    rets = [_window_return(series, w.date, horizon_bars) for w in windows]
    if any(r is None for r in rets):
        return None
    return [float(r) - (cost_rate if i == 0 else 0.0) for i, r in enumerate(rets)]


def _random_pctile(
    windows: Sequence[_Window],
    n: int,
    system_mean: float,
    cost_rate: float,
    n_bootstrap: int,
    seed: int,
    horizon_bars: int,
) -> float:
    """Percentile of the system's mean net return among PERSISTENCE-MATCHED
    Random-N books (PR #2405 review F1).

    Null model: each bootstrap iteration draws ONE static random score per
    symbol of the union universe and ranks every window's cross-section by
    it — the same Top-N pipeline, same dates, same cost model. A static
    score HOLDS its book (turnover arises only when universe membership
    changes), matching the variance of the quasi-static system book. The
    Rev-1 null re-drew books i.i.d. per rebalance date: its churn
    (~2(N-1)/N per window) diversified the held-book drift away, the null
    mean-distribution came out far too tight, and a ZERO-SKILL static
    ranking reached pctile >= 95 in ~33% of seeds (bimodal instead of
    uniform) — size-inflated for the >= 95 activation gate. Calibration is
    test-pinned (``TestNullCalibration``). Seeded per (seed, n, horizon) so
    the number is deterministic regardless of evaluation order.
    """
    rng = np.random.default_rng([seed, n, horizon_bars])
    universe = sorted({s for w in windows for s in w.symbols})
    sym_pos = {s: i for i, s in enumerate(universe)}
    member_pos = [
        np.asarray([sym_pos[s] for s in w.symbols], dtype=int) for w in windows
    ]
    dist = np.empty(n_bootstrap, dtype=float)
    n_windows = len(windows)
    for b in range(n_bootstrap):
        static_scores = rng.random(len(universe))
        prev_set: Optional[frozenset] = None
        total = 0.0
        for window, members in zip(windows, member_pos):
            order = np.argsort(-static_scores[members], kind="stable")
            idx = order[:n]
            picks = frozenset(window.symbols[i] for i in idx)
            gross = float(window.fwd[idx].mean())
            if prev_set is None:
                to = 1.0
            else:
                to = 2.0 * (n - len(picks & prev_set)) / n
            total += gross - to * cost_rate
            prev_set = picks
        dist[b] = total / n_windows
    below = int((dist < system_mean - 1e-12).sum())
    ties = int((np.abs(dist - system_mean) <= 1e-12).sum())
    return 100.0 * (below + 0.5 * ties) / n_bootstrap


# ---------------------------------------------------------------------------
# Main computation
# ---------------------------------------------------------------------------


def _entry_verdict(
    breadth_ok: bool,
    n_windows: int,
    vs_random_pctile: Optional[float],
    vs_spy: Optional[float],
) -> str:
    if not breadth_ok:
        return "insufficient_universe"
    if n_windows == 0:
        return "no_data"
    if (
        vs_random_pctile is not None
        and vs_random_pctile >= 95.0
        and vs_spy is not None
        and vs_spy > 0.0
    ):
        return "beats_baselines"
    return "no_edge"


def _source_verdict(
    available: bool, breadth_ok: bool, entries: Sequence[BookQualityEntry]
) -> str:
    if not available:
        return "unavailable"
    if not breadth_ok:
        return "insufficient_universe"
    by_n: Dict[int, int] = {}
    for e in entries:
        if e.verdict == "beats_baselines":
            by_n[e.n] = by_n.get(e.n, 0) + 1
    # issue #2402 §2: >= 2 horizons must beat baselines for one N
    if any(count >= 2 for count in by_n.values()):
        return "activation_supported"
    return "no_activation"


def compute_book_quality(
    specs: Sequence[RankingSpec],
    closes: Mapping[str, pd.Series],
    *,
    spy_symbol: str = "SPY",
    ns: Sequence[int] = DEFAULT_NS,
    horizons_weeks: Sequence[int] = DEFAULT_HORIZONS_WEEKS,
    bars_per_week: int = BARS_PER_WEEK,
    step_bars: int = DEFAULT_STEP_BARS,
    cost_bps: float = 10.0,
    n_bootstrap: int = DEFAULT_N_BOOTSTRAP,
    seed: int = 0,
    min_universe_breadth: int = MIN_UNIVERSE_BREADTH,
) -> BookQualityResult:
    """Top-N book-quality metrics for every ranking source (issue #2402 §1)."""
    closes = _normalise_closes(closes)
    cost_rate = float(cost_bps) / 10_000.0

    rankings: List[RankingBookQuality] = []
    for spec in specs:
        if spec.panel is None or spec.panel.empty:
            rankings.append(
                RankingBookQuality(
                    source=spec.source,
                    available=False,
                    reason=spec.unavailable_reason or "empty ranking panel",
                    basis=spec.basis,
                    verdict="unavailable",
                )
            )
            continue

        panel_by_date = _panel_by_date(spec.panel)
        all_dates = sorted(panel_by_date)
        rebalance_dates = all_dates[::step_bars]
        sizes = [
            len([s for s in panel_by_date[d] if s in closes]) for d in rebalance_dates
        ]
        breadth = int(np.median(sizes)) if sizes else 0
        breadth_ok = breadth >= int(min_universe_breadth)

        entries: List[BookQualityEntry] = []
        for n in ns:
            for h_weeks in horizons_weeks:
                h_bars = int(h_weeks) * int(bars_per_week)
                built = _build_windows(
                    panel_by_date, rebalance_dates, closes, h_bars, min_book=n
                )
                # F3: a degenerate Top-N cut (whole book tied at the cut
                # score with more candidates outside) would be a purely
                # alphabetical selection — skipped WITH the reason counted
                windows: List[_Window] = []
                books: List[List[str]] = []
                tie_shares: List[float] = []
                degenerate_skips = 0
                for w in built:
                    book, w_tie_share, degenerate = _top_n_with_cut_info(w, int(n))
                    if degenerate:
                        degenerate_skips += 1
                        continue
                    windows.append(w)
                    books.append(book)
                    tie_shares.append(w_tie_share)

                if not windows:
                    entries.append(
                        BookQualityEntry(
                            n=int(n),
                            horizon_weeks=int(h_weeks),
                            horizon_bars=h_bars,
                            n_windows=0,
                            n_effective=0,
                            net_return=None,
                            hac_t=None,
                            hac_p=None,
                            spy_return=None,
                            vs_spy=None,
                            ew_return=None,
                            vs_ew_universe=None,
                            vs_random_pctile=None,
                            precision_at_n=None,
                            avg_turnover=None,
                            excluded_count=None,
                            truncated_count=None,
                            tie_share=None,
                            skipped_degenerate_cut_windows=degenerate_skips,
                            verdict=_entry_verdict(breadth_ok, 0, None, None),
                        )
                    )
                    continue

                sys_net, sys_turns = _series_net(windows, books, cost_rate)
                net_mean = float(np.mean(sys_net))

                precisions = []
                for window, book in zip(windows, books):
                    med = float(np.median(window.fwd))
                    idx = np.asarray([window.symbols.index(s) for s in book], dtype=int)
                    precisions.append(float((window.fwd[idx] > med).mean()))
                precision = float(np.mean(precisions))

                ew_nets, _ = _series_net(
                    windows, [w.symbols for w in windows], cost_rate
                )
                ew_mean = float(np.mean(ew_nets))

                spy_nets = _spy_net(windows, closes, spy_symbol, h_bars, cost_rate)
                spy_mean = None if spy_nets is None else float(np.mean(spy_nets))
                vs_spy = None if spy_mean is None else net_mean - spy_mean

                pctile = _random_pctile(
                    windows,
                    int(n),
                    net_mean,
                    cost_rate,
                    int(n_bootstrap),
                    int(seed),
                    h_bars,
                )

                # overlap factor horizon/step -> honest effective N + HAC t
                n_windows = len(windows)
                n_effective = max(1, (n_windows * int(step_bars)) // h_bars)
                hac_lag = max(0, math.ceil(h_bars / int(step_bars)) - 1)
                hac = hac_mean_test(sys_net, lag=hac_lag)

                entries.append(
                    BookQualityEntry(
                        n=int(n),
                        horizon_weeks=int(h_weeks),
                        horizon_bars=h_bars,
                        n_windows=n_windows,
                        n_effective=int(n_effective),
                        net_return=net_mean,
                        hac_t=float(hac.t_stat),
                        hac_p=float(hac.p_value),
                        spy_return=spy_mean,
                        vs_spy=vs_spy,
                        ew_return=ew_mean,
                        vs_ew_universe=net_mean - ew_mean,
                        vs_random_pctile=pctile,
                        precision_at_n=precision,
                        avg_turnover=float(np.mean(sys_turns)),
                        excluded_count=int(sum(w.excluded_count for w in windows)),
                        truncated_count=int(sum(w.truncated_count for w in windows)),
                        tie_share=float(np.mean(tie_shares)),
                        skipped_degenerate_cut_windows=degenerate_skips,
                        verdict=_entry_verdict(breadth_ok, n_windows, pctile, vs_spy),
                    )
                )

        rankings.append(
            RankingBookQuality(
                source=spec.source,
                available=True,
                reason="",
                basis=spec.basis,
                universe_breadth=breadth,
                entries=entries,
                verdict=_source_verdict(True, breadth_ok, entries),
            )
        )

    verdicts = [r.verdict for r in rankings]
    if "activation_supported" in verdicts:
        overall = "activation_supported"
    elif "no_activation" in verdicts:
        overall = "no_activation"
    elif "insufficient_universe" in verdicts:
        overall = "insufficient_universe"
    else:
        overall = "unavailable"

    params: Dict[str, object] = {
        "ns": [int(n) for n in ns],
        "horizons_weeks": [int(h) for h in horizons_weeks],
        "bars_per_week": int(bars_per_week),
        "step_bars": int(step_bars),
        "cost_bps": float(cost_bps),
        "n_bootstrap": int(n_bootstrap),
        "seed": int(seed),
        "min_universe_breadth": int(min_universe_breadth),
        "spy_symbol": spy_symbol,
        "null_model": (
            "persistence-matched (Rev 2, PR #2405 F1): one static random "
            "score per symbol per bootstrap iteration through the same "
            "Top-N pipeline — random books HOLD like the system book; the "
            "Rev-1 i.i.d.-per-date churn null was size-inflated"
        ),
        "activation_rule": (
            "vs_random_pctile >= 95 AND vs_spy > 0 over >= 2 horizons for "
            "one N (issue #2402 §2); verdict=insufficient_universe gates "
            "NOTHING"
        ),
    }
    return BookQualityResult(params=params, rankings=rankings, verdict=overall)


# ---------------------------------------------------------------------------
# Additive gate-JSON payload + Markdown section (report.py stays untouched)
# ---------------------------------------------------------------------------


def book_quality_payload(result: BookQualityResult) -> Dict[str, object]:
    """The additive ``book_quality`` field of the gate JSON (#2402 §2)."""
    return {
        "issue": "#2402 RTR-0 Inc 2",
        "params": dict(result.params),
        "rankings": {
            r.source: {
                "available": r.available,
                "reason": r.reason,
                "basis": r.basis,
                "universe_breadth": r.universe_breadth,
                "verdict": r.verdict,
                "entries": [
                    {
                        "N": e.n,
                        "horizon_weeks": e.horizon_weeks,
                        "horizon_bars": e.horizon_bars,
                        "n_windows": e.n_windows,
                        "n_effective": e.n_effective,
                        "net_return": e.net_return,
                        "hac_t": e.hac_t,
                        "hac_p": e.hac_p,
                        "spy_return": e.spy_return,
                        "vs_spy": e.vs_spy,
                        "ew_return": e.ew_return,
                        "vs_ew_universe": e.vs_ew_universe,
                        "vs_random_pctile": e.vs_random_pctile,
                        "precision_at_n": e.precision_at_n,
                        "avg_turnover": e.avg_turnover,
                        "excluded_count": e.excluded_count,
                        "truncated_count": e.truncated_count,
                        "tie_share": e.tie_share,
                        "skipped_degenerate_cut_windows": (
                            e.skipped_degenerate_cut_windows
                        ),
                        "verdict": e.verdict,
                    }
                    for e in r.entries
                ],
            }
            for r in result.rankings
        },
        "verdict": result.verdict,
    }


def _fmt(value: Optional[float], spec: str = "+.4f") -> str:
    return "—" if value is None else format(value, spec)


def _fmt_count(value: Optional[int]) -> str:
    return "—" if value is None else str(int(value))


def render_book_quality_markdown(result: BookQualityResult) -> str:
    """Additive RESULTS.md section — appended after the Inc-1 artifact."""
    lines: List[str] = []
    lines.append("## Buch-Qualität (Inc 2 · #2402 — Top-N-Pick-Qualität)")
    lines.append("")
    lines.append(
        "> Misst, ob die Cross-Section reproduzierbar gute BÜCHER rankt "
        "(55-%-Forensik: Pick-Qualität, nicht Timing). Aktivierungs-Regel "
        "(#2388-Arm / RTR-5/B3): `vs_random_pctile ≥ 95` UND `vs_spy > 0` "
        "über ≥ 2 Horizonte für ein N — `insufficient_universe` gatet "
        "NICHTS."
    )
    lines.append("")
    lines.append("```json")
    import json

    lines.append(json.dumps(result.params, indent=2, sort_keys=True))
    lines.append("```")
    lines.append("")
    for r in result.rankings:
        label = f"{r.source} ({r.basis})" if r.basis else r.source
        lines.append(f"### Ranking: {label}")
        lines.append("")
        if not r.available:
            lines.append(f"**Nicht verfügbar:** {r.reason}")
            lines.append("")
            continue
        lines.append(
            f"Universe-Breite (Median je Rebalance-Datum): **{r.universe_breadth}** "
            f"(Minimum {result.params['min_universe_breadth']}) — "
            f"Source-Verdict: **{r.verdict}**"
        )
        lines.append("")
        lines.append(
            "| N | Horizont (Wo) | Fenster | n_eff | Net-Return Ø | t (NW) | "
            "p (NW) | SPY Ø | vs SPY | EW Ø | vs EW | Random-Pctile | "
            "precision@N | Ø Turnover | Excl | Trunc | Tie@Cut | Cut-Skips | "
            "Verdict |"
        )
        lines.append("|---|" + "---|" * 18)
        for e in r.entries:
            lines.append(
                f"| {e.n} | {e.horizon_weeks} | {e.n_windows} | {e.n_effective} | "
                f"{_fmt(e.net_return)} | {_fmt(e.hac_t, '+.2f')} | "
                f"{_fmt(e.hac_p, '.4f')} | {_fmt(e.spy_return)} | "
                f"{_fmt(e.vs_spy)} | {_fmt(e.ew_return)} | "
                f"{_fmt(e.vs_ew_universe)} | {_fmt(e.vs_random_pctile, '.1f')} | "
                f"{_fmt(e.precision_at_n, '.3f')} | {_fmt(e.avg_turnover, '.2f')} | "
                f"{_fmt_count(e.excluded_count)} | {_fmt_count(e.truncated_count)} | "
                f"{_fmt(e.tie_share, '.2f')} | {e.skipped_degenerate_cut_windows} | "
                f"{e.verdict} |"
            )
        lines.append("")
    lines.append(
        "**Ehrlichkeit:** Überlappende Fenster (Step < Horizont) sind keine "
        "unabhängigen Stichproben — `n_eff = Fenster × Step / Horizont` ist "
        "das ehrliche effektive N; t/p via HAC/Newey-West über die "
        "Fenster-Serie (Lag = Überlapp-Faktor − 1, M1-Disziplin). "
        "Random-Baseline (Rev 2, F1): **persistenz-gematcht** — je "
        "Bootstrap-Iteration ein statischer Zufalls-Score je Symbol durch "
        "dieselbe Top-N-Pipeline (Zufallsbücher HALTEN wie das Systembuch; "
        "gleiche Daten, gleiches Kostenmodell, fixer Seed; die i.i.d.-Churn-"
        "Null aus Rev 1 war size-inflated). Kein Survival-Filter (F2): "
        "Symbole, die im Fenster delisten, bleiben mit truncated Return bis "
        "zum letzten Bar im Buch (`Trunc`); `Excl` = gescort, aber ohne Bar "
        "am Ranking-Datum (nicht handelbar). Konstante Score-Cross-Sections "
        "sind unrankbar und werden übersprungen, nie alphabetisch verbucht; "
        "partielle Ties an der Top-N-Grenze werden als `Tie@Cut` "
        "ausgewiesen, ein degenerierter Cut (gesamtes Buch = Alphabet-Pick "
        "aus einer Tie-Gruppe) überspringt das Fenster (`Cut-Skips`, F3)."
    )
    lines.append("")
    return "\n".join(lines)
