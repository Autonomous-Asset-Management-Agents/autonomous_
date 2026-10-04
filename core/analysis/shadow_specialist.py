"""Offline analyzer for the SpecialistAlpha #76 shadow-measurement (activation runbook, stage 1).

The shadow recorder (`core/round_table/shadow_specialist_recorder.py`) drops one JSONL row per
scan of what the SpecialistAlpha *would* vote — recorded, never counted. This module joins those
rows with the realized forward return and reports the metrics the #76 HITL gate needs to decide
whether the specialist carries net-of-cost alpha *before* it is ever given consensus weight
(validate-before-activate; EU AI Act Art. 14 / BaFin).

Design choices that keep the number honest:

- The forward return is the **exact same target the attribution gate uses** —
  `core.analysis.attribution.replay.forward_returns` (``close[t+h]/close[t]-1``, look-ahead-safe:
  windows running past the end of the corpus are dropped, never padded). We do not re-derive it.
- The core `analyze_shadow_specialist` is **pure** (votes list + forward-return frame in, metrics
  dict out) — no provider, no clock, no I/O — so it is fully deterministic and unit-testable.
- The BUY-minus-SELL long/short spread minus a round-trip cost is a **proxy** for tradeable alpha,
  not a re-run of consensus with the specialist weighted in. A full counterfactual needs a Sim-Day
  re-run; this is the cheap, transparent first read the gate asks for.

CLI: ``python -m core.analysis.shadow_specialist --votes <jsonl> --corpus <dir> [--horizon 5]``.
"""

from __future__ import annotations

import argparse
import json
import logging
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

_BUY = "BUY"
_SELL = "SELL"


def load_votes(path: str) -> List[Dict[str, Any]]:
    """Read the append-only shadow-vote JSONL; skip blank/malformed lines (fail-safe)."""
    rows: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except (ValueError, TypeError):
                    logger.warning(
                        "shadow-specialist analyzer: skipping malformed JSONL line"
                    )
    except OSError:
        return []
    return rows


def _fwd_lookup(fwd_returns: pd.DataFrame) -> Dict[tuple, float]:
    """{(SYMBOL, bar-date): fwd_ret} — bar date normalized to midnight, tz-stripped."""
    lookup: Dict[tuple, float] = {}
    if fwd_returns is None or fwd_returns.empty:
        return lookup
    ts = pd.to_datetime(fwd_returns["ts"])
    try:
        ts = ts.dt.tz_localize(None)
    except (TypeError, AttributeError):
        pass  # already tz-naive
    dates = ts.dt.normalize()
    for date, symbol, ret in zip(dates, fwd_returns["symbol"], fwd_returns["fwd_ret"]):
        lookup[(str(symbol).upper(), date)] = float(ret)
    return lookup


def _vote_date(ts_value: Any) -> Optional[pd.Timestamp]:
    try:
        ts = pd.to_datetime(ts_value)
    except (ValueError, TypeError):
        return None
    if ts is None or pd.isna(ts):
        return None
    if getattr(ts, "tz", None) is not None:
        ts = ts.tz_localize(None) if ts.tzinfo is None else ts.tz_convert(None)
    return ts.normalize()


def _spearman(a: List[float], b: List[float]) -> Optional[float]:
    if len(a) < 2:
        return None
    sa, sb = pd.Series(a), pd.Series(b)
    if sa.nunique() < 2 or sb.nunique() < 2:
        return None
    ic = sa.corr(sb, method="spearman")
    return None if pd.isna(ic) else float(ic)


def analyze_shadow_specialist(
    votes: List[Dict[str, Any]],
    fwd_returns: pd.DataFrame,
    *,
    cost_bps: float = 10.0,
) -> Dict[str, Any]:
    """Join recorded shadow votes with realized forward returns → the #76 decision metrics.

    ``votes``: rows from ``shadow_specialist_votes.jsonl`` (needs ``symbol``, ``ts``,
    ``specialist_sentiment_score``, ``specialist_vote``). ``fwd_returns``: the frame from
    ``forward_returns(provider, horizon)`` (columns ``ts``, ``symbol``, ``fwd_ret``).
    ``cost_bps``: assumed round-trip cost for the net-of-cost spread.
    """
    lookup = _fwd_lookup(fwd_returns)

    scores: List[float] = []
    rets: List[float] = []
    directional = 0
    hits = 0
    bucket: Dict[str, List[float]] = {_BUY: [], _SELL: [], "HOLD": []}

    for v in votes:
        date = _vote_date(v.get("ts"))
        symbol = str(v.get("symbol", "")).upper()
        if date is None or (symbol, date) not in lookup:
            continue
        ret = lookup[(symbol, date)]
        score = v.get("specialist_sentiment_score")
        vote = str(v.get("specialist_vote", "")).upper()
        if score is not None:
            scores.append(float(score))
            rets.append(ret)
        if vote in bucket:
            bucket[vote].append(ret)
        if vote in (_BUY, _SELL):
            directional += 1
            if (vote == _BUY and ret > 0) or (vote == _SELL and ret < 0):
                hits += 1

    n_total = len(votes)
    n_matched = len(scores)

    def _mean(xs: List[float]) -> Optional[float]:
        return float(sum(xs) / len(xs)) if xs else None

    mean_by_vote = {k: _mean(xs) for k, xs in bucket.items()}
    buy_m, sell_m = mean_by_vote[_BUY], mean_by_vote[_SELL]
    if buy_m is None or sell_m is None:
        spread_bps: Optional[float] = None
        net_bps: Optional[float] = None
    else:
        spread_bps = (buy_m - sell_m) * 1e4
        net_bps = spread_bps - cost_bps

    return {
        "n_total": n_total,
        "n_matched": n_matched,
        "coverage": (n_matched / n_total) if n_total else 0.0,
        "information_coefficient": _spearman(scores, rets),
        "n_directional": directional,
        "hit_rate": (hits / directional) if directional else None,
        "mean_fwd_by_vote": mean_by_vote,
        "long_short_spread_bps": spread_bps,
        "net_long_short_bps": net_bps,
        "cost_bps": cost_bps,
    }


def run_from_corpus(
    votes_path: str,
    corpus_dir: str,
    *,
    horizon: int = 5,
    cost_bps: float = 10.0,
) -> Dict[str, Any]:
    """Wire the real corpus: ReplayBarsProvider -> forward_returns -> analyze. Thin, untested seam."""
    from core.analysis.attribution.replay import ReplayBarsProvider, forward_returns

    votes = load_votes(votes_path)
    provider = ReplayBarsProvider(corpus_dir)
    fwd = forward_returns(provider, horizon=horizon)
    report = analyze_shadow_specialist(votes, fwd, cost_bps=cost_bps)
    report["horizon"] = horizon
    report["n_bar_symbols"] = len(provider.symbols)
    return report


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="SpecialistAlpha #76 shadow-vote analyzer")
    ap.add_argument(
        "--votes", required=True, help="path to shadow_specialist_votes.jsonl"
    )
    ap.add_argument(
        "--corpus", required=True, help="Sim-Day bars corpus dir (ReplayBarsProvider)"
    )
    ap.add_argument(
        "--horizon", type=int, default=5, help="forward-return horizon in bars"
    )
    ap.add_argument(
        "--cost-bps", type=float, default=10.0, help="assumed round-trip cost (bps)"
    )
    args = ap.parse_args(argv)

    report = run_from_corpus(
        args.votes, args.corpus, horizon=args.horizon, cost_bps=args.cost_bps
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
