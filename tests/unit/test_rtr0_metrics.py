"""
test_rtr0_metrics.py — RTR-0 (#1947) attribution metric library.

The metric library IS the gate: a mis-computed IC or a consensus replication
that drifts from ``ConsensusEngine.aggregate`` could wave a harmful agent
activation through RTR-1..4. Hence every metric is pinned here BEFORE the
implementation exists (TDD Red -> Green), and the consensus replication is
asserted against the REAL engine, not a re-derivation.

Covered (plan #1947 §8):
  * R1 spearman_ic       — monotone => IC ~ +1 / t large; noise => IC ~ 0;
                            degenerate inputs => clean 0.0, no crash
  * R2 hit_rate          — thresholds IMPORTED from consensus.py (no literal dup)
  * R3 loo_consensus_*   — bit-near parity with ConsensusEngine.aggregate incl.
                            regime<0.45 -> Momentum*0.5 coupling and the
                            RegimeDetection/DrawdownGuard mean exclusion;
                            LOO delta sign for helpful/harmful agents
  * R4 vote_correlation_matrix — identical => 1.0, anti-correlated => -1.0
  * R5 net-of-cost       — higher cost_bps lowers Sharpe monotonically
  * FDR                  — Benjamini-Hochberg on IC p-values (plan §12.5)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.analysis.attribution.metrics import (
    benjamini_hochberg,
    classify_agent,
    consensus_positions,
    daily_ic_series,
    daily_ic_significance,
    fold_ics,
    gate_verdict,
    hac_mean_test,
    hit_rate,
    loo_consensus_sharpe,
    portfolio_sharpe,
    replicate_consensus,
    spearman_ic,
    vote_correlation_matrix,
)
from core.round_table.base_agent import VoteResult
from core.round_table.consensus import (
    SIGNAL_BUY_THRESHOLD,
    SIGNAL_SELL_THRESHOLD,
    ConsensusEngine,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _vote(agent: str, score: float, weight: float = 0.5, vetoed: bool = False):
    return VoteResult(
        agent_name=agent,
        symbol="TEST",
        score=score,
        weight=weight,
        reasoning="fixture",
        vetoed=vetoed,
    )


def _votes_df(rows):
    """rows: list of (ts, symbol, agent, score, weight, vetoed)."""
    return pd.DataFrame(
        rows, columns=["ts", "symbol", "agent", "score", "weight", "vetoed"]
    )


# ---------------------------------------------------------------------------
# R1 — spearman_ic
# ---------------------------------------------------------------------------


class TestSpearmanIC:
    def test_perfect_monotone_gives_ic_one_and_large_t(self):
        rng = np.random.default_rng(7)
        scores = rng.uniform(0, 1, 200)
        fwd = np.argsort(np.argsort(scores)).astype(float)  # same ranking
        res = spearman_ic(scores, fwd)
        assert res.ic == pytest.approx(1.0, abs=1e-9)
        assert res.t_stat > 10.0
        assert res.n == 200

    def test_random_target_gives_near_zero_ic(self):
        rng = np.random.default_rng(11)
        scores = rng.uniform(0, 1, 5000)
        fwd = rng.normal(0, 1, 5000)
        res = spearman_ic(scores, fwd)
        assert abs(res.ic) < 0.05
        assert abs(res.t_stat) < 3.0

    def test_anti_monotone_gives_ic_minus_one(self):
        scores = np.arange(50, dtype=float)
        fwd = -scores
        res = spearman_ic(scores, fwd)
        assert res.ic == pytest.approx(-1.0, abs=1e-9)

    def test_constant_scores_degenerate_to_zero_without_crash(self):
        res = spearman_ic(np.ones(30), np.arange(30, dtype=float))
        assert res.ic == 0.0
        assert res.t_stat == 0.0

    def test_too_few_samples_degenerate_to_zero(self):
        res = spearman_ic(np.array([0.5]), np.array([0.1]))
        assert res.ic == 0.0
        assert res.t_stat == 0.0
        assert res.n == 1

    def test_nan_pairs_are_dropped_not_propagated(self):
        scores = np.array([0.1, 0.2, np.nan, 0.4, 0.5, 0.6, 0.7, 0.8])
        fwd = np.array([1.0, 2.0, 3.0, np.nan, 5.0, 6.0, 7.0, 8.0])
        res = spearman_ic(scores, fwd)
        assert res.n == 6
        assert res.ic == pytest.approx(1.0, abs=1e-9)


# ---------------------------------------------------------------------------
# R2 — hit_rate (thresholds imported from consensus.py, never duplicated)
# ---------------------------------------------------------------------------


class TestHitRate:
    def test_default_thresholds_are_the_consensus_constants(self):
        import inspect

        sig = inspect.signature(hit_rate)
        assert sig.parameters["buy_thr"].default is SIGNAL_BUY_THRESHOLD
        assert sig.parameters["sell_thr"].default is SIGNAL_SELL_THRESHOLD

    def test_known_confusion_matrix(self):
        # 2 BUY hits, 1 BUY miss, 1 SELL hit, 1 SELL miss, 2 HOLD (not counted)
        scores = np.array([0.9, 0.8, 0.7, 0.1, 0.2, 0.5, 0.5])
        fwd = np.array([0.01, 0.02, -0.01, -0.02, 0.01, 0.05, -0.05])
        res = hit_rate(scores, fwd)
        assert res.n_signals == 5
        assert res.n_hits == 3
        assert res.rate == pytest.approx(3 / 5)

    def test_no_directional_signals_gives_nan_rate(self):
        scores = np.array([0.5, 0.5, 0.4, 0.6])
        fwd = np.array([0.01, -0.01, 0.02, -0.02])
        res = hit_rate(scores, fwd)
        assert res.n_signals == 0
        assert np.isnan(res.rate)


# ---------------------------------------------------------------------------
# R3 — consensus replication parity + LOO
# ---------------------------------------------------------------------------


class TestConsensusReplicationParity:
    def test_replication_matches_real_engine_plain(self):
        votes = [
            _vote("MomentumAgent", 0.8, 0.45),
            _vote("PatternRecognitionAgent", 0.6, 0.30),
            _vote("LSTMSignalAgent", 0.7, 0.40),
        ]
        expected = ConsensusEngine().aggregate(
            [_vote(v.agent_name, v.score, v.weight, v.vetoed) for v in votes]
        )
        assert replicate_consensus(votes) == pytest.approx(expected, abs=1e-12)

    def test_regime_below_045_halves_momentum_weight(self, monkeypatch):
        # consensus.py:179-188 — bearish regime scales MomentumAgent weight 0.5x (binary).
        # ADR-018 flipped REGIME_CONDITIONER_ENABLED ON by default, replacing this binary
        # ×0.5 with gradual scaling (1 − damp·severity, consensus.py:233). The got==expected
        # parity still holds (both use the real engine); only this test's hardcoded manual
        # ×0.5 value is binary-specific. Pin the flag OFF so the binary path is validated;
        # the gradual path is covered by test_regime_conditioner.py.
        import config

        monkeypatch.setattr(
            config.get_config(), "REGIME_CONDITIONER_ENABLED", False, raising=False
        )
        votes = [
            _vote("RegimeDetectionAgent", 0.30, 0.50),
            _vote("MomentumAgent", 0.9, 0.40),
            _vote("PatternRecognitionAgent", 0.1, 0.40),
        ]
        got = replicate_consensus(votes)
        # manual: momentum 0.9@0.20, pattern 0.1@0.40 -> (0.18+0.04)/0.6
        assert got == pytest.approx((0.9 * 0.20 + 0.1 * 0.40) / 0.60, abs=1e-12)
        expected = ConsensusEngine().aggregate(
            [_vote(v.agent_name, v.score, v.weight, v.vetoed) for v in votes]
        )
        assert got == pytest.approx(expected, abs=1e-12)

    def test_conditioners_are_excluded_from_the_mean(self):
        # consensus.py:190-195 — DrawdownGuard/RegimeDetection never enter the mean
        base = [
            _vote("MomentumAgent", 0.8, 0.45),
            _vote("PatternRecognitionAgent", 0.6, 0.30),
        ]
        with_conditioners = base + [
            _vote("DrawdownGuardAgent", 0.05, 0.60),
            _vote("RegimeDetectionAgent", 0.55, 0.50),
        ]
        assert replicate_consensus(with_conditioners) == pytest.approx(
            replicate_consensus(base), abs=1e-12
        )

    def test_abstentions_and_vetoed_votes_are_excluded(self):
        base = [_vote("MomentumAgent", 0.8, 0.45)]
        noisy = base + [
            _vote("LSTMSignalAgent", 0.5, 0.0),  # abstention (weight 0)
            _vote("RLConfidenceAgent", 0.99, 0.40, vetoed=True),
        ]
        assert replicate_consensus(noisy) == pytest.approx(
            replicate_consensus(base), abs=1e-12
        )


class TestConsensusPositions:
    def test_buy_sell_hold_mapping(self):
        df = _votes_df(
            [
                ("2024-01-02", "AAA", "MomentumAgent", 0.9, 0.45, False),
                ("2024-01-03", "AAA", "MomentumAgent", 0.1, 0.45, False),
                ("2024-01-04", "AAA", "MomentumAgent", 0.5, 0.45, False),
            ]
        )
        pos = consensus_positions(df)
        by_ts = pos.set_index("ts")["position"]
        assert by_ts["2024-01-02"] == 1
        assert by_ts["2024-01-03"] == -1
        assert by_ts["2024-01-04"] == 0

    def test_veto_blocks_buy_but_not_sell(self):
        # runner.py:176-193 — direction-aware veto: blocks HOLD/BUY, passes SELL
        df = _votes_df(
            [
                ("2024-01-02", "AAA", "MomentumAgent", 0.9, 0.45, False),
                ("2024-01-02", "AAA", "DrawdownGuardAgent", 0.05, 0.60, True),
                ("2024-01-03", "AAA", "MomentumAgent", 0.1, 0.45, False),
                ("2024-01-03", "AAA", "DrawdownGuardAgent", 0.05, 0.60, True),
            ]
        )
        pos = consensus_positions(df)
        by_ts = pos.set_index("ts")["position"]
        assert by_ts["2024-01-02"] == 0  # BUY blocked by veto
        assert by_ts["2024-01-03"] == -1  # SELL passes through the veto

    def test_no_active_votes_is_no_trade_not_a_sell(self):
        # aggregate() returns 0.0 on an empty active set; that artifact must
        # never be read as a SELL conviction.
        df = _votes_df(
            [
                ("2024-01-02", "AAA", "LSTMSignalAgent", 0.5, 0.0, False),
                ("2024-01-02", "AAA", "DrawdownGuardAgent", 0.9, 0.60, False),
            ]
        )
        pos = consensus_positions(df)
        assert pos["position"].tolist() == [0]

    def test_exclude_agent_removes_its_vote(self):
        df = _votes_df(
            [
                ("2024-01-02", "AAA", "MomentumAgent", 0.9, 0.45, False),
                ("2024-01-02", "AAA", "PatternRecognitionAgent", 0.1, 4.00, False),
            ]
        )
        pos_all = consensus_positions(df).set_index("ts")["position"]
        pos_loo = consensus_positions(
            df, exclude_agent="PatternRecognitionAgent"
        ).set_index("ts")["position"]
        assert pos_all["2024-01-02"] == -1  # heavy bearish pattern dominates
        assert pos_loo["2024-01-02"] == 1  # without it, momentum BUY


# ---------------------------------------------------------------------------
# R5 — net-of-cost portfolio Sharpe
# ---------------------------------------------------------------------------


def _positions_frame():
    # AAA: long on day1+2 then flat; turnover on entry and exit
    return pd.DataFrame(
        {
            "ts": ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"],
            "symbol": ["AAA"] * 4,
            "position": [1, 1, 0, 1],
        }
    )


def _fwd_frame():
    return pd.DataFrame(
        {
            "ts": ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"],
            "symbol": ["AAA"] * 4,
            "fwd_ret": [0.01, -0.005, 0.002, 0.008],
        }
    )


class TestPortfolioSharpe:
    def test_zero_cost_equals_gross(self):
        gross = portfolio_sharpe(_positions_frame(), _fwd_frame(), cost_bps=0.0)
        assert gross.total_cost == 0.0

    def test_cost_monotonically_lowers_sharpe(self):
        s0 = portfolio_sharpe(_positions_frame(), _fwd_frame(), cost_bps=0.0)
        s10 = portfolio_sharpe(_positions_frame(), _fwd_frame(), cost_bps=10.0)
        s50 = portfolio_sharpe(_positions_frame(), _fwd_frame(), cost_bps=50.0)
        assert s0.sharpe > s10.sharpe > s50.sharpe
        assert s10.total_cost > 0.0

    def test_flat_book_has_zero_sharpe_and_zero_cost(self):
        pos = _positions_frame().assign(position=0)
        res = portfolio_sharpe(pos, _fwd_frame(), cost_bps=10.0)
        assert res.sharpe == 0.0
        assert res.total_cost == 0.0


class TestLeaveOneOut:
    def _panel(self):
        """60 days, 2 symbols. HelperAgent is perfectly aligned with returns,
        NoiseAgent is dead neutral, HarmAgent is perfectly inverted."""
        rng = np.random.default_rng(42)
        rows = []
        fwd_rows = []
        dates = pd.bdate_range("2024-01-02", periods=60)
        for sym in ("AAA", "BBB"):
            rets = rng.normal(0.0, 0.01, len(dates))
            for ts, r in zip(dates, rets):
                good = 0.9 if r > 0 else 0.1
                bad = 0.1 if r > 0 else 0.9
                rows.append((ts, sym, "HelperAgent", good, 0.6, False))
                rows.append((ts, sym, "HarmAgent", bad, 0.4, False))
                rows.append((ts, sym, "NoiseAgent", 0.5, 0.3, False))
                fwd_rows.append((ts, sym, r))
        votes = _votes_df(rows)
        fwd = pd.DataFrame(fwd_rows, columns=["ts", "symbol", "fwd_ret"])
        return votes, fwd

    def test_loo_delta_signs(self):
        votes, fwd = self._panel()
        loo = loo_consensus_sharpe(votes, fwd, cost_bps=0.0)
        # removing the helpful agent must HURT the consensus => positive delta
        assert loo["HelperAgent"].delta_sharpe > 0
        # removing the harmful agent must HELP the consensus => negative delta
        assert loo["HarmAgent"].delta_sharpe < 0

    def test_loo_covers_every_agent_in_the_frame(self):
        votes, fwd = self._panel()
        loo = loo_consensus_sharpe(votes, fwd, cost_bps=10.0)
        assert set(loo) == {"HelperAgent", "HarmAgent", "NoiseAgent"}


# ---------------------------------------------------------------------------
# M1 — per-date cross-sectional IC series (deterministic)
# ---------------------------------------------------------------------------


def _cs_panel(date_orientations, n_symbols=4):
    """Panel with one exactly known cross-sectional IC per date.

    ``date_orientations``: list of +1/-1 — per date the symbol scores are
    rank-aligned (+1 -> IC exactly +1.0) or rank-inverted (-1 -> IC -1.0)
    with the forward returns.
    """
    rows = []
    dates = pd.bdate_range("2024-01-02", periods=len(date_orientations))
    symbols = [f"S{i}" for i in range(n_symbols)]
    for ts, orient in zip(dates, date_orientations):
        for k, sym in enumerate(symbols):
            fwd = (k + 1) * 0.01
            score = (k + 1) / (n_symbols + 1)
            if orient < 0:
                score = 1.0 - score
            rows.append((ts, sym, score, fwd))
    return pd.DataFrame(rows, columns=["ts", "symbol", "score", "fwd_ret"])


class TestDailyICSeries:
    def test_deterministic_per_date_ics(self):
        panel = _cs_panel([+1, -1, +1])
        series = daily_ic_series(panel)
        assert len(series) == 3
        assert series.iloc[0] == pytest.approx(1.0)
        assert series.iloc[1] == pytest.approx(-1.0)
        assert series.iloc[2] == pytest.approx(1.0)

    def test_thin_cross_sections_are_skipped_not_zeroed(self):
        # date 2 has only 2 symbols -> below min_cross_section=3 -> skipped
        panel = _cs_panel([+1, +1])
        thin_date = pd.Timestamp("2024-01-04")
        rows = [
            (thin_date, "S0", 0.2, 0.01),
            (thin_date, "S1", 0.8, 0.02),
        ]
        panel = pd.concat(
            [panel, pd.DataFrame(rows, columns=panel.columns)], ignore_index=True
        )
        series = daily_ic_series(panel, min_cross_section=3)
        assert thin_date not in series.index
        assert len(series) == 2

    def test_constant_score_dates_are_skipped(self):
        panel = _cs_panel([+1])
        const_date = pd.Timestamp("2024-01-03")
        rows = [(const_date, f"S{i}", 0.5, (i + 1) * 0.01) for i in range(4)]
        panel = pd.concat(
            [panel, pd.DataFrame(rows, columns=panel.columns)], ignore_index=True
        )
        series = daily_ic_series(panel)
        assert const_date not in series.index


# ---------------------------------------------------------------------------
# M1 — HAC / Newey-West t on the daily IC series (reference-pinned)
# ---------------------------------------------------------------------------


class TestHACMeanTest:
    def test_reference_value_lag1(self):
        # hand-computed Bartlett/Newey-West reference:
        # values [0.1, 0.2, 0.15, 0.05, 0.1], lag 1
        # mean 0.12, gamma0 0.0026, gamma1 0.00002, S 0.00262
        # t = 0.12 / sqrt(0.00262 / 5) = 5.2422...
        res = hac_mean_test([0.1, 0.2, 0.15, 0.05, 0.1], lag=1)
        assert res.mean == pytest.approx(0.12)
        assert res.t_stat == pytest.approx(5.2422, abs=1e-3)
        assert res.p_value == pytest.approx(1.5865e-07, rel=1e-3)

    def test_lag_zero_equals_iid_t(self):
        # lag 0 collapses to the plain (ddof=0) t-test: 5.2623...
        res = hac_mean_test([0.1, 0.2, 0.15, 0.05, 0.1], lag=0)
        assert res.t_stat == pytest.approx(5.2623, abs=1e-3)

    def test_positive_autocorrelation_shrinks_t(self):
        # overlapping labels induce positive autocorrelation; the HAC
        # correction must WIDEN the error band (smaller |t|) vs iid
        rng = np.random.default_rng(3)
        noise = rng.normal(0, 0.01, 400)
        # strong MA(4) overlap structure, like 5d labels on daily data
        vals = 0.05 + np.convolve(noise, np.ones(5), mode="valid")
        iid = hac_mean_test(vals, lag=0)
        hac = hac_mean_test(vals, lag=4)
        assert abs(hac.t_stat) < abs(iid.t_stat)

    def test_degenerate_inputs_are_safe(self):
        assert hac_mean_test([], lag=5).t_stat == 0.0
        assert hac_mean_test([0.1], lag=5).p_value == 1.0
        # constant nonzero series: zero variance, nonzero mean -> capped t
        res = hac_mean_test([0.2, 0.2, 0.2, 0.2], lag=2)
        assert res.t_stat > 1e6
        assert res.p_value == 0.0


class TestDailyICSignificance:
    def test_cross_sectional_panel_uses_hac_method(self):
        panel = _cs_panel([+1] * 30)
        res = daily_ic_significance(panel, lag=5)
        assert res.method == "per-date-cs-hac"
        assert res.n_days == 30
        assert res.ic_mean == pytest.approx(1.0)
        assert res.p_value < 0.001

    def test_single_symbol_panel_falls_back_to_pooled(self):
        rows = [
            (ts, "AAA", i / 40.0, i * 0.001)
            for i, ts in enumerate(pd.bdate_range("2024-01-02", periods=40))
        ]
        panel = pd.DataFrame(rows, columns=["ts", "symbol", "score", "fwd_ret"])
        res = daily_ic_significance(panel, lag=5)
        assert res.method == "pooled-iid-fallback"
        assert res.ic_mean == pytest.approx(1.0)

    def test_mixed_dates_mean_is_series_mean(self):
        panel = _cs_panel([+1, +1, -1, +1])
        res = daily_ic_significance(panel, lag=1)
        assert res.ic_mean == pytest.approx((1.0 + 1.0 - 1.0 + 1.0) / 4.0)


# ---------------------------------------------------------------------------
# M3 — sign-aware truth-table classification
# ---------------------------------------------------------------------------


class TestSignAwareClassification:
    def test_significant_negative_ic_never_keeps(self):
        # M3: FDR-significant NEGATIVE IC + LOO lift is a sign conflict —
        # machine-readable own status, never 'keep'
        cls = classify_agent(0.4, True, ic=-0.08)
        assert cls == "INVERTED-IC"
        assert gate_verdict(cls) == "invert-candidate"

    def test_significant_positive_ic_with_lift_keeps(self):
        cls = classify_agent(0.4, True, ic=0.05)
        assert cls == "HEBT"
        assert gate_verdict(cls) == "keep"

    def test_lift_without_significance_stays_noise(self):
        assert classify_agent(0.4, False, ic=0.5) == "RAUSCHEN"

    def test_harm_beyond_band_is_schadet_regardless_of_ic_sign(self):
        assert classify_agent(-0.2, True, ic=-0.5) == "SCHADET"
        assert classify_agent(-0.2, True, ic=0.5) == "SCHADET"

    def test_excluded_when_delta_is_none(self):
        assert classify_agent(None, False, ic=None) == "EXCLUDED"


# ---------------------------------------------------------------------------
# M4 + harness first consumer — fold ICs over TRADING-DAY blocks
# ---------------------------------------------------------------------------


class TestFoldICs:
    def test_folds_are_trading_day_blocks_not_row_blocks(self):
        # 30 dates x 3 symbols + 1 extra symbol on date 0 (91 rows — NOT
        # divisible into row blocks that respect day boundaries). Date 9 is
        # rank-inverted (IC -1), all others +1. Day-block folds (fold_size
        # 30 // 5 = 6 dates): [6..11], [12..17], [18..23], [24..29]
        # -> fold ICs [(5 - 1)/6, 1.0, 1.0, 1.0]. Purge (5 trading DAYS =
        # label horizon) leaves fold 0 one clean train day — folds survive.
        orientations = [+1] * 30
        orientations[9] = -1
        panel = _cs_panel(orientations, n_symbols=3)
        extra = pd.DataFrame(
            [(pd.Timestamp("2024-01-02"), "S99", 0.9, 0.04)], columns=panel.columns
        )
        panel = pd.concat([panel, extra], ignore_index=True)
        ics = fold_ics(panel, n_splits=4, label_horizon=5, embargo=5)
        assert len(ics) == 4
        assert ics[0] == pytest.approx((5 * 1.0 - 1.0) / 6.0)
        assert ics[1] == pytest.approx(1.0)
        assert ics[2] == pytest.approx(1.0)
        assert ics[3] == pytest.approx(1.0)

    def test_monotone_signal_positive_in_every_fold(self):
        panel = _cs_panel([+1] * 60)
        ics = fold_ics(panel, n_splits=4, label_horizon=5, embargo=5)
        assert len(ics) == 4
        assert all(ic > 0.99 for ic in ics)

    def test_too_few_days_yield_no_folds(self):
        panel = _cs_panel([+1] * 4)
        assert fold_ics(panel, n_splits=4, label_horizon=5, embargo=5) == []


# ---------------------------------------------------------------------------
# R4 — vote correlation matrix
# ---------------------------------------------------------------------------


class TestVoteCorrelationMatrix:
    def test_identical_and_anticorrelated_series(self):
        rows = []
        for i, ts in enumerate(pd.bdate_range("2024-01-02", periods=20)):
            s = (i % 10) / 10.0
            rows.append((ts, "AAA", "AgentX", s, 0.5, False))
            rows.append((ts, "AAA", "AgentY", s, 0.5, False))
            rows.append((ts, "AAA", "AgentZ", 1.0 - s, 0.5, False))
        corr = vote_correlation_matrix(_votes_df(rows))
        assert corr.loc["AgentX", "AgentY"] == pytest.approx(1.0)
        assert corr.loc["AgentX", "AgentZ"] == pytest.approx(-1.0)

    def test_abstentions_are_not_treated_as_opinions(self):
        rows = []
        for i, ts in enumerate(pd.bdate_range("2024-01-02", periods=10)):
            rows.append((ts, "AAA", "AgentX", i / 10.0, 0.5, False))
            rows.append((ts, "AAA", "Abstainer", 0.5, 0.0, False))
        corr = vote_correlation_matrix(_votes_df(rows))
        assert "Abstainer" not in corr.columns

    def test_dormant_weight_zero_scores_are_still_opinions(self):
        # validate-before-activate: a DORMANT agent (default_weight 0.0 —
        # Pattern/Fundamentals/Valuation) votes real scores at weight 0.
        # Those are opinions (score != abstention contract 0.5) and MUST be
        # measurable, or the gate can never clear the very agents it exists
        # to validate.
        rows = []
        for i, ts in enumerate(pd.bdate_range("2024-01-02", periods=10)):
            s = (i % 10) / 10.0
            rows.append((ts, "AAA", "AgentX", s, 0.5, False))
            rows.append((ts, "AAA", "DormantAgent", s, 0.0, False))
        corr = vote_correlation_matrix(_votes_df(rows))
        assert "DormantAgent" in corr.columns
        assert corr.loc["AgentX", "DormantAgent"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# FDR — Benjamini-Hochberg (plan §12.5: 11 agents x metrics need correction)
# ---------------------------------------------------------------------------


class TestBenjaminiHochberg:
    def test_rejects_small_pvalues_keeps_large(self):
        pvals = [0.001, 0.002, 0.8, 0.9]
        sig = benjamini_hochberg(pvals, alpha=0.05)
        assert sig == [True, True, False, False]

    def test_all_large_pvalues_reject_nothing(self):
        assert benjamini_hochberg([0.4, 0.6, 0.9], alpha=0.05) == [
            False,
            False,
            False,
        ]

    def test_empty_input_is_safe(self):
        assert benjamini_hochberg([], alpha=0.05) == []
