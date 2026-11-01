"""Shadow SpecialistAlpha recorder (activation runbook, addition 3 — the #76 measurement output).

Records what the specialist WOULD vote vs the real consensus — recorded, NOT counted. Append-only
JSONL, fail-safe. Mirrors core/round_table/shadow_tft_recorder. Works in a Sim-Day run (offline,
as-of via #2651/#2656) and in live paper (forward shadow); an offline analyzer joins each record
with the realized forward return for the #76 information-coefficient / counterfactual-alpha metric.
"""

import json

from core.round_table.shadow_specialist_recorder import (
    _specialist_vote_from,
    record_shadow_specialist_vote,
)


def test_vote_mapping_recommendation_then_score():
    assert _specialist_vote_from(50, "buy") == "BUY"
    assert _specialist_vote_from(50, "sell") == "SELL"
    assert _specialist_vote_from(50, "hold") == "HOLD"
    # recommendation absent -> score fallback (same 60/40 rule as SpecialistAlphaAgent)
    assert _specialist_vote_from(72, None) == "BUY"
    assert _specialist_vote_from(30, None) == "SELL"
    assert _specialist_vote_from(50, None) == "HOLD"
    assert _specialist_vote_from(None, None) == "HOLD"


def test_record_writes_expected_jsonl(tmp_path):
    p = tmp_path / "sub" / "shadow_specialist_votes.jsonl"
    record_shadow_specialist_vote(
        symbol="AAPL",
        sentiment_score=72.0,
        recommendation="buy",
        escalate=True,
        consensus_score=0.71,
        real_action="BUY",
        chain_path=str(p),
    )
    rec = json.loads(p.read_text(encoding="utf-8").splitlines()[0])
    assert rec["symbol"] == "AAPL"
    assert rec["specialist_vote"] == "BUY"
    assert rec["specialist_recommendation"] == "buy"
    assert rec["specialist_sentiment_score"] == 72.0
    assert rec["specialist_escalate"] is True
    assert rec["real_consensus_score"] == 0.71
    assert rec["real_action"] == "BUY"
    assert rec["agreement"] is True
    assert isinstance(rec["ts"], str) and rec["ts"]


def test_record_never_raises_on_bad_path(tmp_path):
    # chain_path is an existing directory -> open() fails -> must NOT raise (fail-safe).
    record_shadow_specialist_vote(
        symbol="X",
        sentiment_score=50.0,
        recommendation="hold",
        escalate=False,
        consensus_score=0.5,
        real_action=None,
        chain_path=str(tmp_path),
    )
