# tests/unit/test_meta_label_training.py
# #1955 TRD-4 — offline meta-label trainer over the RTR-0 replay harness output.
# TDD Red → Green (synthetic fixtures — the REAL training run over the 2024–2026
# bar panel is a documented Inc-2 prerequisite, NOT faked here; plan §11).
#
# Label definition (López de Prado): binary 1 = net-of-cost profitable, computed
# ONLY on the primary model's BUY support set (consensus_positions == +1), with
# the round-trip cost (2 × one-way cost_bps, #1969 convention) subtracted from
# the forward return.

from __future__ import annotations

import pickle
from types import SimpleNamespace

import pandas as pd
import pytest


def _votes_df(n_days=40, symbols=("AAA", "BBB"), start="2024-01-02", with_agents=True):
    """Synthetic RTR-0 vote panel: MomentumAgent drives a BUY consensus (0.9),
    DrawdownGuard/Regime are the conditioner features. drawdown_score alternates
    high/low per day so the label is learnable from a whitelist feature."""
    days = pd.bdate_range(start, periods=n_days)
    rows = []
    for symbol in symbols:
        for i, ts in enumerate(days):
            dd = 0.9 if i % 2 == 0 else 0.1
            rows.append((ts, symbol, "MomentumAgent", 0.9, 1.0, False))
            if with_agents:
                rows.append((ts, symbol, "DrawdownGuardAgent", dd, 1.0, False))
                rows.append((ts, symbol, "RegimeDetectionAgent", 0.6, 1.0, False))
    return pd.DataFrame(
        rows, columns=["ts", "symbol", "agent", "score", "weight", "vetoed"]
    )


def _fwd_df(votes_df):
    """Forward returns tied to the drawdown feature: dd>0.5 → +5%, else −5%."""
    dd = (
        votes_df[votes_df["agent"] == "DrawdownGuardAgent"]
        .rename(columns={"score": "dd"})[["ts", "symbol", "dd"]]
        .copy()
    )
    dd["fwd_ret"] = dd["dd"].apply(lambda v: 0.05 if v > 0.5 else -0.05)
    return dd[["ts", "symbol", "fwd_ret"]]


# ---------------------------------------------------------------------------
# Label construction
# ---------------------------------------------------------------------------
def test_frame_contains_only_buy_support_set():
    from core.analysis.attribution.meta_label_training import build_meta_label_frame

    votes = _votes_df()
    frame = build_meta_label_frame(votes, _fwd_df(votes), cost_bps=10.0)

    assert not frame.empty
    # Momentum 0.9 at weight 1 → consensus 0.9 (> BUY) on every bar → all rows BUY
    assert (frame["consensus_score"] > 0.65).all()
    assert set(frame.columns) >= {
        "ts",
        "symbol",
        "consensus_score",
        "drawdown_score",
        "regime_score",
        "fwd_ret",
        "net_ret",
        "label",
    }


def test_label_is_net_of_cost_round_trip():
    from core.analysis.attribution.meta_label_training import (
        ROUND_TRIP_COST_LEGS,
        build_meta_label_frame,
    )

    votes = _votes_df()
    frame = build_meta_label_frame(votes, _fwd_df(votes), cost_bps=10.0)

    cost = ROUND_TRIP_COST_LEGS * 10.0 / 10_000.0
    assert ROUND_TRIP_COST_LEGS == 2  # entry + exit, one-way cost each (#1969)
    expected = (frame["fwd_ret"] - cost > 0).astype(int)
    assert (frame["label"] == expected).all()
    # both classes present in the synthetic fixture
    assert set(frame["label"].unique()) == {0, 1}


def test_gross_winner_below_cost_is_labelled_zero():
    """fwd_ret > 0 but <= round-trip cost ⇒ NOT net-profitable ⇒ label 0."""
    from core.analysis.attribution.meta_label_training import build_meta_label_frame

    votes = _votes_df(n_days=4, symbols=("AAA",))
    fwd = _fwd_df(votes)
    fwd["fwd_ret"] = 0.0015  # 15 bps gross < 20 bps round-trip cost
    frame = build_meta_label_frame(votes, fwd, cost_bps=10.0)
    assert (frame["label"] == 0).all()


def test_missing_whitelist_votes_are_dropped_never_neutral_filled():
    from core.analysis.attribution.meta_label_training import build_meta_label_frame

    votes = _votes_df(with_agents=False)  # momentum only — no DG/RD votes
    frame = build_meta_label_frame(votes, _fwd_df(_votes_df()), cost_bps=10.0)
    assert frame.empty  # rows without real whitelist features are DROPPED


# ---------------------------------------------------------------------------
# Trainer — honest refusal + purged split + determinism
# ---------------------------------------------------------------------------
def test_trainer_refuses_thin_label_base():
    from core.analysis.attribution.meta_label_training import (
        build_meta_label_frame,
        train_meta_label_model,
    )

    votes = _votes_df(n_days=10, symbols=("AAA",))
    frame = build_meta_label_frame(votes, _fwd_df(votes), cost_bps=10.0)
    with pytest.raises(ValueError, match="too thin"):
        train_meta_label_model(frame, min_samples=200)


