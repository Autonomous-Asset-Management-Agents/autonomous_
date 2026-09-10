"""
metrics.py — RTR-0 (#1947) attribution metric library.

Pure functions over vote panels. The consensus replication NEVER re-derives
the weighted mean: it constructs ``VoteResult`` objects and calls the REAL
``ConsensusEngine.aggregate`` (consensus.py:160-214), so the conditioner
exclusions (consensus.py:190-195) and the regime<0.45 -> Momentum*0.5
coupling (consensus.py:179-188) can not drift from production (plan §12.4).
The agent-level veto gate mirrors runner.py:176-193 (direction-aware: a veto
blocks HOLD/BUY, never a risk-reducing SELL).

Vote panel schema (``votes_df``): columns
    ts, symbol, agent, score, weight, vetoed
Forward-return panel (``fwd_df``): columns
    ts, symbol, fwd_ret

IC significance (M1, adversarial review 2026-07-22): agent-level significance
is computed on the PER-DATE cross-sectional Spearman-IC time series with a
HAC/Newey-West t (lag ~ label horizon) — never on the pooled panel with an
iid t, which overstates the effective N by orders of magnitude (overlapping
forward labels + correlated cross-sections).

Signal thresholds are IMPORTED from consensus.py (single source of truth,
ADR-SEC-01) — never duplicated as literals here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

from core.ml.validation import purged_walk_forward
from core.round_table.base_agent import VoteResult
from core.round_table.consensus import (
    SIGNAL_BUY_THRESHOLD,
    SIGNAL_SELL_THRESHOLD,
    ConsensusEngine,
)

# Annualisation for daily bars (trading days).
_TRADING_DAYS_PER_YEAR = 252.0

# Verdict threshold on the LOO delta-Sharpe: below this magnitude a
# contribution is treated as noise rather than signal. Editorial start
# calibration for the gate report — reviewed with every benchmark run.
DELTA_SHARPE_NOISE_BAND = 0.05


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ICResult:
    """Spearman information coefficient with significance."""

    ic: float
    t_stat: float
    p_value: float
    n: int


@dataclass(frozen=True)
class HACTestResult:
    """HAC / Newey-West test of H0: series mean == 0."""

    mean: float
    t_stat: float
    p_value: float
    n: int
    lag: int


@dataclass(frozen=True)
class DailyICResult:
    """Cross-sectional daily-IC significance (M1 remediation).

    ``ic_mean``: mean of the per-date cross-sectional Spearman ICs.
    ``t_stat`` / ``p_value``: HAC / Newey-West over the daily IC series
    (lag = label horizon), never the pooled-iid t of the stacked panel.
    ``method``: ``per-date-cs-hac`` or the explicit, labelled
    ``pooled-iid-fallback`` (only when the panel has no usable
    cross-section, e.g. a single-symbol run).
    """

    ic_mean: float
    t_stat: float
    p_value: float
    n_days: int
    hac_lag: int
    method: str


@dataclass(frozen=True)
class HitRateResult:
    """Directional hit-rate over BUY/SELL territory votes only."""

    rate: float
    n_signals: int
    n_hits: int


@dataclass(frozen=True)
class SharpeResult:
    """Annualised net-of-cost Sharpe of the replicated consensus book."""

    sharpe: float
    mean_daily_ret: float
    daily_vol: float
    n_days: int
    total_cost: float


@dataclass(frozen=True)
class LOOResult:
    """Leave-one-out marginal contribution of one agent."""

    agent: str
    sharpe_all: float
    sharpe_without: float
    delta_sharpe: float


# ---------------------------------------------------------------------------
# R1 — Spearman IC (+t) with clean degeneracy handling
# ---------------------------------------------------------------------------


def _rank(values: np.ndarray) -> np.ndarray:
    """Average ranks (ties -> mean rank), matching Spearman convention."""
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=float)
    sorted_vals = values[order]
    i = 0
    while i < len(values):
        j = i
        while j + 1 < len(values) and sorted_vals[j + 1] == sorted_vals[i]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        ranks[order[i : j + 1]] = avg
        i = j + 1
    return ranks


def _normal_sf(x: float) -> float:
    """Survival function of the standard normal via erfc (no scipy needed)."""
    return 0.5 * math.erfc(x / math.sqrt(2.0))


def spearman_ic(scores: Sequence[float], fwd_ret: Sequence[float]) -> ICResult:
    """Spearman rank IC between agent scores and forward returns.

    Degenerate inputs (n < 3, constant scores or constant targets, NaN-only)
    resolve to ``ICResult(0.0, 0.0, 1.0, n)`` — an honest zero, never a crash
    and never a fabricated significance.

    t-statistic: ``t = ic * sqrt((n - 2) / (1 - ic^2))`` — an IID t that is
    ONLY valid on independent observations. ⚠ On a stacked (ts, symbol) panel
    with overlapping forward-return labels this t is inflated by serial AND
    cross-sectional correlation (adversarial review M1): panel-level agent
    significance MUST go through ``daily_ic_significance`` (per-date
    cross-sectional ICs + HAC/Newey-West) instead. This function remains the
    building block for single cross-sections and the labelled pooled fallback.
    """
    s = np.asarray(scores, dtype=float)
    f = np.asarray(fwd_ret, dtype=float)
    mask = np.isfinite(s) & np.isfinite(f)
    s, f = s[mask], f[mask]
    n = int(s.size)
    if n < 3 or np.all(s == s[0]) or np.all(f == f[0]):
        return ICResult(ic=0.0, t_stat=0.0, p_value=1.0, n=n)

    rs, rf = _rank(s), _rank(f)
    rs = rs - rs.mean()
    rf = rf - rf.mean()
    denom = math.sqrt(float(np.dot(rs, rs)) * float(np.dot(rf, rf)))
    if denom == 0.0:
        return ICResult(ic=0.0, t_stat=0.0, p_value=1.0, n=n)
    ic = float(np.dot(rs, rf) / denom)
    ic = max(-1.0, min(1.0, ic))

    if abs(ic) >= 1.0:
        t_stat = math.copysign(1e12, ic)
        p_value = 0.0
    else:
        t_stat = ic * math.sqrt((n - 2) / (1.0 - ic * ic))
        p_value = 2.0 * _normal_sf(abs(t_stat))
    return ICResult(ic=ic, t_stat=float(t_stat), p_value=float(p_value), n=n)


# ---------------------------------------------------------------------------
# M1 — per-date cross-sectional IC series + HAC/Newey-West significance
# ---------------------------------------------------------------------------

# Minimum symbols per date for a meaningful cross-sectional rank correlation.
MIN_CROSS_SECTION = 3


def _spearman_rho(
    scores: np.ndarray, fwd_ret: np.ndarray, min_n: int = 2
) -> Optional[float]:
    """Spearman rho of one cross-section, ``None`` when degenerate (skip).

    ``min_n`` counts FINITE pairs — a date whose usable cross-section shrinks
    below the minimum after NaN-dropping is skipped, not force-computed.
    """
    mask = np.isfinite(scores) & np.isfinite(fwd_ret)
    s, f = scores[mask], fwd_ret[mask]
    if s.size < max(2, min_n) or np.all(s == s[0]) or np.all(f == f[0]):
        return None
    rs, rf = _rank(s), _rank(f)
    rs = rs - rs.mean()
    rf = rf - rf.mean()
    denom = math.sqrt(float(np.dot(rs, rs)) * float(np.dot(rf, rf)))
    if denom == 0.0:
        return None
    return max(-1.0, min(1.0, float(np.dot(rs, rf) / denom)))


def daily_ic_series(
    merged: pd.DataFrame, *, min_cross_section: int = MIN_CROSS_SECTION
) -> pd.Series:
    """One cross-sectional Spearman IC per trading day (M1 remediation).

    ``merged`` columns: ``ts, score, fwd_ret`` (symbol optional). Dates with
    fewer than ``min_cross_section`` finite pairs, or a constant side, are
    SKIPPED — never recorded as a fabricated 0.0. The result is the IC time
    series whose mean and HAC-t carry the panel-level significance claim.
    """
    if merged.empty:
        return pd.Series(dtype=float)
    ts = pd.to_datetime(merged["ts"]).dt.normalize()
    out: Dict[pd.Timestamp, float] = {}
    for day, group in merged.groupby(ts, sort=True):
        rho = _spearman_rho(
            group["score"].to_numpy(dtype=float),
            group["fwd_ret"].to_numpy(dtype=float),
            min_n=min_cross_section,
        )
        if rho is not None:
            out[day] = rho
    return pd.Series(out, dtype=float).sort_index()


def hac_mean_test(values: Sequence[float], *, lag: int) -> HACTestResult:
    """HAC / Newey-West (Bartlett kernel) t-test of H0: mean == 0.

    ``t = mean / sqrt(S / T)`` with ``S = gamma_0 + 2 * sum_{l=1..L}
    (1 - l/(L+1)) * gamma_l`` (autocovariances at ddof=0). ``lag`` should be
    ~ the forward-label horizon (overlapping 5d labels induce MA(4) serial
    correlation in the daily IC series — adversarial review M1). ``lag=0``
    collapses to the plain iid t. Degenerate inputs (n < 2) resolve to
    ``t=0, p=1``; a zero-variance nonzero-mean series caps at ``t=1e12``.
    """
    v = np.asarray(values, dtype=float)
    v = v[np.isfinite(v)]
    n = int(v.size)
    if n < 2:
        return HACTestResult(
            mean=float(v.mean()) if n else 0.0,
            t_stat=0.0,
            p_value=1.0,
            n=n,
            lag=int(lag),
        )
    mean = float(v.mean())
    e = v - mean
    gamma0 = float(np.dot(e, e)) / n
    lag_eff = max(0, min(int(lag), n - 1))
    s_var = gamma0
    for lg in range(1, lag_eff + 1):
        gamma_l = float(np.dot(e[lg:], e[:-lg])) / n
        s_var += 2.0 * (1.0 - lg / (lag_eff + 1.0)) * gamma_l
    if s_var <= 0.0:
        if mean == 0.0:
            return HACTestResult(mean=0.0, t_stat=0.0, p_value=1.0, n=n, lag=int(lag))
        return HACTestResult(
            mean=mean, t_stat=math.copysign(1e12, mean), p_value=0.0, n=n, lag=int(lag)
        )
    t_stat = mean / math.sqrt(s_var / n)
    p_value = 2.0 * _normal_sf(abs(t_stat))
    return HACTestResult(
        mean=mean, t_stat=float(t_stat), p_value=float(p_value), n=n, lag=int(lag)
    )


def daily_ic_significance(
    merged: pd.DataFrame,
    *,
    lag: int,
    min_cross_section: int = MIN_CROSS_SECTION,
) -> DailyICResult:
    """Panel-level IC significance the M1-correct way.

    Per-date cross-sectional Spearman ICs -> one IC time series -> HAC /
    Newey-West t (lag ~ label horizon) on that series. This is robust to both
    defects of the pooled-iid t (overlapping labels: serial correlation;
    many symbols per day: cross-sectional correlation — the effective N is
    the number of DAYS, not the number of panel rows).

    When fewer than 3 daily ICs exist (no usable cross-section, e.g. a
    single-symbol panel) the pooled Spearman IC + iid t is returned as an
    explicit, labelled ``pooled-iid-fallback`` — visible in the artifact,
    never a silent downgrade.
    """
    series = daily_ic_series(merged, min_cross_section=min_cross_section)
    if len(series) >= 3:
        hac = hac_mean_test(series.to_numpy(), lag=lag)
        return DailyICResult(
            ic_mean=hac.mean,
            t_stat=hac.t_stat,
            p_value=hac.p_value,
            n_days=hac.n,
            hac_lag=int(lag),
            method="per-date-cs-hac",
        )
    pooled = spearman_ic(
        merged["score"].to_numpy(dtype=float), merged["fwd_ret"].to_numpy(dtype=float)
    )
    return DailyICResult(
        ic_mean=pooled.ic,
        t_stat=pooled.t_stat,
        p_value=pooled.p_value,
        n_days=int(len(series)),
        hac_lag=int(lag),
        method="pooled-iid-fallback",
    )


def fold_ics(
    merged: pd.DataFrame,
    *,
    n_splits: int = 5,
    label_horizon: int = 5,
    embargo: float = 0.0,
    min_cross_section: int = MIN_CROSS_SECTION,
) -> List[float]:
    """IC per purged walk-forward TEST fold, cut over TRADING DAYS (M4).

    First real consumer of the dormant leakage-safe harness
    (core/ml/validation/harness.py:180-238, plan §5.2). The folds are built
    over the panel's UNIQUE trading days — never over raw panel rows — so
    ``label_horizon`` and ``embargo`` are true trading-day units and a fold
    boundary can never cut a day's cross-section in half (adversarial review
    M4). Purge + embargo clean the harness' train windows per contract; the
    reported fold IC is the mean per-date cross-sectional IC of the test-day
    block (pooled fallback when the block has no usable cross-section).
    Folds without any computable IC are omitted, never fabricated as 0.0.
    """
    if merged.empty:
        return []
    ts = pd.to_datetime(merged["ts"]).dt.normalize()
    days = np.array(sorted(ts.unique()))
    n_days = int(days.size)
    if n_days < n_splits + 1:
        return []
    ics: List[float] = []
    for fold in purged_walk_forward(
        n_days, n_splits=n_splits, embargo=embargo, label_horizon=label_horizon
    ):
        test_days = set(days[fold.test_idx])
        block = merged[ts.isin(test_days)]
        series = daily_ic_series(block, min_cross_section=min_cross_section)
        if len(series) > 0:
            ics.append(float(series.mean()))
            continue
        pooled = spearman_ic(
            block["score"].to_numpy(dtype=float),
            block["fwd_ret"].to_numpy(dtype=float),
        )
        if pooled.n >= 3:
            ics.append(pooled.ic)
    return ics


# ---------------------------------------------------------------------------
# R2 — hit rate (thresholds imported, not duplicated — ADR-SEC-01)
# ---------------------------------------------------------------------------


def hit_rate(
    scores: Sequence[float],
    fwd_ret: Sequence[float],
    buy_thr: float = SIGNAL_BUY_THRESHOLD,
    sell_thr: float = SIGNAL_SELL_THRESHOLD,
) -> HitRateResult:
    """Fraction of directional votes on the right side of the forward return.

    Only votes in BUY (> ``buy_thr``) or SELL (< ``sell_thr``) territory count
    as signals; HOLD-zone scores express no direction and are not graded.
    A BUY hits when the forward return is > 0, a SELL when it is < 0.
    No directional votes at all -> ``rate = NaN`` (undefined, not 0).
    """
    s = np.asarray(scores, dtype=float)
    f = np.asarray(fwd_ret, dtype=float)
    mask = np.isfinite(s) & np.isfinite(f)
    s, f = s[mask], f[mask]

    buys = s > buy_thr
    sells = s < sell_thr
    n_signals = int(buys.sum() + sells.sum())
    if n_signals == 0:
        return HitRateResult(rate=float("nan"), n_signals=0, n_hits=0)
    n_hits = int((buys & (f > 0)).sum() + (sells & (f < 0)).sum())
    return HitRateResult(rate=n_hits / n_signals, n_signals=n_signals, n_hits=n_hits)


# ---------------------------------------------------------------------------
# R3 — consensus replication (calls the REAL engine) + positions
# ---------------------------------------------------------------------------

_ENGINE = ConsensusEngine()


def replicate_consensus(votes: List[VoteResult]) -> float:
    """Consensus score for one (symbol, bar) — the REAL ``aggregate``.

    Defensive copies are passed in because ``aggregate`` mutates vote weights
    in place (Momentum down-weight under a bearish regime,
    consensus.py:179-188); callers' vote objects must stay pristine so a
    leave-one-out re-run sees unmodified weights.
    """
    copies = [
        VoteResult(
            agent_name=v.agent_name,
            symbol=v.symbol,
            score=v.score,
            weight=v.weight,
            reasoning=v.reasoning,
            vetoed=v.vetoed,
        )
        for v in votes
    ]
    return _ENGINE.aggregate(copies)


def _has_directional_vote(votes: List[VoteResult]) -> bool:
    """True when at least one non-vetoed, weighted, non-conditioner vote exists.

    Guard against the ``aggregate`` artifact of returning 0.0 on an empty
    active set: a consensus of nobody is NO-TRADE, never a SELL conviction.
    Mirrors the active-vote filter in consensus.py:190-195.
    """
    return any(
        (not v.vetoed)
        and v.weight > 0.0
        and v.agent_name not in ("RegimeDetectionAgent", "DrawdownGuardAgent")
        for v in votes
    )


def consensus_positions(
    votes_df: pd.DataFrame, exclude_agent: Optional[str] = None
) -> pd.DataFrame:
    """Replicated production decision per (ts, symbol) -> position in {-1, 0, +1}.

    Pipeline per bar (mirroring runner.py):
      1. build ``VoteResult`` objects (minus ``exclude_agent`` for LOO),
      2. ``ConsensusEngine.aggregate`` (the real engine — conditioner
         exclusions and regime coupling included),
      3. direction-aware agent veto (runner.py:176-193): any vetoed vote
         blocks the decision unless the consensus is a SELL,
      4. score -> position: > BUY_THRESHOLD -> +1, < SELL_THRESHOLD -> -1,
         else 0. No active directional votes -> 0 (no-trade, see
         ``_has_directional_vote``).
    """
    rows = []
    for (ts, symbol), group in votes_df.groupby(["ts", "symbol"], sort=True):
        votes = [
            VoteResult(
                agent_name=str(r.agent),
                symbol=str(symbol),
                score=float(r.score),
                weight=float(r.weight),
                reasoning="",
                vetoed=bool(r.vetoed),
            )
            for r in group.itertuples(index=False)
            if str(r.agent) != exclude_agent
        ]
        score = replicate_consensus(votes)

        if not _has_directional_vote(votes):
            position = 0
        else:
            vetoed = any(v.vetoed for v in votes)
            if vetoed and score >= SIGNAL_SELL_THRESHOLD:
                # runner.py:187 — veto overrides everything except a SELL
                position = 0
            elif score > SIGNAL_BUY_THRESHOLD:
                position = 1
            elif score < SIGNAL_SELL_THRESHOLD:
                position = -1
            else:
                position = 0
        rows.append((ts, symbol, score, position))
    return pd.DataFrame(rows, columns=["ts", "symbol", "consensus_score", "position"])


# ---------------------------------------------------------------------------
# R5 — net-of-cost portfolio Sharpe
# ---------------------------------------------------------------------------


def portfolio_sharpe(
    positions_df: pd.DataFrame,
    fwd_df: pd.DataFrame,
    cost_bps: float = 10.0,
) -> SharpeResult:
    """Annualised Sharpe of the equal-weight consensus book, net of costs.

    Convention (documented in the gate artifact so RTR-1..4 reuse the SAME
    target): daily rebalance to the replicated position; per-symbol daily
    P&L = position * 1-day forward return; transaction cost = ``cost_bps``
    (one-way, #1969 convention: 10 bps) on every unit of position change
    (|pos_t - pos_{t-1}|, entry from flat = 1 unit). Portfolio return =
    mean across symbols in the book that day. Sharpe = mean/std * sqrt(252);
    a flat or zero-variance book resolves to 0.0, never a division crash.
    """
    merged = positions_df.merge(fwd_df, on=["ts", "symbol"], how="inner").sort_values(
        ["symbol", "ts"]
    )
    if merged.empty:
        return SharpeResult(0.0, 0.0, 0.0, 0, 0.0)

    cost_rate = float(cost_bps) / 10_000.0
    merged["turnover"] = (
        merged.groupby("symbol")["position"].diff().fillna(merged["position"]).abs()
    )
    merged["net_ret"] = (
        merged["position"] * merged["fwd_ret"] - merged["turnover"] * cost_rate
    )

    daily = merged.groupby("ts")["net_ret"].mean()
    total_cost = float((merged["turnover"] * cost_rate).sum())
    n_days = int(daily.size)
    if n_days < 2:
        return SharpeResult(
            0.0, float(daily.mean() if n_days else 0.0), 0.0, n_days, total_cost
        )

    mean_ret = float(daily.mean())
    vol = float(daily.std(ddof=1))
    if vol == 0.0 or not np.isfinite(vol):
        return SharpeResult(0.0, mean_ret, 0.0, n_days, total_cost)
    sharpe = mean_ret / vol * math.sqrt(_TRADING_DAYS_PER_YEAR)
    return SharpeResult(float(sharpe), mean_ret, vol, n_days, total_cost)


def loo_consensus_sharpe(
    votes_df: pd.DataFrame,
    fwd_df: pd.DataFrame,
    cost_bps: float = 10.0,
) -> Dict[str, LOOResult]:
    """Leave-one-out marginal Sharpe contribution per agent.

    ``delta_sharpe = sharpe_all - sharpe_without_agent`` — positive means the
    agent LIFTS the net-of-cost consensus, negative means it HARMS it.
    Conditioners (DrawdownGuard / RegimeDetection) are assessed through their
    veto / coupling path automatically, because removal strips exactly that
    influence from the replicated pipeline (plan Gherkin §7).
    """
    base = portfolio_sharpe(consensus_positions(votes_df), fwd_df, cost_bps)
    out: Dict[str, LOOResult] = {}
    for agent in sorted(votes_df["agent"].unique()):
        without = portfolio_sharpe(
            consensus_positions(votes_df, exclude_agent=agent), fwd_df, cost_bps
        )
        out[agent] = LOOResult(
            agent=agent,
            sharpe_all=base.sharpe,
            sharpe_without=without.sharpe,
            delta_sharpe=base.sharpe - without.sharpe,
        )
    return out


# ---------------------------------------------------------------------------
# R4 — vote correlation matrix (redundancy, momentum vs regime)
# ---------------------------------------------------------------------------


def opinion_mask(votes_df: pd.DataFrame) -> pd.Series:
    """True where a vote row is a real OPINION, not an abstention.

    The gremium-wide abstention contract is ``score 0.5 at weight 0.0``
    (consensus.py:126-137). Everything else is an opinion — INCLUDING real
    scores at weight 0 from DORMANT agents (default_weight 0.0: Pattern /
    Fundamentals / Valuation). Those cannot move the consensus, but their
    standalone signal quality is exactly what validate-before-activate must
    measure; filtering on ``weight > 0`` would blind the gate to the very
    agents it exists to validate.

    Known contract ambiguity, documented: a dormant agent's GENUINE neutral
    verdict (score exactly 0.5 at weight 0) is indistinguishable from an
    abstention and is dropped with it — the measured sample skews toward its
    directional bars.
    """
    return ~((votes_df["weight"] == 0.0) & (votes_df["score"] == 0.5))


def vote_correlation_matrix(votes_df: pd.DataFrame) -> pd.DataFrame:
    """Pairwise Spearman correlation of agent score series.

    Abstentions (score 0.5 at weight 0, consensus.py:126-137) are the ABSENCE
    of an opinion, not a neutral one — they are dropped before correlating,
    and an agent that only ever abstains does not appear in the matrix at
    all. Dormant weight-0 agents with real scores stay in (``opinion_mask``).
    """
    opinions = votes_df[opinion_mask(votes_df)]
    wide = opinions.pivot_table(
        index=["ts", "symbol"], columns="agent", values="score", aggfunc="first"
    )
    return wide.corr(method="spearman")


# ---------------------------------------------------------------------------
# FDR — Benjamini-Hochberg (plan §12.5)
# ---------------------------------------------------------------------------


def benjamini_hochberg(p_values: Sequence[float], alpha: float = 0.10) -> List[bool]:
    """Benjamini-Hochberg FDR: which p-values survive at level ``alpha``.

    11 agents x several metrics is a multiple-testing problem — the LSTM rank
    ADR (agents.py:571-577) documents exactly this failure mode ("0/488
    survive FDR"). No IC may be called significant before this step.
    """
    m = len(p_values)
    if m == 0:
        return []
    order = np.argsort(np.asarray(p_values, dtype=float))
    significant = [False] * m
    max_k = -1
    for rank_pos, idx in enumerate(order, start=1):
        if p_values[idx] <= alpha * rank_pos / m:
            max_k = rank_pos
    for rank_pos, idx in enumerate(order, start=1):
        if rank_pos <= max_k:
            significant[idx] = True
    return significant


# ---------------------------------------------------------------------------
# Truth table — prove-it-or-kill-it verdicts
# ---------------------------------------------------------------------------


def classify_agent(
    delta_sharpe: Optional[float],
    ic_significant_after_fdr: bool,
    ic: Optional[float] = None,
    noise_band: float = DELTA_SHARPE_NOISE_BAND,
) -> str:
    """HEBT | INVERTED-IC | RAUSCHEN | SCHADET | EXCLUDED (plan Gherkin §7 + M3).

    HEBT        — positive LOO delta beyond the noise band AND an IC that
                  survives FDR with a POSITIVE sign (a lift with no
                  significant signal is luck).
    INVERTED-IC — sign conflict (adversarial review M3): the LOO delta lifts,
                  the IC survives FDR, but its sign is NEGATIVE. A
                  significantly anti-predictive score must NEVER auto-'keep';
                  the machine-readable verdict is ``invert-candidate`` and
                  requires an explicit gate review.
    SCHADET     — negative LOO delta beyond the noise band.
    RAUSCHEN    — everything else measurable.
    EXCLUDED    — not offline-attributable (delta is None).
    """
    if delta_sharpe is None:
        return "EXCLUDED"
    if delta_sharpe > noise_band and ic_significant_after_fdr:
        if ic is not None and ic < 0:
            return "INVERTED-IC"
        return "HEBT"
    if delta_sharpe < -noise_band:
        return "SCHADET"
    return "RAUSCHEN"


def gate_verdict(classification: str) -> str:
    """Map the truth-table class to the activation-gate verdict.

    ``keep`` / ``fix`` / ``kill`` / ``invert-candidate`` / ``n/a``.
    INVERTED-IC (M3) maps to the machine-readable ``invert-candidate`` — an
    RTR-1..4 PR reading the gate JSON can never mistake a sign conflict for a
    clean 'keep'. EXCLUDED agents get ``n/a — separate evidence required``:
    the offline gate neither clears nor condemns what it cannot measure
    (plan §12.2).
    """
    return {
        "HEBT": "keep",
        "INVERTED-IC": "invert-candidate",
        "RAUSCHEN": "fix",
        "SCHADET": "kill",
        "EXCLUDED": "n/a — separate evidence required",
    }[classification]
