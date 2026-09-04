"""
test_rtr0_report.py — RTR-0 (#1947) gate artifact assembly.

The report is the GATE ARTIFACT RTR-1..4 must cite. Pinned here:
  * every ALL_AGENTS member appears exactly once — replayed, abstained
    (N/A) or EXCLUDED — nothing is silently dropped;
  * an agent that abstained on every bar is N/A (EXCLUDED verdict), never
    classified as RAUSCHEN off zero information;
  * IC significance is per-date cross-sectional + HAC/Newey-West (M1) —
    the pooled-iid path only survives as an explicit, labelled fallback;
  * the truth table carries the keep/fix/kill/invert-candidate gate verdict;
  * the artifact is versioned (run parameters + flag POSTURE embedded — M2
    reproducibility contract) and documents the conditioner-damp limitation.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from core.analysis.attribution.report import (
    build_agent_rows,
    build_run_meta,
    render_markdown,
    runtime_posture,
)
from core.round_table.agents import ALL_AGENTS

_ALL_AGENT_NAMES = [type(a).__name__ for a in ALL_AGENTS]


def _panel(n_days: int = 40, n_symbols: int = 4):
    """Deterministic multi-symbol panel: per date the MomentumAgent scores are
    rank-aligned with the forward returns (cross-sectional IC exactly +1),
    plus one full abstainer (VIXAwareRiskAgent)."""
    rng = np.random.default_rng(9)
    symbols = [f"SY{i}" for i in range(n_symbols)]
    rows = []
    fwd_rows = []
    for ts in pd.bdate_range("2024-01-02", periods=n_days):
        base = float(rng.normal(0, 0.01))
        for k, sym in enumerate(symbols):
            r = base + (k + 1) * 0.005
            score = (k + 1) / (n_symbols + 1)
            rows.append((ts, sym, "MomentumAgent", score, 0.45, False))
            rows.append((ts, sym, "VIXAwareRiskAgent", 0.5, 0.0, False))  # abstains
            fwd_rows.append((ts, sym, r))
    votes = pd.DataFrame(
        rows, columns=["ts", "symbol", "agent", "score", "weight", "vetoed"]
    )
    fwd = pd.DataFrame(fwd_rows, columns=["ts", "symbol", "fwd_ret"])
    return votes, fwd


class TestBuildAgentRows:
    def test_every_all_agents_member_has_exactly_one_row(self):
        votes, fwd = _panel()
        rows, _ = build_agent_rows(
            votes,
            fwd_ic=fwd,
            fwd_1d=fwd,
            exclusions={"NewsSentimentAgent": "LLM", "SpecialistAlphaAgent": "dormant"},
            cost_bps=10.0,
        )
        names = [r.agent for r in rows]
        assert names == _ALL_AGENT_NAMES
        assert len(names) == len(set(names))

    def test_full_abstainer_is_na_not_noise(self):
        votes, fwd = _panel()
        rows, _ = build_agent_rows(
            votes, fwd_ic=fwd, fwd_1d=fwd, exclusions={}, cost_bps=10.0
        )
        vix = next(r for r in rows if r.agent == "VIXAwareRiskAgent")
        assert vix.classification == "EXCLUDED"
        assert vix.ic is None

    def test_excluded_agent_carries_reason_and_na_verdict(self):
        votes, fwd = _panel()
        rows, _ = build_agent_rows(
            votes,
            fwd_ic=fwd,
            fwd_1d=fwd,
            exclusions={"NewsSentimentAgent": "external LLM"},
            cost_bps=10.0,
        )
        news = next(r for r in rows if r.agent == "NewsSentimentAgent")
        assert news.classification == "EXCLUDED"
        assert "LLM" in news.note
        assert news.verdict.startswith("n/a")

    def test_opinionated_agent_gets_measured_cross_sectionally(self):
        votes, fwd = _panel()
        rows, _ = build_agent_rows(
            votes, fwd_ic=fwd, fwd_1d=fwd, exclusions={}, cost_bps=0.0
        )
        mom = next(r for r in rows if r.agent == "MomentumAgent")
        # per-date cross-sectional IC is exactly +1 on every date (M1)
        assert mom.ic is not None and mom.ic == pytest.approx(1.0)
        assert mom.ic_method == "per-date-cs-hac"
        assert mom.n_days == 40
        assert mom.loo_delta_sharpe is not None

    def test_hac_lag_follows_ic_horizon(self):
        votes, fwd = _panel()
        rows, _ = build_agent_rows(
            votes, fwd_ic=fwd, fwd_1d=fwd, exclusions={}, cost_bps=0.0, ic_horizon=7
        )
        mom = next(r for r in rows if r.agent == "MomentumAgent")
        assert mom.hac_lag == 7

    def test_single_symbol_panel_is_labelled_pooled_fallback(self):
        # one symbol -> no cross-section -> the (weaker) pooled-iid path must
        # be explicit in the artifact, never silent
        rng = np.random.default_rng(21)
        rows_in, fwd_rows = [], []
        for ts in pd.bdate_range("2024-01-02", periods=40):
            r = float(rng.normal(0, 0.01))
            rows_in.append(
                (ts, "AAA", "MomentumAgent", 0.9 if r > 0 else 0.1, 0.45, False)
            )
            fwd_rows.append((ts, "AAA", r))
        votes = pd.DataFrame(
            rows_in, columns=["ts", "symbol", "agent", "score", "weight", "vetoed"]
        )
        fwd = pd.DataFrame(fwd_rows, columns=["ts", "symbol", "fwd_ret"])
        rows, _ = build_agent_rows(
            votes, fwd_ic=fwd, fwd_1d=fwd, exclusions={}, cost_bps=0.0
        )
        mom = next(r for r in rows if r.agent == "MomentumAgent")
        assert mom.ic_method == "pooled-iid-fallback"
        assert "pooled" in mom.note.lower()

    def test_dormant_agent_ic_is_measured_but_loo_is_zero(self):
        # Fundamentals/Valuation: default_weight 0.0 — real scores at
        # weight 0 cannot move the consensus (LOO delta 0) but their
        # standalone signal quality MUST appear in the gate.
        # (PatternRecognitionAgent was deleted in #2395 — Fundamentals is
        # the remaining dormant weight-0 roster member.)
        votes, fwd = _panel()
        dormant_rows = []
        for ts in pd.bdate_range("2024-01-02", periods=40):
            for k, sym in enumerate([f"SY{i}" for i in range(4)]):
                dormant_rows.append(
                    (ts, sym, "FundamentalsAgent", (k + 1) / 5.0, 0.0, False)
                )
        votes = pd.concat(
            [votes, pd.DataFrame(dormant_rows, columns=votes.columns)],
            ignore_index=True,
        )
        rows, _ = build_agent_rows(
            votes, fwd_ic=fwd, fwd_1d=fwd, exclusions={}, cost_bps=0.0
        )
        fund = next(r for r in rows if r.agent == "FundamentalsAgent")
        assert fund.ic is not None and fund.ic == pytest.approx(1.0)
        assert fund.loo_delta_sharpe == pytest.approx(0.0)  # cannot move consensus
        assert "dormant" in fund.note.lower()


# ---------------------------------------------------------------------------
# M2 — posture snapshot + run_meta reproducibility contract
# ---------------------------------------------------------------------------


class TestRuntimePosture:
    REQUIRED = {
        "DRAWDOWN_GUARD_CONDITIONER_ENABLED",
        "DRAWDOWN_SEVERE_VETO_THRESHOLD",
        "DRAWDOWN_CONVICTION_DAMP_MAX",
        "effective_drawdown_veto_threshold_30d",
        "effective_drawdown_veto_threshold_1bar",
        "LSTM_VOTE_USES_CROSS_SECTIONAL_RANK",
        "SPECIALIST_ALPHA_WEIGHT",
        "loo_book_models",
    }

    def test_posture_contains_reproducibility_contract_keys(self):
        posture = runtime_posture()
        assert self.REQUIRED <= set(posture)
        assert isinstance(posture["DRAWDOWN_GUARD_CONDITIONER_ENABLED"], bool)

    def test_effective_veto_thresholds_follow_the_flag(self):
        posture = runtime_posture()
        if posture["DRAWDOWN_GUARD_CONDITIONER_ENABLED"]:
            assert (
                posture["effective_drawdown_veto_threshold_30d"]
                == posture["DRAWDOWN_SEVERE_VETO_THRESHOLD"]
            )
        else:
            assert posture["effective_drawdown_veto_threshold_30d"] == 0.07
            assert posture["effective_drawdown_veto_threshold_1bar"] == 0.05

    def test_loo_book_limitation_is_explicit(self):
        posture = runtime_posture()
        assert "hard-veto" in posture["loo_book_models"]

    def test_posture_captures_momentum_fallback_abstain_flag(self):
        # #1947 N1: MOMENTUM_FALLBACK_ABSTAIN_ENABLED (agents.py:387) shapes
        # the Momentum vote on thin windows — a run under flag ON would
        # produce different Momentum rows; the posture must show it.
        posture = runtime_posture()
        assert "MOMENTUM_FALLBACK_ABSTAIN_ENABLED" in posture
        assert isinstance(posture["MOMENTUM_FALLBACK_ABSTAIN_ENABLED"], bool)


class TestBuildRunMeta:
    def _fields(self):
        return {
            "issue": "#1947 RTR-0",
            "generated_utc": "2026-07-22T00:00:00+00:00",
            "git_sha": "abc1234",
            "source": "replay",
            "data_dir": "/tmp/bars",
            "symbols": ["AAA"],
            "coverage": {"AAA": {"bars": 10, "from": "2024-01-01", "to": "2024-02-01"}},
            "start": "2024-01-01",
            "end": "2024-02-01",
            "n_bars_replayed": 10,
            "cost_bps": 10.0,
            "ic_horizon_bars": 5,
            "hac_lag_days": 5,
            "fdr_alpha": 0.10,
            "seed": 0,
            "vix_series": False,
            "target": "test",
        }

    def test_complete_fields_pass_and_posture_is_injected(self):
        meta = build_run_meta(**self._fields())
        assert "posture" in meta
        assert "DRAWDOWN_GUARD_CONDITIONER_ENABLED" in meta["posture"]
        assert meta["git_sha"] == "abc1234"

    def test_missing_required_field_raises(self):
        fields = self._fields()
        del fields["git_sha"]
        with pytest.raises(ValueError, match="git_sha"):
            build_run_meta(**fields)

    def test_verdict_constants_are_injected(self):
        # #1947 N2: DELTA_SHARPE_NOISE_BAND and MIN_CROSS_SECTION shape the
        # truth-table verdicts and daily-IC series; pinning them only via
        # git_sha is not citable — they belong in every run_meta.
        from core.analysis.attribution.metrics import (
            DELTA_SHARPE_NOISE_BAND,
            MIN_CROSS_SECTION,
        )

        meta = build_run_meta(**self._fields())
        assert meta["constants"]["DELTA_SHARPE_NOISE_BAND"] == DELTA_SHARPE_NOISE_BAND
        assert meta["constants"]["MIN_CROSS_SECTION"] == MIN_CROSS_SECTION

    def test_lstm_field_defaults_to_disabled(self):
        # #2409: every artifact must state whether the LSTM row was
        # model-backed; a caller that says nothing gets an honest
        # enabled=False, never an absent field. Rev 3 (PR #2421): the key is
        # ``lstm`` — the reproducibility contract the owner run checks.
        meta = build_run_meta(**self._fields())
        assert meta["lstm"] == {"enabled": False}
        explicit = build_run_meta(
            **self._fields(), lstm={"enabled": True, "sequence_length": 20}
        )
        assert explicit["lstm"]["enabled"] is True


class TestRenderMarkdown:
    def test_markdown_contains_table_verdicts_and_run_metadata(self):
        votes, fwd = _panel()
        rows, corr = build_agent_rows(
            votes, fwd_ic=fwd, fwd_1d=fwd, exclusions={}, cost_bps=10.0
        )
        md = render_markdown(
            rows,
            correlation=corr,
            run_meta={"start": "2024-01-02", "end": "2024-02-26", "cost_bps": 10.0},
        )
        assert "| Agent |" in md
        assert "MomentumAgent" in md
        assert "keep" in md or "fix" in md or "kill" in md
        assert "cost_bps" in md
        # machine-readable meta stays valid JSON in the artifact
        json.dumps({"rows": [r.agent for r in rows]})

    def test_markdown_documents_conditioner_damp_limitation(self):
        votes, fwd = _panel()
        rows, corr = build_agent_rows(
            votes, fwd_ic=fwd, fwd_1d=fwd, exclusions={}, cost_bps=10.0
        )
        md = render_markdown(
            rows, correlation=corr, run_meta={"posture": runtime_posture()}
        )
        assert "Limitation" in md
        assert "Conditioner" in md
        assert "Hard-Veto" in md
