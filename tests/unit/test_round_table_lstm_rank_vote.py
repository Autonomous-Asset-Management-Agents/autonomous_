# tests/unit/test_round_table_lstm_rank_vote.py
# ADR-RT-LSTM-01 — LSTMSignalAgent votes on the CROSS-SECTIONAL RANK instead of the
# raw per-symbol prediction, behind LSTM_VOTE_USES_CROSS_SECTIONAL_RANK.
#
# ⚠ This is the most SUBSTANTIVE trading-behaviour flag in Phase B: LSTMSignalAgent is
# an ACTIVE weight-0.40 voter, so changing what it judges on changes what the bot buys.
# It is therefore dormant by default and these tests exist mainly to PROVE that: with
# the flag OFF the vote is byte-identical to #1969's tanh path.
#
# WHY the swap is the honest direction (our own walk-forward, not a preference):
#   - raw per-symbol prediction: 0/488 symbols survive FDR correction -> noise once you
#     account for having tested 488 of them. That is what the agent votes on TODAY.
#   - cross-sectional rank: IC 0.067, t 20.4 -> the model orders stocks better than it
#     calls them. That is what the report already shows the user.
# "Our anchor says the other one is better" is a reason to TEST the swap, not to ship
# it silently — hence flag-gated, hence walk-forward before activation.
#
# Fully deterministic: no network / LLM / GPU / Redis.

from __future__ import annotations

import importlib.util
import os
from datetime import datetime, timezone

import pytest

import config
from core.report.lstm_panel_store import cross_section_standing, get_store
from core.round_table.agents import _MIN_PANEL_SYMBOLS, LSTMSignalAgent


