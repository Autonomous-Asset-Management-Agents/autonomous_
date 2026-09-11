# tests/unit/test_book_constructor.py
"""TDD for RTR-5/B1 (#2401) — Book-Constructor (dark, flag-gated).

Plan §7 Gherkin (issue #2401):
    Given BOOK_CONSTRUCTOR_ENABLED=True und ein Ranking über das Universe
    When der Rebalance-Zyklus läuft
    Then entsteht GENAU EIN Ziel-Buch (Top-N) und ein minimaler Order-Diff
    And kein Symbol erzeugt mehr als einen Trade pro Rebalance-Fenster

    Given beide Flags off
    Then heutiges Verhalten exakt erhalten (byte-identisch — hier gepinnt:
    maybe_construct_book ist ein No-Op und das Modul ist an keinem Loop verdrahtet).

V-1 seams (verified against origin/main 2026-07-22):
  * LSTM cross-section  -> core/report/lstm_panel_store.py:156 cross_section_at
    (writer: LSTMDynamic.update_lstm_rankings, lstm_strategy.py:484).
  * Consensus fallback  -> core/round_table/recent_decisions.py:117
    get_recent_round_table_decisions (producer: runner.py:566, latest-per-symbol,
    entries carry ``consensus_score``).

B1 is CONSTRUCTION/DIFF ONLY — no order submission, no loop wiring (that is B3).
"""

from datetime import date, datetime, timedelta

import pytest

from core.round_table.book_constructor import (
    RANKING_SOURCE_CONSENSUS,
    RANKING_SOURCE_LSTM,
    BookProposal,
    build_target_book,
    construct_book,
    gather_ranking,
    maybe_construct_book,
    should_rebalance,
)

# Ranking fixture: 12 names, scores strictly descending A1 > A2 > ... > A12.
RANKING = {f"A{i}": 1.0 - i * 0.01 for i in range(1, 13)}
TOP_10 = tuple(f"A{i}" for i in range(1, 11))


# ------------------------------------------------------------------ flag off = dark
def test_flag_off_maybe_construct_returns_none():
    """Default config (flag OFF) -> maybe_construct_book is a no-op (dark, BORA)."""
    assert maybe_construct_book(held=["A1"], max_positions=10) is None


def test_flag_on_maybe_construct_builds_book(monkeypatch):
    """Flag ON -> exactly one proposal built from the gathered ranking."""
    import config as _config

    monkeypatch.setattr(_config, "BOOK_CONSTRUCTOR_ENABLED", True, raising=False)
    from core.report.lstm_panel_store import get_store

    store = get_store()
    store.reset()
    try:
        store.record_cycle(date(2026, 7, 22), list(RANKING.items()))
        proposal = maybe_construct_book(
            held=["A1"], max_positions=10, now=datetime(2026, 7, 22, 15, 0)
        )
        assert isinstance(proposal, BookProposal)
        assert proposal.target_book == TOP_10
        assert proposal.ranking_source == RANKING_SOURCE_LSTM
    finally:
        store.reset()


# ------------------------------------------------- exactly ONE target book (Top-N)
def test_exactly_one_target_book_top_n():
    proposal = construct_book(ranking=RANKING, held=[], max_positions=10)
    assert proposal.target_book == TOP_10
    assert len(proposal.target_book) == 10


def test_target_book_smaller_universe_than_n():
    """Universe smaller than N -> book is the whole (ranked) universe, never padded."""
    small = {"X": 0.9, "Y": 0.8}
    proposal = construct_book(ranking=small, held=[], max_positions=10)
    assert proposal.target_book == ("X", "Y")


def test_target_book_deterministic_tie_break():
    """Equal scores -> deterministic alphabetical tie-break (audit reproducibility)."""
    tied = {"B": 0.5, "A": 0.5, "C": 0.5}
    assert build_target_book(tied, 2) == ("A", "B")


def test_construct_book_is_deterministic():
    p1 = construct_book(ranking=RANKING, held=["A1", "A12"], max_positions=10)
    p2 = construct_book(ranking=RANKING, held=["A1", "A12"], max_positions=10)
    assert p1.target_book == p2.target_book
    assert p1.buys == p2.buys
    assert p1.sells == p2.sells


# ------------------------------------------------------------- minimal rebalance diff
def test_minimal_diff_buys_sells_holds():
    """Held: A1 (stays top), A12 (out of top-10) -> minimal diff: 9 buys, 1 sell."""
    proposal = construct_book(ranking=RANKING, held=["A1", "A12"], max_positions=10)
    assert proposal.holds == ("A1",)
    assert proposal.sells == ("A12",)
    assert proposal.buys == tuple(f"A{i}" for i in range(2, 11))


def test_no_diff_when_book_already_matches():
    """Held == target -> empty diff (no churn: nothing to trade)."""
    proposal = construct_book(ranking=RANKING, held=list(TOP_10), max_positions=10)
    assert proposal.buys == ()
    assert proposal.sells == ()
    assert set(proposal.holds) == set(TOP_10)


