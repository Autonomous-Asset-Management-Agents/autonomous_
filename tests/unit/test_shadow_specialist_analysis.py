"""Offline analyzer for the #76 shadow-specialist measurement (activation runbook).

Joins the recorded `shadow_specialist_votes.jsonl` with the *same* forward-return target the
attribution gate uses (`core.analysis.attribution.replay.forward_returns`, look-ahead-safe) and
computes the decision metrics: rank information coefficient, directional hit-rate, the BUY-minus-
SELL long/short spread, and a net-of-cost estimate. Pure + deterministic (no provider needed here).
"""

import json

import pandas as pd

from core.analysis.shadow_specialist import analyze_shadow_specialist, load_votes


def _fwd(rows):
    return pd.DataFrame(rows, columns=["ts", "symbol", "fwd_ret"])


def _vote(symbol, ts, score, vote):
    return {
        "symbol": symbol,
        "ts": ts,
        "specialist_sentiment_score": score,
        "specialist_vote": vote,
    }


def test_analyze_joins_and_computes_the_gate_metrics():
    votes = [
        _vote("AAPL", "2026-01-05T15:30:00+00:00", 80, "BUY"),
        _vote("MSFT", "2026-01-05T15:30:00+00:00", 70, "BUY"),
        _vote("TSLA", "2026-01-05T15:30:00+00:00", 20, "SELL"),
        _vote("NVDA", "2026-01-05T15:30:00+00:00", 30, "SELL"),
        _vote("AMZN", "2026-01-06T15:30:00+00:00", 50, "HOLD"),
        _vote(
            "GOOG", "2026-01-07T15:30:00+00:00", 90, "BUY"
        ),  # no matching bar -> unmatched
    ]
    fwd = _fwd(
        [
            (pd.Timestamp("2026-01-05"), "AAPL", 0.03),
            (pd.Timestamp("2026-01-05"), "MSFT", 0.01),
            (pd.Timestamp("2026-01-05"), "TSLA", -0.02),
            (pd.Timestamp("2026-01-05"), "NVDA", 0.01),
            (pd.Timestamp("2026-01-06"), "AMZN", 0.00),
        ]
    )

    r = analyze_shadow_specialist(votes, fwd, cost_bps=10.0)

    assert r["n_total"] == 6
    assert r["n_matched"] == 5
    assert r["coverage"] == 5 / 6
    # score ranks with forward return -> positive rank IC
    assert r["information_coefficient"] > 0
    # BUY(+), BUY(+), SELL(-) are hits; SELL(+) is a miss -> 3/4
    assert r["n_directional"] == 4
    assert r["hit_rate"] == 0.75
    # BUY mean 0.02, SELL mean -0.005 -> spread 0.025 = 250 bps; net of 10 bps cost -> 240
    assert round(r["long_short_spread_bps"], 6) == 250.0
    assert round(r["net_long_short_bps"], 6) == 240.0
    assert round(r["mean_fwd_by_vote"]["BUY"], 6) == 0.02
    assert round(r["mean_fwd_by_vote"]["SELL"], 6) == -0.005


def test_analyze_no_matches_is_graceful():
    votes = [_vote("AAPL", "2026-01-05T00:00:00+00:00", 80, "BUY")]
    fwd = _fwd([(pd.Timestamp("2026-02-01"), "AAPL", 0.03)])
    r = analyze_shadow_specialist(votes, fwd)
    assert r["n_total"] == 1
    assert r["n_matched"] == 0
    assert r["coverage"] == 0.0
    assert r["information_coefficient"] is None
    assert r["hit_rate"] is None
    assert r["long_short_spread_bps"] is None


def test_load_votes_skips_blank_and_malformed(tmp_path):
    p = tmp_path / "shadow_specialist_votes.jsonl"
    good = json.dumps(_vote("AAPL", "2026-01-05T00:00:00+00:00", 80, "BUY"))
    p.write_text(good + "\n\n" + "{not json}\n" + good + "\n", encoding="utf-8")
    rows = load_votes(str(p))
    assert len(rows) == 2
    assert rows[0]["symbol"] == "AAPL"


def test_load_votes_missing_file_returns_empty(tmp_path):
    assert load_votes(str(tmp_path / "nope.jsonl")) == []
