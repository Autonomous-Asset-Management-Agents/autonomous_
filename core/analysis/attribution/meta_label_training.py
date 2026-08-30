"""
meta_label_training.py — #1955 TRD-4 offline meta-label trainer (RTR-0 consumer).

Builds the López-de-Prado meta-label training set from the RTR-0 replay
harness output and fits the lightweight secondary Trade/No-Trade model that
``core/round_table/meta_label.py`` serves at inference time.

Meta-labeling core principle (AFML Ch. 3.6): the secondary model learns ONLY
on the primary model's support set — here the bars where the replicated
consensus (``consensus_positions``, the REAL ``ConsensusEngine.aggregate`` +
direction-aware veto) resolves to a BUY (+1). The label is binary
net-of-cost profitability: forward return minus the ROUND-TRIP transaction
cost (2 × one-way ``cost_bps``, #1969 convention — entry AND exit both pay).

Feature discipline (plan §12.2, forensics-backed): features come EXCLUSIVELY
from ``core.round_table.meta_label.FEATURE_WHITELIST`` — the intersection of
{harness-available} ∩ {really filled live}. Rows whose whitelist agent votes
are missing are DROPPED, never neutral-filled — a fabricated 0.5 is exactly
the neutral-default leak the whitelist exists to prevent.

OFFLINE ONLY: nothing on the runtime path imports this module (the runtime
filter only unpickles the artifact). Pure finance domain (Airlock §5.7),
no paid APIs — sklearn LogisticRegression, deterministic seed.
"""

from __future__ import annotations

import pickle
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List

import pandas as pd

from core.analysis.attribution.metrics import consensus_positions
from core.round_table.meta_label import (
    _ARTIFACT_SCHEMA_VERSION,
    _DRAWDOWN_AGENT,
    _REGIME_AGENT,
    FEATURE_WHITELIST,
)

# ADR-ML-META-03 (#1955): ROUND_TRIP_COST_LEGS = 2
# Basis: #1969 cost convention (10 bps ONE-WAY). Rationale: a meta-label BUY
# outcome spans entry AND exit over the holding horizon — both legs pay the
# one-way cost, so net-of-cost = fwd_ret − 2 × cost_bps. Charging only one leg
# would systematically over-label marginal BUYs as profitable. Annual review.
ROUND_TRIP_COST_LEGS = 2

# ADR-ML-META-04 (#1955): MIN_TRAIN_SAMPLES = 200
# Rationale: below ~200 BUY-support samples a 3-feature logistic fit is noise
# (plan §12 Cascade-Prevention: "ist die Basis zu dünn → Nein dokumentieren,
# nicht trainieren"). The trainer REFUSES instead of shipping a noise model.
MIN_TRAIN_SAMPLES = 200


@dataclass(frozen=True)
class MetaLabelTrainResult:
    """Fitted model + honest split/OOS bookkeeping for the gate artifact."""

    model: Any
    n_train: int
    n_test: int
    base_rate_train: float
    base_rate_test: float
    oos_precision: float
    oos_n_selected: int
    threshold: float
    train_days: List[pd.Timestamp]
    test_days: List[pd.Timestamp]
    embargo_days: int
    seed: int


def build_meta_label_frame(
    votes_df: pd.DataFrame, fwd_df: pd.DataFrame, *, cost_bps: float = 10.0
) -> pd.DataFrame:
    """Meta-label training frame from an RTR-0 vote panel + forward returns.

    Pipeline:
      1. ``consensus_positions`` (REAL engine + direction-aware veto) → keep
         ONLY position == +1 rows (the primary model's BUY support set).
      2. Join the whitelist conditioner votes (DrawdownGuard / Regime) per
         (ts, symbol); rows with a missing whitelist vote are DROPPED.
      3. Join ``fwd_df`` and label: 1 ⇔ ``fwd_ret − round-trip cost > 0``.

    Columns: ts, symbol, consensus_score, drawdown_score, regime_score,
    fwd_ret, net_ret, label.
    """
    empty = pd.DataFrame(
        columns=[
            "ts",
            "symbol",
            "consensus_score",
            "drawdown_score",
            "regime_score",
            "fwd_ret",
            "net_ret",
            "label",
        ]
    )
    if votes_df.empty:
        return empty

    positions = consensus_positions(votes_df)
    buys = positions[positions["position"] == 1][["ts", "symbol", "consensus_score"]]
    if buys.empty:
        return empty

    wide = votes_df.pivot_table(
        index=["ts", "symbol"], columns="agent", values="score", aggfunc="first"
    ).reset_index()
    for agent, feature in (
        (_DRAWDOWN_AGENT, "drawdown_score"),
        (_REGIME_AGENT, "regime_score"),
    ):
        if agent in wide.columns:
            wide = wide.rename(columns={agent: feature})
        else:
            wide[feature] = float("nan")
    wide = wide[["ts", "symbol", "drawdown_score", "regime_score"]]

    frame = buys.merge(wide, on=["ts", "symbol"], how="left")
    # Plan §12.2: DROP rows with missing whitelist features — never 0.5-fill.
    frame = frame.dropna(subset=["drawdown_score", "regime_score"])
    frame = frame.merge(fwd_df, on=["ts", "symbol"], how="inner")
    if frame.empty:
        return empty

    cost = ROUND_TRIP_COST_LEGS * float(cost_bps) / 10_000.0
    frame["net_ret"] = frame["fwd_ret"] - cost
    frame["label"] = (frame["net_ret"] > 0).astype(int)
    return frame.sort_values(["ts", "symbol"]).reset_index(drop=True)