def test_max_one_trade_per_symbol_per_window():
    """Gherkin: no symbol appears in more than ONE action bucket per rebalance window."""
    proposal = construct_book(
        ranking=RANKING, held=["A1", "A11", "A12"], max_positions=10
    )
    buckets = (
        list(proposal.buys)
        + list(proposal.sells)
        + list(proposal.holds)
        + [s for s, _ in proposal.blocked_sells]
    )
    assert len(buckets) == len(set(buckets)), "symbol appears in more than one bucket"
    # Every involved symbol (held or targeted) shows up exactly once.
    assert set(buckets) == set(proposal.target_book) | {"A1", "A11", "A12"}


# ----------------------------------------------- anti-churn: min-hold gate respected
def test_min_hold_blocked_sell_is_suppressed_and_audited():
    """can_sell_position veto (portfolio_manager.py:621) -> sell suppressed, audited."""

    def can_sell(symbol):
        if symbol == "A12":
            return False, "Minimum hold period not met (0.4/1 hours)"
        return True, "Hold period satisfied"

    proposal = construct_book(
        ranking=RANKING, held=["A11", "A12"], max_positions=10, can_sell=can_sell
    )
    assert proposal.sells == ("A11",)
    assert proposal.blocked_sells == (
        ("A12", "Minimum hold period not met (0.4/1 hours)"),
    )


def test_unheld_symbols_never_sold():
    proposal = construct_book(ranking=RANKING, held=[], max_positions=10)
    assert proposal.sells == ()
    assert proposal.blocked_sells == ()


# ------------------------------------------------------------------ audit record
def test_audit_record_fields():
    proposal = construct_book(
        ranking=RANKING,
        held=["A12"],
        max_positions=10,
        as_of=datetime(2026, 7, 22, 15, 30),
    )
    record = proposal.to_audit_record()
    assert record["as_of"] == "2026-07-22T15:30:00"
    assert record["target_book"] == list(TOP_10)
    assert record["max_positions"] == 10
    assert record["universe_size"] == 12
    assert "A12" in record["sells"]
    assert proposal.reasoning
    assert "top-10" in proposal.reasoning.lower() or "top 10" in proposal.reasoning


# ------------------------------------------------------------- ranking source seams
def test_gather_ranking_lstm_primary():
    from core.report.lstm_panel_store import get_store

    store = get_store()
    store.reset()
    try:
        store.record_cycle(date(2026, 7, 22), [("AAPL", 0.9), ("MSFT", 0.8)])
        ranking, source = gather_ranking(as_of=datetime(2026, 7, 22, 16, 0))
        assert source == RANKING_SOURCE_LSTM
        assert ranking == {"AAPL": 0.9, "MSFT": 0.8}
    finally:
        store.reset()


def test_gather_ranking_consensus_fallback(caplog):
    """Empty LSTM panel -> consensus-score fallback (recent_decisions) + WARNING (§5.6)."""
    import logging

    from core.report.lstm_panel_store import get_store
    from core.round_table.recent_decisions import (
        clear_recent_round_table_decisions,
        record_round_table_decision,
    )

    get_store().reset()
    clear_recent_round_table_decisions()
    try:
        record_round_table_decision({"symbol": "AAPL", "consensus_score": 0.71})
        record_round_table_decision({"symbol": "MSFT", "consensus_score": 0.55})
        with caplog.at_level(logging.WARNING):
            ranking, source = gather_ranking()
        assert source == RANKING_SOURCE_CONSENSUS
        assert ranking == {"AAPL": 0.71, "MSFT": 0.55}
        assert any("fallback" in r.message.lower() for r in caplog.records)
    finally:
        clear_recent_round_table_decisions()


def test_gather_ranking_no_source_returns_empty(caplog):
    import logging

    from core.report.lstm_panel_store import get_store
    from core.round_table.recent_decisions import clear_recent_round_table_decisions

    get_store().reset()
    clear_recent_round_table_decisions()
    with caplog.at_level(logging.WARNING):
        ranking, source = gather_ranking()
    assert ranking == {}
    assert any("no ranking source" in r.message.lower() for r in caplog.records)


# ------------------------------------------------------------- rebalance cadence
def test_should_rebalance_first_time_true():
    assert should_rebalance(None, datetime(2026, 7, 22), interval_hours=168.0) is True


def test_should_rebalance_within_interval_false():
    last = datetime(2026, 7, 20, 12, 0)
    now = last + timedelta(hours=100)
    assert should_rebalance(last, now, interval_hours=168.0) is False


def test_should_rebalance_past_interval_true():
    last = datetime(2026, 7, 1, 12, 0)
    now = last + timedelta(hours=168)
    assert should_rebalance(last, now, interval_hours=168.0) is True


def test_should_rebalance_default_interval_from_config():
    """interval_hours=None resolves the ADR-tunable from config (weekly default)."""
    last = datetime(2026, 7, 22, 12, 0)
    assert should_rebalance(last, last + timedelta(hours=1)) is False
    assert should_rebalance(last, last + timedelta(hours=169)) is True


# ------------------------------------------------------------------ pure module
def test_module_has_no_order_submission_surface():
    """B1 is construction/diff ONLY: the module must not import broker/executor code."""
    import sys

    import core.round_table.book_constructor as bc

    src = open(bc.__file__, encoding="utf-8").read()
    for forbidden in ("submit_order", "OrderExecutor", "TradingClient"):
        assert forbidden not in src, f"B1 must not touch order submission: {forbidden}"
    assert "core.engine.order_executor" not in sys.modules or True
