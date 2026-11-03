"""
test_rtr0_book_quality.py — RTR-0 Inc 2 (#2402) Top-N book-quality metrics.

The book-quality artifact gates the #2388 arm and RTR-5/B3 — a mis-computed
precision@N or a random baseline that flatters the system could wave a
worthless selection mechanism into live activation. Hence every metric is
pinned here BEFORE the implementation exists (TDD Red -> Green), on
deterministic fixtures with a KNOWN ranking, so precision@N and the verdicts
are exact — never "roughly right".

Covered (issue #2402, Option B):
  * Top-N book from a per-date cross-sectional ranking (deterministic ties)
  * forward book returns 4/8/12 weeks, equal-weight, net-of-cost (10 bps
    one-way, harness convention) incl. rebalance turnover
  * baselines — SPY, equal-weight universe, Random-N bootstrap (fixed seed,
    deterministic) -> vs_random_pctile
  * precision@N (share of picks > universe median over the horizon) — exact
    on a constructed ranking
  * honesty — n_effective (overlapping windows), HAC t over the window
    series, verdict="insufficient_universe" below the minimum breadth
    (gates NOTHING — the 10-symbol smoke MUST end this way)
  * dual display — LSTM ranking (primary) vs consensus ranking side-by-side;
    an all-abstaining LSTM surfaces as available=False with a reason

Rev 2 (PR #2405 adversarial review, F1-F4):
  * F1 — NULL CALIBRATION: the random baseline must be persistence-matched
    (static random score per bootstrap iteration through the same Top-N
    pipeline). A zero-skill static ranking may reach pctile >= 95 only at
    the fair ~5% rate (binomial-tolerant, seed-pinned) — the Rev-1
    i.i.d.-per-date null was size-inflated (~33% false activations).
  * F2 — DELISTING: a symbol whose series ends inside the forward window
    stays in the book with its TRUNCATED return (crash burdens the book,
    never silently vanishes); excluded/truncated counts in the artifact.
  * F3 — ties at the Top-N cut are reported (tie_share); a degenerate cut
    (whole book = alphabetical pick from one tie group) skips the window.
  * F4 — ``--book-bootstrap`` argparse floor >= 1000.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.analysis.attribution.book_quality import (
    MIN_UNIVERSE_BREADTH,
    RankingSpec,
    book_quality_payload,
    build_ranking_specs,
    compute_book_quality,
    lstm_panel_from_store,
    parse_book_bootstrap,
    render_book_quality_markdown,
)
from core.report.lstm_panel_store import LstmPanelStore

# ---------------------------------------------------------------------------
# Fixtures — deterministic universes with a KNOWN rank ordering
# ---------------------------------------------------------------------------


def _make_closes(n_symbols: int, n_days: int, drift_step: float = 0.0005):
    """Universe of geometric price paths: symbol i compounds at i*drift_step
    per bar — the true future-return ranking IS the symbol index, exactly."""
    dates = pd.bdate_range("2025-01-06", periods=n_days)
    closes = {}
    for i in range(n_symbols):
        prices = 100.0 * (1.0 + i * drift_step) ** np.arange(n_days, dtype=float)
        closes[f"S{i:03d}"] = pd.Series(prices, index=dates)
    return closes, dates


def _make_panel(symbols, dates, score_of):
    rows = [(d, s, float(score_of(s))) for d in dates for s in symbols]
    return pd.DataFrame(rows, columns=["ts", "symbol", "score"])


def _spec(panel, source="lstm"):
    return RankingSpec(source=source, panel=panel)


def _wide_fixture(score_of, n_symbols=120, n_days=120):
    """>= MIN_UNIVERSE_BREADTH symbols + a SPY series at the median drift."""
    closes, dates = _make_closes(n_symbols, n_days)
    spy = 100.0 * (1.0 + (n_symbols // 2) * 0.0005) ** np.arange(n_days, dtype=float)
    closes["SPY"] = pd.Series(spy, index=dates)
    panel = _make_panel([f"S{i:03d}" for i in range(n_symbols)], dates, score_of)
    return closes, panel


# ---------------------------------------------------------------------------
# Perfect / inverted ranking — exact precision@N, pctile, verdicts
# ---------------------------------------------------------------------------


class TestKnownRanking:
    def test_perfect_ranking_precision_one_and_beats_baselines(self):
        closes, panel = _wide_fixture(score_of=lambda s: int(s[1:]))
        result = compute_book_quality(
            [_spec(panel)],
            closes,
            ns=(5, 10),
            horizons_weeks=(4, 8),
            n_bootstrap=200,
            seed=42,
        )
        src = result.rankings[0]
        assert src.available
        assert src.universe_breadth == 120
        assert len(src.entries) == 4
        for e in src.entries:
            assert e.precision_at_n == pytest.approx(1.0)
            assert e.vs_random_pctile >= 95.0
            assert e.vs_spy is not None and e.vs_spy > 0
            assert e.verdict == "beats_baselines"
        # >= 2 horizons beat baselines for one N -> activation supported
        assert src.verdict == "activation_supported"
        assert result.verdict == "activation_supported"

    def test_inverted_ranking_precision_zero_and_no_edge(self):
        closes, panel = _wide_fixture(score_of=lambda s: -int(s[1:]))
        result = compute_book_quality(
            [_spec(panel)],
            closes,
            ns=(5,),
            horizons_weeks=(4,),
            n_bootstrap=200,
            seed=42,
        )
        entry = result.rankings[0].entries[0]
        assert entry.precision_at_n == pytest.approx(0.0)
        assert entry.vs_random_pctile <= 5.0
        assert entry.verdict == "no_edge"
        assert result.rankings[0].verdict == "no_activation"
        assert result.verdict == "no_activation"

    def test_exact_mid_precision_half(self):
        # 5 symbols, strictly ordered window returns; book = {best, worst}
        # -> exactly one of two picks above the universe median -> 0.5.
        closes, dates = _make_closes(5, 40)
        scores = {"S004": 10.0, "S000": 9.0, "S001": 1.0, "S002": 0.9, "S003": 0.8}
        panel = _make_panel(list(scores), dates, scores.__getitem__)
        result = compute_book_quality(
            [_spec(panel)],
            closes,
            ns=(2,),
            horizons_weeks=(4,),
            n_bootstrap=50,
            seed=0,
        )
        entry = result.rankings[0].entries[0]
        assert entry.precision_at_n == pytest.approx(0.5)
        # 5 symbols is far below the minimum breadth — measured, never gating
        assert entry.verdict == "insufficient_universe"


# ---------------------------------------------------------------------------
# Honesty — insufficient universe, n_effective, unrankable cross-sections
# ---------------------------------------------------------------------------


class TestHonesty:
    def test_ten_symbol_smoke_is_insufficient_universe_and_gates_nothing(self):
        closes, panel = _wide_fixture(score_of=lambda s: int(s[1:]), n_symbols=10)
        result = compute_book_quality([_spec(panel)], closes, n_bootstrap=100, seed=0)
        src = result.rankings[0]
        assert src.universe_breadth == 10
        assert src.universe_breadth < MIN_UNIVERSE_BREADTH
        assert src.verdict == "insufficient_universe"
        assert result.verdict == "insufficient_universe"
        for e in src.entries:
            assert e.verdict == "insufficient_universe"
            # numbers are still shown (honest display), never gate-worthy
            assert e.n_windows > 0
            assert e.net_return is not None

    def test_n_effective_discounts_overlapping_windows(self):
        closes, panel = _wide_fixture(score_of=lambda s: int(s[1:]), n_days=120)
        result = compute_book_quality(
            [_spec(panel)],
            closes,
            ns=(5,),
            horizons_weeks=(4,),
            n_bootstrap=50,
            seed=0,
        )
        entry = result.rankings[0].entries[0]
        # 120 bdays, step 5 -> 24 rebalance dates; h=20 bars -> windows at
        # positions 0..95 (start+h <= 119) -> 20 windows, overlap factor 4.
        assert entry.n_windows == 20
        assert entry.n_effective == 5
        assert entry.hac_t is not None and entry.hac_p is not None

    def test_constant_scores_are_unrankable_not_alphabetical_books(self):
        closes, panel = _wide_fixture(score_of=lambda s: 1.0)
        result = compute_book_quality(
            [_spec(panel)],
            closes,
            ns=(5,),
            horizons_weeks=(4,),
            n_bootstrap=50,
            seed=0,
        )
        entry = result.rankings[0].entries[0]
        assert entry.n_windows == 0
        assert entry.verdict == "no_data"

    def test_missing_spy_yields_none_never_fabricated(self):
        closes, dates = _make_closes(120, 80)  # no SPY series
        panel = _make_panel(list(closes), dates, lambda s: int(s[1:]))
        result = compute_book_quality(
            [_spec(panel)],
            closes,
            ns=(5,),
            horizons_weeks=(4,),
            n_bootstrap=50,
            seed=0,
        )
        entry = result.rankings[0].entries[0]
        assert entry.vs_spy is None
        # without a SPY baseline the activation criterion cannot be met
        assert entry.verdict == "no_edge"


# ---------------------------------------------------------------------------
# Determinism + cost model
# ---------------------------------------------------------------------------


class TestDeterminismAndCosts:
    def test_same_seed_same_pctile_bitwise(self):
        closes, panel = _wide_fixture(score_of=lambda s: int(s[1:]), n_symbols=30)
        kw = {"ns": (5,), "horizons_weeks": (4, 8), "n_bootstrap": 100, "seed": 7}
        a = compute_book_quality([_spec(panel)], closes, **kw)
        b = compute_book_quality([_spec(panel)], closes, **kw)
        pa = [e.vs_random_pctile for e in a.rankings[0].entries]
        pb = [e.vs_random_pctile for e in b.rankings[0].entries]
        assert pa == pb
        assert book_quality_payload(a) == book_quality_payload(b)

    def test_higher_cost_lowers_net_return(self):
        closes, panel = _wide_fixture(score_of=lambda s: int(s[1:]))
        cheap = compute_book_quality(
            [_spec(panel)],
            closes,
            ns=(5,),
            horizons_weeks=(4,),
            n_bootstrap=20,
            seed=0,
            cost_bps=0.0,
        )
        dear = compute_book_quality(
            [_spec(panel)],
            closes,
            ns=(5,),
            horizons_weeks=(4,),
            n_bootstrap=20,
            seed=0,
            cost_bps=50.0,
        )
        assert (
            dear.rankings[0].entries[0].net_return
            < cheap.rankings[0].entries[0].net_return
        )


# ---------------------------------------------------------------------------
# Ranking sources — LSTM (primary) vs consensus (comparison), dual display
# ---------------------------------------------------------------------------


def _votes_df(rows):
    return pd.DataFrame(
        rows, columns=["ts", "symbol", "agent", "score", "weight", "vetoed"]
    )


class TestRankingSources:
    def test_all_abstaining_lstm_is_unavailable_with_reason(self):
        ts = pd.Timestamp("2025-01-06")
        rows = []
        for sym, mom in (("AAA", 0.9), ("BBB", 0.2)):
            rows.append((ts, sym, "LSTMSignalAgent", 0.5, 0.0, False))  # abstain
            rows.append((ts, sym, "MomentumAgent", mom, 0.5, False))
        specs = build_ranking_specs(_votes_df(rows))
        by_source = {s.source: s for s in specs}
        assert set(by_source) == {"lstm", "consensus"}
        assert by_source["lstm"].panel.empty
        assert by_source["lstm"].unavailable_reason  # honest reason, non-empty
        assert not by_source["consensus"].panel.empty

    def test_consensus_panel_scores_come_from_the_real_engine(self):
        ts = pd.Timestamp("2025-01-06")
        rows = [
            (ts, "AAA", "MomentumAgent", 0.9, 0.5, False),
            (ts, "BBB", "MomentumAgent", 0.2, 0.5, False),
        ]
        specs = build_ranking_specs(_votes_df(rows))
        cons = {s.source: s for s in specs}["consensus"].panel
        scores = dict(zip(cons["symbol"], cons["score"]))
        # single active agent -> consensus == that agent's score
        assert scores["AAA"] == pytest.approx(0.9)
        assert scores["BBB"] == pytest.approx(0.2)

    def test_dual_display_both_sources_in_payload(self):
        closes, panel = _wide_fixture(score_of=lambda s: int(s[1:]), n_symbols=10)
        specs = [
            RankingSpec(source="lstm", panel=pd.DataFrame(), unavailable_reason="x"),
            RankingSpec(source="consensus", panel=panel),
        ]
        result = compute_book_quality(specs, closes, n_bootstrap=50, seed=0)
        payload = book_quality_payload(result)
        assert payload["rankings"]["lstm"]["available"] is False
        assert payload["rankings"]["lstm"]["reason"] == "x"
        assert payload["rankings"]["lstm"]["verdict"] == "unavailable"
        assert payload["rankings"]["consensus"]["available"] is True
        entry = payload["rankings"]["consensus"]["entries"][0]
        # issue #2402 §2 gate schema — every key present, additive
        # (Rev 2 / F2+F3: excluded/truncated counts + tie diagnostics)
        for key in (
            "N",
            "horizon_weeks",
            "net_return",
            "vs_spy",
            "vs_random_pctile",
            "precision_at_n",
            "n_effective",
            "excluded_count",
            "truncated_count",
            "tie_share",
            "skipped_degenerate_cut_windows",
            "verdict",
        ):
            assert key in entry
        assert payload["verdict"] == "insufficient_universe"

    def test_lstm_panel_from_store_reads_cross_section_at(self):
        store = LstmPanelStore()
        d1, d2 = pd.Timestamp("2025-01-06"), pd.Timestamp("2025-01-07")
        store.record_cycle(d1, [("AAA", 0.8), ("BBB", 0.1)])
        store.record_cycle(d2, [("AAA", 0.3), ("BBB", 0.9)])
        panel = lstm_panel_from_store(store, [d1, d2])
        got = {(row.ts, row.symbol): row.score for row in panel.itertuples(index=False)}
        assert got[(d1, "AAA")] == pytest.approx(0.8)
        assert got[(d2, "BBB")] == pytest.approx(0.9)
        assert len(panel) == 4


# ---------------------------------------------------------------------------
# Rendering — additive Markdown section
# ---------------------------------------------------------------------------


class TestRendering:
    def test_markdown_section_shows_insufficient_universe_smoke(self):
        closes, panel = _wide_fixture(score_of=lambda s: int(s[1:]), n_symbols=10)
        specs = [
            RankingSpec(
                source="lstm", panel=pd.DataFrame(), unavailable_reason="abstained"
            ),
            RankingSpec(source="consensus", panel=panel),
        ]
        result = compute_book_quality(specs, closes, n_bootstrap=50, seed=0)
        md = render_book_quality_markdown(result)
        assert "Buch-Qualit" in md  # section header present
        assert "insufficient_universe" in md
        assert "abstained" in md  # unavailable source surfaces its reason
        assert "n_eff" in md


# ---------------------------------------------------------------------------
# Rev 2 / F1 — null calibration: persistence-matched random baseline
# ---------------------------------------------------------------------------


class TestNullCalibration:
    def test_zero_skill_static_ranking_hits_fair_rate(self):
        """A STATIC random score per symbol has zero selection skill but full
        book persistence — exactly the realistic null of a persistent,
        uninformative ranking. Under a correctly calibrated null its pctile
        is ~Uniform(0, 100): P(pctile >= 95) = 5%. Binomial tolerance over
        12 seeds: P(X >= 4 | n=12, p=0.05) ~ 0.2% -> hits must be <= 3.

        The Rev-1 i.i.d.-per-date null FAILED this (review F1: 4-5/12 hits,
        bimodal pctiles) because churning random books diversify away the
        idiosyncratic drift a held book carries over the whole period.
        Everything below is seed-pinned and deterministic.
        """
        n_seeds = 12
        pctiles = []
        for score_seed in range(n_seeds):
            rng = np.random.default_rng([9000, score_seed])
            static = {f"S{i:03d}": float(x) for i, x in enumerate(rng.random(120))}
            closes, panel = _wide_fixture(score_of=static.__getitem__)
            result = compute_book_quality(
                [_spec(panel)],
                closes,
                ns=(5,),
                horizons_weeks=(4,),
                n_bootstrap=250,
                seed=42,
            )
            assert "persistence" in str(result.params.get("null_model", ""))
            pctiles.append(result.rankings[0].entries[0].vs_random_pctile)
        hits = sum(1 for p in pctiles if p >= 95.0)
        assert hits <= 3, (hits, pctiles)

    def test_identical_price_paths_static_scores_pin_pctile_50(self):
        """Degenerate proof from review F1: identical price paths for every
        symbol -> every book has the identical gross return, so ANY pctile
        away from the tie-midpoint 50.0 is a pure cost/persistence artifact.
        The Rev-1 churn null paid ~2(N-1)/N x cost per window while the held
        zero-skill book paid ~0 -> pctile 100.0 at zero skill. The
        persistence-matched null holds its books too -> exactly 50.0."""
        dates = pd.bdate_range("2025-01-06", periods=60)
        prices = 100.0 * 1.002 ** np.arange(60, dtype=float)
        closes = {f"S{i:03d}": pd.Series(prices.copy(), index=dates) for i in range(12)}
        rng = np.random.default_rng(7)
        static = {s: float(x) for s, x in zip(sorted(closes), rng.random(12))}
        panel = _make_panel(sorted(closes), dates, static.__getitem__)
        result = compute_book_quality(
            [_spec(panel)],
            closes,
            ns=(5,),
            horizons_weeks=(4,),
            n_bootstrap=200,
            seed=3,
        )
        entry = result.rankings[0].entries[0]
        assert entry.vs_random_pctile == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# Rev 2 / F2 — delisting: truncated returns instead of survival filtering
# ---------------------------------------------------------------------------


class TestDelistingTruncation:
    def test_delisted_crash_burdens_book_not_vanishes(self):
        """A top-ranked symbol crashes 50% and delists inside the forward
        window. Rev 1 removed it from the cross-section at the RANKING date
        (forward-survival look-ahead, review F2) -> flat book, net ~ 0. It
        MUST stay in the book with its return truncated at the last
        available bar, and the exclusion/truncation counts must surface."""
        dates = pd.bdate_range("2025-01-06", periods=40)
        closes = {f"S{i:03d}": pd.Series(100.0, index=dates) for i in range(5)}
        # S005: highest score; 100 -> 50 crash on bar 10, series ends bar 11
        crash = np.full(12, 100.0)
        crash[10:] = 50.0
        closes["S005"] = pd.Series(crash, index=dates[:12])
        scores = {s: float(i) for i, s in enumerate(sorted(closes))}
        panel = _make_panel(sorted(closes), dates, scores.__getitem__)
        result = compute_book_quality(
            [_spec(panel)],
            closes,
            ns=(2,),
            horizons_weeks=(4,),
            n_bootstrap=50,
            seed=0,
        )
        entry = result.rankings[0].entries[0]
        # 4 valid windows (rebalance positions 0/5/10/15; sample-end windows
        # stay dropped): S005 is IN the book with a truncated return on the
        # first three, excluded (no bar at t -> untradeable) on the last
        assert entry.n_windows == 4
        assert entry.truncated_count == 3
        assert entry.excluded_count == 1
        # the -50% delisting crash burdens the equal-weight 2-name book
        assert entry.net_return < -0.10


# ---------------------------------------------------------------------------
# Rev 2 / F3 — ties at the Top-N cut: reported, degenerate cuts skipped
# ---------------------------------------------------------------------------


class TestTieHandling:
    def test_partial_tie_share_reported(self):
        # cut score 3.0 shared by S001/S002/S003, ONE book slot decided
        # alphabetically -> tie_share 1/2 on every window
        closes, dates = _make_closes(6, 40)
        scores = {
            "S000": 5.0,
            "S001": 3.0,
            "S002": 3.0,
            "S003": 3.0,
            "S004": 1.0,
            "S005": 0.5,
        }
        panel = _make_panel(sorted(closes), dates, scores.__getitem__)
        result = compute_book_quality(
            [_spec(panel)],
            closes,
            ns=(2,),
            horizons_weeks=(4,),
            n_bootstrap=50,
            seed=0,
        )
        entry = result.rankings[0].entries[0]
        assert entry.n_windows == 4
        assert entry.tie_share == pytest.approx(0.5)
        assert entry.skipped_degenerate_cut_windows == 0

    def test_degenerate_cut_skipped_with_reason(self):
        # four symbols tied at the TOP score, N=2 -> every book slot would
        # carry the cut score with more candidates tied outside: the book is
        # a purely alphabetical selection (the honesty rule's forbidden
        # case) -> window skipped and the skip surfaces in the artifact
        closes, panel = _wide_fixture(
            score_of=lambda s: 200.0 if int(s[1:]) >= 116 else float(int(s[1:]))
        )
        result = compute_book_quality(
            [_spec(panel)],
            closes,
            ns=(2,),
            horizons_weeks=(4,),
            n_bootstrap=50,
            seed=0,
        )
        entry = result.rankings[0].entries[0]
        assert entry.n_windows == 0
        assert entry.verdict == "no_data"
        assert entry.skipped_degenerate_cut_windows == 20


# ---------------------------------------------------------------------------
# Rev 2 / F4 — --book-bootstrap floor
# ---------------------------------------------------------------------------


class TestBootstrapFloor:
    def test_floor_accepts_1000_and_above(self):
        assert parse_book_bootstrap("1000") == 1000
        assert parse_book_bootstrap("2500") == 2500

    def test_floor_rejects_below_1000_and_garbage(self):
        import argparse

        with pytest.raises(argparse.ArgumentTypeError):
            parse_book_bootstrap("999")
        with pytest.raises(argparse.ArgumentTypeError):
            parse_book_bootstrap("10")
        with pytest.raises(argparse.ArgumentTypeError):
            parse_book_bootstrap("abc")
