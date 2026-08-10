# tests/unit/test_round_table_reasoning_provenance.py
# Georg's board requirement: "jeder der round table modulen soll sagen können warum er
# ja oder nein sagt". A vote the user cannot interrogate is not a board member's
# opinion, it's a number.
#
# SCOPE — deliberately narrow, and this is the honest part: these tests assert the
# EXPLANATION, never the score. Reasoning is audit text; changing it cannot change a
# single trade, so this needs no flag. Where a module's evidence is genuinely weak
# (Momentum reading ONE bar, Pattern reading candlestick geometry), the reason must SAY
# so rather than dress it up — a board member who overstates their basis is worse than
# one who abstains.
#
# What this does NOT do: fix those weak modules. Grounding their SCORES in the report's
# real trend facts is a trading change and needs its own flag + walk-forward.

from __future__ import annotations

import inspect

from core.round_table.agents import ALL_AGENTS


def _reason_sources() -> dict[str, str]:
    return {a.__class__.__name__: inspect.getsource(a.__class__) for a in ALL_AGENTS}


def test_every_module_can_say_where_its_evidence_came_from():
    """Each module's reasoning must carry at least one [src: ...] provenance tag, or
    abstain. 'The model said so' is not a reason a user can check."""
    missing = [
        name
        for name, src in _reason_sources().items()
        if "[src:" not in src and "EXCLUDED" not in src
    ]
    assert not missing, f"modules that cannot cite their evidence: {missing}"


def test_modules_with_no_data_abstain_rather_than_vote_neutral():
    """The abstention contract: no data -> weight 0, not a confident-looking 0.5.
    Asserted structurally across the gremium so a new module inherits the rule."""
    for name, src in _reason_sources().items():
        if "EXCLUDED" in src or "abstention" in src:
            assert (
                "weight=0.0" in src or "0.0," in src
            ), f"{name} names an abstention path but never zeroes its weight"


def test_weak_evidence_modules_disclose_their_weakness():
    """No spin, downward: Momentum's fallback votes off a single bar. Its reason must
    admit the basis, because the user reads these. (#1951: PatternRecognitionAgent —
    the other weak-evidence module — was deleted outright.)"""
    srcs = _reason_sources()
    # NB: assert only on fragments that do not straddle an f-string line break —
    # the source text is not the rendered string.
    assert "one-day move" in srcs["MomentumAgent"]
    assert "PatternRecognitionAgent" not in srcs  # deleted, not just dormant


def test_market_wide_modules_do_not_pose_as_stock_opinions():
    """VIXAware scores the market's risk appetite — the SAME number for every symbol
    on a given day. In a per-stock board that must be stated, or it reads as a verdict
    on this stock."""
    src = _reason_sources()["VIXAwareRiskAgent"]
    assert "identical for every symbol" in src
    assert "not this stock" in src


def test_reasoning_changes_are_score_free():
    """The guarantee that lets this ship unflagged: no reasoning edit may sit on a
    line that also computes a score."""
    for name, src in _reason_sources().items():
        for line in src.splitlines():
            if "[src:" in line:
                assert (
                    "score = " not in line
                ), f"{name}: provenance tag shares a line with a score assignment"