def train_meta_label_model(
    frame: pd.DataFrame,
    *,
    min_samples: int = MIN_TRAIN_SAMPLES,
    test_fraction: float = 0.25,
    embargo_days: int = 5,
    threshold: float = 0.55,
    seed: int = 42,
) -> MetaLabelTrainResult:
    """Fit the secondary model on a purged chronological split.

    Split (leakage guard, plan §12.3): the LAST ``test_fraction`` of unique
    trading days is the OOS block; ``embargo_days`` trading days immediately
    before it are DROPPED entirely (overlapping forward-return labels straddle
    the boundary — training on them peeks into the test window).

    Raises ``ValueError`` when the label base is too thin or single-class —
    the trainer refuses to fit noise (prove-it-or-kill-it, plan §11 Inc 2).
    """
    n = len(frame)
    if n < min_samples:
        raise ValueError(
            f"meta-label base too thin: {n} BUY-support samples < {min_samples} "
            "required — extend the harness window/panel; do NOT train on noise."
        )

    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    day = pd.to_datetime(frame["ts"]).dt.normalize()
    days = sorted(day.unique())
    n_test_days = max(1, int(len(days) * float(test_fraction)))
    n_train_days = len(days) - n_test_days - int(embargo_days)
    if n_train_days < 1:
        raise ValueError(
            f"meta-label base too thin: {len(days)} trading days cannot fit a "
            f"{n_test_days}-day OOS block plus a {embargo_days}-day embargo."
        )
    train_days = days[:n_train_days]
    test_days = days[len(days) - n_test_days :]

    train = frame[day.isin(train_days)]
    test = frame[day.isin(test_days)]
    y_train = train["label"].to_numpy()
    if len(set(y_train)) < 2:
        raise ValueError(
            "meta-label training block is single-class — nothing separable to "
            "learn; extend the window instead of fitting a constant."
        )

    features = list(FEATURE_WHITELIST)
    model = Pipeline(
        [
            ("scaler", StandardScaler()),
            ("logit", LogisticRegression(max_iter=1000, random_state=seed)),
        ]
    )
    model.fit(train[features].to_numpy(), y_train)

    y_test = test["label"].to_numpy()
    proba = model.predict_proba(test[features].to_numpy())[:, 1]
    selected = proba >= float(threshold)
    oos_n_selected = int(selected.sum())
    oos_precision = float(y_test[selected].mean()) if oos_n_selected else float("nan")

    return MetaLabelTrainResult(
        model=model,
        n_train=int(len(train)),
        n_test=int(len(test)),
        base_rate_train=float(y_train.mean()),
        base_rate_test=float(y_test.mean()) if len(y_test) else float("nan"),
        oos_precision=oos_precision,
        oos_n_selected=oos_n_selected,
        threshold=float(threshold),
        train_days=list(train_days),
        test_days=list(test_days),
        embargo_days=int(embargo_days),
        seed=int(seed),
    )


def save_meta_label_artifact(
    result: MetaLabelTrainResult,
    path: str | Path,
    *,
    horizon: int,
    cost_bps: float,
    source: str = "rtr0-replay-harness",
) -> dict:
    """Write the runtime artifact ``MetaLabelFilter`` loads (schema_version 1).

    The feature list is pinned to ``FEATURE_WHITELIST`` — the runtime loader
    rejects any artifact whose features differ (schema drift ⇒ fail-open).
    """
    payload = {
        "schema_version": _ARTIFACT_SCHEMA_VERSION,
        "model": result.model,
        "features": list(FEATURE_WHITELIST),
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "label": {
            "definition": "fwd_ret - ROUND_TRIP_COST_LEGS * cost_bps > 0",
            "horizon_bars": int(horizon),
            "cost_bps_one_way": float(cost_bps),
            "cost_legs": ROUND_TRIP_COST_LEGS,
        },
        "training": {
            "n_train": result.n_train,
            "n_test": result.n_test,
            "base_rate_train": result.base_rate_train,
            "base_rate_test": result.base_rate_test,
            "oos_precision_at_threshold": result.oos_precision,
            "oos_n_selected": result.oos_n_selected,
            "threshold": result.threshold,
            "embargo_days": result.embargo_days,
            "seed": result.seed,
        },
    }
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "wb") as fh:
        pickle.dump(payload, fh)
    return payload