def _load_config_oss():
    oss_path = os.path.join(os.path.dirname(config.__file__), "config.oss.py")
    spec = importlib.util.spec_from_file_location("config_oss_lstm_rank", oss_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _clean_panel():
    get_store().reset()
    yield
    get_store().reset()


def _panel(ranked):
    """Seed today's cross-section: [(symbol, score), ...].

    Pads with filler names to clear _MIN_PANEL_SYMBOLS (ADR-RT-LSTM-02) so a test
    about SCORING is never silently answered by the breadth guard. Filler scores sit
    strictly below the named ones and are mutually distinct, so they neither reorder
    the names under test nor collapse the panel's dispersion.
    """
    named = list(ranked)
    lo = min(v for _, v in named)
    hi = max(v for _, v in named)
    need = max(0, _MIN_PANEL_SYMBOLS - len(named))
    # strictly BETWEEN the named extremes, so the named bottom stays the panel's
    # bottom and the named top stays its top — padding must not invert what a test
    # is asserting about standing.
    step = (hi - lo) / (need + 1)
    filler = [(f"_PAD{i}", lo + step * (i + 1)) for i in range(need)]
    get_store().record_cycle(datetime.now(timezone.utc).date(), named + filler)


def _raw_panel(ranked):
    """Seed the cross-section VERBATIM — no padding. For the guard's own tests."""
    get_store().record_cycle(datetime.now(timezone.utc).date(), list(ranked))


# =========================================================================== #
# 1. SHIP DEFAULT — round-table-v2 activation flipped this ON, both editions
#    (BORA parity: config.py:1234-1235 & config.oss.py:1139-1140, env default
#    "True"). The rank vote is now the shipped behaviour.
# =========================================================================== #
def test_rank_vote_flag_default_on_enterprise():
    assert config.get_config().LSTM_VOTE_USES_CROSS_SECTIONAL_RANK is True
    assert (
        "LSTM_VOTE_USES_CROSS_SECTIONAL_RANK" in config.RuntimeConfigState.model_fields
    )


def test_rank_vote_flag_default_on_oss():
    oss = _load_config_oss()
    assert oss.LSTM_VOTE_USES_CROSS_SECTIONAL_RANK is True
    assert oss.get_config().LSTM_VOTE_USES_CROSS_SECTIONAL_RANK is True


def test_rank_path_is_lexically_unreachable_with_the_flag_off():
    """Mechanical dormancy proof: the rank branch is only ever entered behind the
    flag, and #1969's tanh scorer survives verbatim as the OFF path."""
    import inspect

    from core.round_table import agents

    src = inspect.getsource(agents.LSTMSignalAgent)
    assert "if config.get_config().LSTM_VOTE_USES_CROSS_SECTIONAL_RANK:" in src
    # the untouched #1969 path is still there, character for character
    assert "0.5 + 0.5 * math.tanh(pred / _LSTM_VOTE_SCALE)" in src
    # ...and _vote_on_rank is CALLED from nowhere else (its own def line is not a
    # call site, hence the filter — the assertion is about reachability)
    call_lines = [
        ln
        for ln in src.splitlines()
        if "_vote_on_rank(" in ln and not ln.strip().startswith("def ")
    ]
    assert len(call_lines) == 1, "the rank scorer must have exactly one call site"


def test_agent_weight_is_unchanged_by_the_swap():
    """The swap changes WHAT the module judges, never how loud it is — a weight
    change would be a second, independent trading change riding along."""
    assert LSTMSignalAgent.default_weight == 0.40
    assert LSTMSignalAgent.min_weight == 0.15
    assert LSTMSignalAgent.max_weight == 1.50


# =========================================================================== #
# 2. The rank scorer itself (called directly — the vote() plumbing is #1969's
#    and is covered by its own tests).
# =========================================================================== #
def test_top_ranked_stock_scores_high_and_bottom_scores_low():
    _panel([("AAA", 0.9), ("BBB", 0.5), ("CCC", 0.1)])
    agent = LSTMSignalAgent()

    top_score, top_w, _ = agent._vote_on_rank("AAA", 0.02, "BUY")
    bottom_score, bottom_w, _ = agent._vote_on_rank("CCC", 0.02, "BUY")

    assert top_score > 0.6 and bottom_score < 0.4
    assert top_w == agent.weight and bottom_w == agent.weight


def test_identical_raw_predictions_vote_differently_by_standing():
    """The POINT of the swap: the same isolated prediction is a strong opinion at the
    top of the universe and a weak one at the bottom. Under the tanh path both would
    score identically — that's the refuted claim."""
    _panel([("AAA", 0.9), ("BBB", 0.5), ("CCC", 0.1)])
    agent = LSTMSignalAgent()

    same_pred = 0.03
    top, _, _ = agent._vote_on_rank("AAA", same_pred, "BUY")
    bottom, _, _ = agent._vote_on_rank("CCC", same_pred, "BUY")
    assert top != bottom

    import math

    from core.round_table.agents import _LSTM_VOTE_SCALE

    tanh_score = 0.5 + 0.5 * math.tanh(same_pred / _LSTM_VOTE_SCALE)
    assert not (
        top == pytest.approx(tanh_score) and bottom == pytest.approx(tanh_score)
    )


def test_score_is_the_percentile_and_stays_in_bounds():
    """score = percentile/100 — a stock beating 90% of the universe votes 0.9. The
    mapping must be exact (not merely monotone) so the vote is explainable."""
    _panel([(f"S{i}", float(i)) for i in range(10)])
    agent = LSTMSignalAgent()

    for sym in [f"S{i}" for i in range(10)]:
        pct, _, _ = cross_section_standing(
            get_store().cross_section_at(datetime.now(timezone.utc).date()), sym
        )
        score, _, _ = agent._vote_on_rank(sym, 0.01, "HOLD")
        assert score == pytest.approx(pct / 100.0)
        assert 0.0 <= score <= 1.0


def test_absent_from_panel_abstains_instead_of_guessing():
    """No panel entry -> no validated basis -> weight 0. A quiet 0.5 at weight 0.40
    would drag every consensus toward neutral while looking like a real opinion."""
    _panel([("AAA", 0.9), ("BBB", 0.5)])
    agent = LSTMSignalAgent()

    score, weight, reasoning = agent._vote_on_rank("ZZZ", 0.05, "BUY")
    assert weight == 0.0
    assert score == 0.5
    assert "EXCLUDED" in reasoning


def test_empty_panel_abstains():
    """Cold start (ranking cycle never ran) must abstain, not crash."""
    agent = LSTMSignalAgent()
    score, weight, reasoning = agent._vote_on_rank("AAA", 0.05, "BUY")
    assert (score, weight) == (0.5, 0.0)
    assert "EXCLUDED" in reasoning


# =========================================================================== #
# 2b. ADR-RT-LSTM-02 — degenerate panels must not become confident bearishness.
#     Both cases below are REAL live-engine states, and both would otherwise
#     produce a maximally bearish 0.0 vote at weight 0.40 out of NO information.
# =========================================================================== #
def test_sole_symbol_panel_abstains_instead_of_voting_maximally_bearish():
    """n=1: the symbol beats nobody -> 0th percentile -> score 0.0. That is not a
    bearish finding, it is an empty panel wearing a number."""
    _raw_panel([("AAA", 0.7)])
    agent = LSTMSignalAgent()

    score, weight, reasoning = agent._vote_on_rank("AAA", 0.05, "BUY")
    assert (score, weight) == (0.5, 0.0), "a sole-member panel must not vote"
    assert "EXCLUDED" in reasoning


def test_thin_panel_abstains():
    """Below the ADR floor the percentile is too coarse to BE the validated signal."""
    _raw_panel([(f"S{i}", float(i)) for i in range(_MIN_PANEL_SYMBOLS - 1)])
    agent = LSTMSignalAgent()

    _, weight, reasoning = agent._vote_on_rank("S5", 0.05, "BUY")
    assert weight == 0.0
    assert "EXCLUDED" in reasoning


def test_collapsed_model_does_not_turn_the_whole_universe_bearish():
    """THE dangerous one. If the model degenerates and emits one identical score for
    every name, NO symbol beats any other -> every symbol lands at the 0th percentile
    at once -> the board would read the ENTIRE universe as 'sell' off a model
    failure. A panel with no dispersion carries no standing; everyone abstains."""
    _raw_panel([(f"S{i}", 0.5) for i in range(_MIN_PANEL_SYMBOLS + 20)])
    agent = LSTMSignalAgent()

    for sym in ["S0", "S7", "S42"]:
        score, weight, reasoning = agent._vote_on_rank(sym, 0.05, "BUY")
        assert (score, weight) == (0.5, 0.0), f"{sym} voted off a collapsed panel"
        assert "EXCLUDED" in reasoning


def test_a_broad_dispersed_panel_still_votes():
    """The guard must not swallow the real case it exists to protect."""
    _raw_panel([(f"S{i}", float(i)) for i in range(_MIN_PANEL_SYMBOLS)])
    agent = LSTMSignalAgent()

    score, weight, _ = agent._vote_on_rank(f"S{_MIN_PANEL_SYMBOLS - 1}", 0.05, "BUY")
    assert weight == LSTMSignalAgent.default_weight
    assert score > 0.9, "top of a full, dispersed panel must vote bullish"


# =========================================================================== #
# 3. Georg's requirement: every module must be able to say WHY.
# =========================================================================== #
def test_reasoning_states_the_standing_and_cites_its_source():
    """The reason must carry the operative fact (rank/percentile), a [src:] tag the
    auditor can trace, and must NOT hide that the raw prediction is refuted."""
    _panel([("AAA", 0.9), ("BBB", 0.5), ("CCC", 0.1)])
    agent = LSTMSignalAgent()

    _, _, reasoning = agent._vote_on_rank("AAA", 0.042, "BUY")
    assert f"rank 1 of {_MIN_PANEL_SYMBOLS}" in reasoning
    assert "percentile" in reasoning
    assert "[src: AI model daily ranking" in reasoning
    # the honesty the anchor demands: the raw pred is reported as context, and
    # explicitly labelled as the thing that does NOT survive FDR
    assert "+0.042" in reasoning
    assert "0/488" in reasoning


def test_reasoning_does_not_claim_the_prediction_drove_the_vote():
    """Audit trail integrity: under the rank path the vote is NOT a function of
    pred, so the reason must not present it as the driver (#1969's wording does)."""
    _panel([("AAA", 0.9), ("BBB", 0.1)])
    agent = LSTMSignalAgent()

    _, _, reasoning = agent._vote_on_rank("AAA", 0.042, "BUY")
    assert "tanh" not in reasoning
    assert "cross-sectional standing" in reasoning


# =========================================================================== #
# 4. The invariant that makes the board trustworthy: the vote and the report
#    read ONE definition of "rank" — they cannot diverge.
# =========================================================================== #
def test_vote_and_report_share_one_rank_definition():
    """If the report says 'rank 3 of 500' the board must have voted on rank 3 of 500.
    Guaranteed structurally: both call cross_section_standing()."""
    import inspect

    from core.report import fact_set
    from core.round_table import agents

    assert "cross_section_standing" in inspect.getsource(fact_set.build_fact_set)
    assert "cross_section_standing" in inspect.getsource(agents.LSTMSignalAgent)


def test_rank_helper_matches_the_percentile_the_report_renders():
    """Behavioural half of the same invariant."""
    _panel([("AAA", 0.9), ("BBB", 0.5), ("CCC", 0.1)])
    xsec = get_store().cross_section_at(datetime.now(timezone.utc).date())

    pct, rank, n = cross_section_standing(xsec, "BBB")
    assert n == _MIN_PANEL_SYMBOLS

    agent = LSTMSignalAgent()
    score, _, reasoning = agent._vote_on_rank("BBB", 0.01, "HOLD")
    assert score == pytest.approx(pct / 100.0)
    assert f"rank {rank} of {n}" in reasoning


def test_cross_section_standing_is_pure_and_absent_symbol_is_none():
    """The shared helper must never invent a standing for a symbol it doesn't have."""
    assert cross_section_standing({}, "AAA") == (None, None, None)
    assert cross_section_standing({"AAA": 1.0}, "ZZZ") == (None, None, None)
    # sole member of the universe: beats nobody -> 0th percentile, rank 1 of 1
    assert cross_section_standing({"AAA": 1.0}, "AAA") == (0.0, 1, 1)


def test_cross_section_standing_tolerates_none_scores_by_abstaining():
    """Defense-in-depth for the now trading-critical helper: it feeds the live board
    vote, not just telemetry, and its contract is 'abstain, never guess / never raise
    into the round table'. A malformed cross-section carrying a ``None`` score must
    degrade to abstention, never a ``TypeError`` on ``s < base``. The live ingest
    (``record_cycle`` float-coerces + skips bad rows) already guarantees floats, so
    this guards the same contract at the consumer boundary too."""
    # a None-scored PEER is not comparable -> excluded from the ranking, the rest stand
    assert cross_section_standing({"AAA": 1.0, "BBB": None}, "AAA") == (0.0, 1, 1)
    # the SYMBOL itself carries no score -> abstain, never guess
    assert cross_section_standing({"AAA": None, "BBB": 1.0}, "AAA") == (
        None,
        None,
        None,
    )
    # nothing comparable at all -> honest gap
    assert cross_section_standing({"AAA": None}, "AAA") == (None, None, None)


# =========================================================================== #
# 5. REACHABILITY — the prerequisite that is not obvious from the flag's name.
#    I shipped this swap while believing the flag alone switched the vote. It does
#    not: the standing comes from a panel that only ONE strategy writes, under a
#    DIFFERENT flag. Flipping this under the default strategy measures nothing.
# =========================================================================== #
def test_the_panel_has_exactly_one_writer_and_it_is_lstm_dynamic():
    """If a second writer ever appears, or this one moves, the flag's prerequisite
    changes and the config comment becomes wrong. Pin it."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    writers = []
    for py in root.rglob("core/**/*.py"):
        if "test" in py.parts:
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if ".record_cycle(" in line:
                writers.append(f"{py.name}:{i}")

    assert all(
        w.startswith("lstm_strategy.py") or w.startswith("runner.py") for w in writers
    ), f"the panel's writers moved — reachability comment is now wrong: {writers}"


def test_default_strategy_is_not_the_panel_writer():
    """The uncomfortable fact, asserted so it cannot be forgotten: the shipped
    default strategy never writes a panel, so LSTM_VOTE_USES_CROSS_SECTIONAL_RANK
    alone leaves the module abstaining on every symbol. Both prerequisites
    (ACTIVE_STRATEGY=LSTMDynamic + REPORT_GENERATOR_V2_ENABLED) are required."""
    assert config.get_config().ACTIVE_STRATEGY == "RLAgent"

    import inspect

    from core.strategies import lstm_strategy

    src = inspect.getsource(lstm_strategy)
    # the writer is inside LSTMDynamic AND behind the report flag
    assert "REPORT_GENERATOR_V2_ENABLED" in src
    assert "record_cycle" in src


def test_the_prerequisite_is_documented_where_the_flag_is_defined():
    """A reachability trap that lives only in someone's head is a trap. It must be
    stated at the switch, because that is where the next person reads."""
    import pathlib

    cfg = (pathlib.Path(config.__file__).parent / "config.py").read_text(
        encoding="utf-8"
    )
    # anchor on the ASSIGNMENT, not the first mention — the flag name is also
    # referenced from the full-universe comment above it
    block = cfg.split("LSTM_VOTE_USES_CROSS_SECTIONAL_RANK: bool = (")[0][-2500:]
    assert "PREREQUISITE" in block
    assert "RLAgent" in block