def test_trainer_purged_chronological_split():
    from core.analysis.attribution.meta_label_training import (
        build_meta_label_frame,
        train_meta_label_model,
    )

    votes = _votes_df(n_days=150, symbols=("AAA", "BBB", "CCC"))
    frame = build_meta_label_frame(votes, _fwd_df(votes), cost_bps=10.0)
    result = train_meta_label_model(
        frame, min_samples=100, test_fraction=0.25, embargo_days=5
    )

    train_days = pd.to_datetime(pd.Series(result.train_days))
    test_days = pd.to_datetime(pd.Series(result.test_days))
    assert len(test_days) > 0 and len(train_days) > 0
    # strict chronology + embargo gap: no overlap, gap >= embargo_days trading days
    assert train_days.max() < test_days.min()
    all_days = sorted(pd.to_datetime(frame["ts"]).dt.normalize().unique())
    gap = all_days.index(test_days.min()) - all_days.index(train_days.max()) - 1
    assert gap >= 5
    assert result.n_train + result.n_test < len(frame)  # embargo rows dropped


def test_trainer_is_deterministic():
    from core.analysis.attribution.meta_label_training import (
        build_meta_label_frame,
        train_meta_label_model,
    )

    votes = _votes_df(n_days=150, symbols=("AAA", "BBB", "CCC"))
    frame = build_meta_label_frame(votes, _fwd_df(votes), cost_bps=10.0)
    r1 = train_meta_label_model(frame, min_samples=100, seed=42)
    r2 = train_meta_label_model(frame, min_samples=100, seed=42)

    x = frame[["consensus_score", "drawdown_score", "regime_score"]].to_numpy()[:10]
    p1 = r1.model.predict_proba(x)[:, 1]
    p2 = r2.model.predict_proba(x)[:, 1]
    assert (p1 == p2).all()


def test_trainer_reports_honest_oos_numbers():
    from core.analysis.attribution.meta_label_training import (
        build_meta_label_frame,
        train_meta_label_model,
    )

    votes = _votes_df(n_days=150, symbols=("AAA", "BBB", "CCC"))
    frame = build_meta_label_frame(votes, _fwd_df(votes), cost_bps=10.0)
    result = train_meta_label_model(frame, min_samples=100)

    assert 0.0 <= result.base_rate_test <= 1.0
    # separable synthetic signal → the OOS precision of selected trades must
    # beat the unconditional base rate (sanity, not the Inc-2 gate itself)
    assert result.oos_precision >= result.base_rate_test
    assert result.oos_n_selected > 0


# ---------------------------------------------------------------------------
# Artifact roundtrip — the trained artifact drives the live filter
# ---------------------------------------------------------------------------
def test_artifact_roundtrip_into_meta_label_filter(tmp_path, monkeypatch):
    from core.analysis.attribution.meta_label_training import (
        build_meta_label_frame,
        save_meta_label_artifact,
        train_meta_label_model,
    )
    from core.round_table.meta_label import FEATURE_WHITELIST, MetaLabelFilter

    votes = _votes_df(n_days=150, symbols=("AAA", "BBB", "CCC"))
    frame = build_meta_label_frame(votes, _fwd_df(votes), cost_bps=10.0)
    result = train_meta_label_model(frame, min_samples=100)

    out = tmp_path / "meta_label_model.pkl"
    payload = save_meta_label_artifact(
        result, out, horizon=5, cost_bps=10.0, source="synthetic-unit-fixture"
    )
    assert out.exists()
    assert payload["features"] == list(FEATURE_WHITELIST)
    with open(out, "rb") as fh:
        assert pickle.load(fh)["schema_version"] == 1

    import config

    monkeypatch.setattr(
        config,
        "get_config",
        lambda: SimpleNamespace(
            META_LABEL_FILTER_ENABLED=True,
            META_LABEL_MIN_PROBA=0.55,
            META_LABEL_MODEL_PATH=str(out),
        ),
    )

    def _vote(name, score):
        return SimpleNamespace(
            agent_name=name, score=score, weight=1.0, vetoed=False, reasoning=""
        )

    filt = MetaLabelFilter()
    state = {"symbol": "AAA", "ohlc": {"close": 100.0}}
    # high drawdown_score (0.9) was net-profitable in training → trade
    good = [_vote("DrawdownGuardAgent", 0.9), _vote("RegimeDetectionAgent", 0.6)]
    trade_hi, p_hi, _ = filt.should_trade(state, good, 0.9)
    # low drawdown_score (0.1) was a net loser → no-trade
    bad = [_vote("DrawdownGuardAgent", 0.1), _vote("RegimeDetectionAgent", 0.6)]
    trade_lo, p_lo, reason = filt.should_trade(state, bad, 0.9)

    assert 0.0 <= p_lo < p_hi <= 1.0
    assert trade_hi is True
    assert trade_lo is False
    assert "net-of-cost" in reason
