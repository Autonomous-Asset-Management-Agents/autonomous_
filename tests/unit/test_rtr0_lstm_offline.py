"""
test_rtr0_lstm_offline.py — RTR-0 Inc 3 (#2409): dedicated offline LSTM scorer.

Covered (plan #2409, Option B):
  * bundle loading — replicates the live loader (`_load_torch_model_assets`,
    core/strategies/lstm_strategy.py:143-219) including metadata sequence
    resolution, ensemble ``models.0.`` prefix stripping, checkpoint-driven
    feature truncation and scaler subsetting; missing artifacts raise LOUDLY
    (an operator who asked for LSTM scoring must never get a silent
    EXCLUDED row).
  * PARITY PIN (the mandatory gate of #2409): on identical fixture bars the
    offline score equals the live ranking-path score
    (`_get_torch_prediction`, lstm_strategy.py:234-371) within 1e-6 — and
    abstentions (short history) agree too.
  * cross-section — per-date scores over the panel bars, PIT-tradeable
    symbols only; the ADR-ML-COLLAPSE-01 guard (lstm_strategy.py:435-472)
    is replicated: a constant cross-section yields NO ranking.
  * replay integration — with the strategy injected via
    ``replay_votes(strategy=...)`` the LSTMSignalAgent row carries real
    opinions whose score is the REAL agents.py tanh mapping; the truth
    table measures LSTM instead of EXCLUDED; ``build_ranking_specs`` gets a
    non-empty 'lstm' ranking source (#2402 integration).
  * RLConfidenceAgent side effect — documented, production-faithful for a
    position-less book: it reads the SAME ``lstm_prediction`` field
    (agents.py:898) exactly as on Desktop where LSTMDynamic is the active
    strategy; its BUY/HOLD mappings are pinned here so drift is visible.
  * CLI — ``--lstm-artifacts`` wires the scorer into the benchmark and
    stamps ``run_meta.lstm`` (reproducibility, M2 discipline).
  * Rev 3 (PR #2421 owner repro) — intraday-stamped bars: the engine's real
    ``market_data_cache`` carries bars at 04:00/05:00 UTC, not midnight. The
    replay normalizes ``as_of`` to the calendar day, so the PIT slice
    ``index <= midnight`` excluded the as-of day's own bar, every symbol
    failed the tradeability gate and BOTH model agents abstained on 100% of
    bars — EXIT 0, no error. Pinned here Red->Green: the cross-section and
    the parity contract must hold on intraday-stamped bars, a full-abstention
    run under ``--lstm-artifacts`` must fail LOUDLY (exit 2), and the offline
    scores must feed the ``lstm`` book-quality ranking with provenance.
"""

from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pytest
import torch

from core.analysis.attribution.book_quality import build_ranking_specs
from core.analysis.attribution.lstm_offline import (
    LstmArtifactError,
    LstmOfflineScorer,
    OfflineLstmStrategy,
    load_lstm_bundle,
)
from core.analysis.attribution.metrics import opinion_mask
from core.analysis.attribution.replay import (
    ReplayBarsProvider,
    forward_returns,
    replay_votes,
)
from core.analysis.attribution.report import build_agent_rows
from core.round_table.agents import _LSTM_VOTE_SCALE


@pytest.fixture(autouse=True)
def _pin_preactivation_lstm_vote_path(monkeypatch):
    """RTR-0 OFFLINE-REPLAY attribution suite: these tests assert the per-symbol LSTM
    scoring `0.5 + 0.5·tanh(pred/scale)` (agents.py:773) over a fixture replay. ADR-018
    flipped LSTM_VOTE_USES_CROSS_SECTIONAL_RANK / ROUND_TABLE_DISTINCT_ML_SOURCES ON by
    default; under those the LSTMSignalAgent switches to the rank path (`_vote_on_rank`,
    agents.py:811), which reads the live panel store — empty in offline replay → EXCLUDED.
    The activated cross-sectional-rank path has its own coverage (test_round_table_lstm_rank_vote).
    Pin the flags OFF on the real config singleton (config.py:1408) so this replay keeps
    measuring the per-symbol attribution path it was written for."""
    import config

    cfg = config.get_config()
    monkeypatch.setattr(
        cfg, "LSTM_VOTE_USES_CROSS_SECTIONAL_RANK", False, raising=False
    )
    monkeypatch.setattr(cfg, "ROUND_TABLE_DISTINCT_ML_SOURCES", False, raising=False)


# The v1 repo-bundle feature set (data/model_metadata.json — 23 features, all
# produced by models.torch_model.create_live_features).
FEATURES_23 = [
    "log_ret",
    "volatility_20",
    "volatility_5",
    "returns_cumsum_5",
    "returns_std_5",
    "rsi_14",
    "rsi_7",
    "rsi_overbought",
    "rsi_oversold",
    "macd",
    "macd_signal",
    "macd_hist",
    "adx_14",
    "price_vs_sma20",
    "sma_slope_20",
    "bb_position",
    "bb_width",
    "volume_ratio",
    "volume_momentum",
    "high_low_ratio",
    "close_position",
    "gap",
    "momentum_vol_interaction",
]


# ---------------------------------------------------------------------------
# Fixture bundle — a tiny but structurally faithful model artifact set
# ---------------------------------------------------------------------------


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _standard_scaler(n: int, seed: int = 5):
    from sklearn.preprocessing import StandardScaler

    rng = np.random.default_rng(seed)
    sc = StandardScaler()
    sc.mean_ = rng.normal(0.0, 1.0, n)
    sc.scale_ = rng.uniform(0.5, 2.0, n)
    sc.var_ = sc.scale_**2
    sc.n_features_in_ = n
    return sc


def _write_bundle(
    dir_: Path,
    *,
    ensemble: bool = False,
    scaler_features: int = 23,
    metadata_features=None,
    metadata_input_dim: int = 23,
    zero_weights: bool = False,
    seed: int = 11,
) -> Path:
    """Write lstm_model.pth + scaler_x/y.pkl + model_metadata.json (+ manifest).

    Mirrors the shipped v1 bundle shape (seq 20, 23 features); ``ensemble``
    saves a ``models.0.``-prefixed state dict, ``scaler_features > 23``
    exercises the live loader's scaler-subsetting branch
    (lstm_strategy.py:204-216).
    """
    from models.torch_model import LSTMModel

    dir_.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(seed)
    model = LSTMModel(23, 8, 2, 1)
    if zero_weights:
        with torch.no_grad():
            for p in model.parameters():
                p.zero_()
    sd = model.state_dict()
    if ensemble:
        sd = {f"models.0.{k}": v for k, v in sd.items()}
    torch.save(sd, dir_ / "lstm_model.pth")

    base = _standard_scaler(23)
    if scaler_features > 23:
        big = _standard_scaler(scaler_features, seed=99)
        big.mean_[:23] = base.mean_
        big.scale_[:23] = base.scale_
        big.var_ = big.scale_**2
        scaler_x = big
    else:
        scaler_x = base
    joblib.dump(scaler_x, dir_ / "scaler_x.pkl")

    scaler_y = _standard_scaler(1, seed=7)
    scaler_y.mean_ = np.array([0.53658], dtype=np.float64)
    scaler_y.scale_ = np.array([0.49866], dtype=np.float64)
    scaler_y.var_ = scaler_y.scale_**2
    joblib.dump(scaler_y, dir_ / "scaler_y.pkl")

    metadata = {
        "features_list": list(metadata_features or FEATURES_23),
        "sequence_length": 20,
        "model_params": {
            "input_dim": metadata_input_dim,
            "hidden_dim": 8,
            "num_layers": 2,
            "output_dim": 1,
        },
    }
    (dir_ / "model_metadata.json").write_text(json.dumps(metadata))

    # asset-integrity manifest beside the assets (core/ml/asset_integrity.py:37-44)
    manifest = {
        "models": [
            {"filename": name, "sha256": _sha256(dir_ / name)}
            for name in ("lstm_model.pth", "scaler_x.pkl", "scaler_y.pkl")
        ]
    }
    (dir_ / "models_manifest.json").write_text(json.dumps(manifest))
    return dir_


def _bars(n: int = 120, start: str = "2024-01-02", seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=n)
    close = 100.0 + np.cumsum(rng.normal(0.05, 1.0, n))
    open_ = close + rng.normal(0, 0.3, n)
    high = np.maximum(open_, close) + rng.uniform(0.0, 0.8, n)
    low = np.minimum(open_, close) - rng.uniform(0.0, 0.8, n)
    vol = rng.integers(1_000_000, 5_000_000, n).astype(float)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": vol},
        index=idx,
    )


@pytest.fixture(scope="module")
def bundle_dir(tmp_path_factory) -> Path:
    return _write_bundle(tmp_path_factory.mktemp("lstm_bundle"))


@pytest.fixture(scope="module")
def bars_dir(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("bars")
    for sym, seed in (("AAA", 3), ("BBB", 4)):
        _bars(seed=seed).to_parquet(d / f"{sym}.parquet")
    return d


@pytest.fixture()
def provider(bars_dir) -> ReplayBarsProvider:
    return ReplayBarsProvider(bars_dir)


@pytest.fixture()
def scorer(bundle_dir) -> LstmOfflineScorer:
    return LstmOfflineScorer(load_lstm_bundle(bundle_dir))


# ---------------------------------------------------------------------------
# Bundle loading
# ---------------------------------------------------------------------------


class TestBundleLoading:
    def test_bundle_carries_metadata_shape(self, bundle_dir):
        bundle = load_lstm_bundle(bundle_dir)
        assert bundle.sequence_length == 20
        assert bundle.input_dim == 23
        assert bundle.features_list == FEATURES_23
        assert not bundle.model.training  # eval mode, like the live loader

    def test_missing_artifact_raises_loudly(self, tmp_path):
        # live loader logs+returns (engine must not crash); the OFFLINE tool
        # fails LOUDLY — the operator explicitly asked for LSTM scoring.
        with pytest.raises(LstmArtifactError, match="lstm_model.pth"):
            load_lstm_bundle(tmp_path)

    def test_ensemble_prefix_and_scaler_subset_match_plain_bundle(
        self, tmp_path, bundle_dir, provider
    ):
        # v1-realistic edge cases: models.0.-prefixed checkpoint + oversized
        # scaler + oversized metadata feature list -> identical predictions
        # (the loader replicates lstm_strategy.py:171-216 exactly).
        variant = _write_bundle(
            tmp_path / "variant",
            ensemble=True,
            scaler_features=25,
            metadata_features=FEATURES_23 + ["vix", "vix_change"],
            metadata_input_dim=25,
        )
        plain = LstmOfflineScorer(load_lstm_bundle(bundle_dir))
        varc = LstmOfflineScorer(load_lstm_bundle(variant))
        ts = provider.frames["AAA"].index[-1]
        p1 = plain.predict(provider, "AAA", ts)
        p2 = varc.predict(provider, "AAA", ts)
        assert p1 is not None and p2 is not None
        assert abs(p1 - p2) <= 1e-6


# ---------------------------------------------------------------------------
# PARITY — the mandatory pin of #2409 (offline == live ranking path, ±1e-6)
# ---------------------------------------------------------------------------


def _live_strategy(monkeypatch, bundle_dir: Path, provider):
    """The REAL LSTMDynamicStrategy loader + ranking path on the fixture
    bundle: ``__new__`` skips only the BaseStrategy plumbing (client/risk
    manager), then the genuine ``_load_torch_model_assets`` and
    ``_get_torch_prediction`` run unmodified."""
    import models.torch_model as tm
    from core.strategies.lstm_strategy import SEQUENCE_LENGTH, LSTMDynamicStrategy

    s = LSTMDynamicStrategy.__new__(LSTMDynamicStrategy)
    s.torch = torch
    s.np = np
    s.pd = pd
    s.joblib = joblib
    s.device = torch.device("cpu")
    s.torch_model = None
    s.scaler_x = None
    s.scaler_y = None
    s.features_list = []
    s.sequence_length = SEQUENCE_LENGTH
    monkeypatch.setattr(
        tm,
        "get_lstm_paths",
        lambda version=None: (
            str(bundle_dir / "lstm_model.pth"),
            str(bundle_dir / "scaler_x.pkl"),
            str(bundle_dir / "scaler_y.pkl"),
            str(bundle_dir / "model_metadata.json"),
        ),
    )
    s._load_torch_model_assets()
    assert s.torch_model is not None, "live loader failed on the fixture bundle"
    s.client = object()  # not a SimulationAdapter -> data_provider path
    s.data_provider = provider
    return s


class TestLiveParityPin:
    def test_offline_score_equals_live_ranking_path(
        self, monkeypatch, bundle_dir, provider, scorer
    ):
        live = _live_strategy(monkeypatch, bundle_dir, provider)
        checked = 0
        for symbol in ("AAA", "BBB"):
            for ts in provider.frames[symbol].index[[-1, -10, -25]]:
                live_pred, _ = asyncio.run(
                    live._get_torch_prediction(symbol, ts.to_pydatetime(), {})
                )
                off_pred = scorer.predict(provider, symbol, ts)
                assert live_pred is not None and off_pred is not None
                assert abs(off_pred - live_pred) <= 1e-6, (
                    f"parity broken for {symbol}@{ts.date()}: "
                    f"offline={off_pred!r} live={live_pred!r}"
                )
                checked += 1
        assert checked == 6

    def test_abstention_parity_on_short_history(
        self, monkeypatch, bundle_dir, provider, scorer
    ):
        # date ~40 bars in => < FEATURE_WARMUP_ROWS rows of history: the live
        # path abstains (returns (None, None), lstm_strategy.py:289-298) and
        # the offline scorer must abstain identically — never emit a number.
        live = _live_strategy(monkeypatch, bundle_dir, provider)
        ts = provider.frames["AAA"].index[40]
        live_pred, _ = asyncio.run(
            live._get_torch_prediction("AAA", ts.to_pydatetime(), {})
        )
        assert live_pred is None
        assert scorer.predict(provider, "AAA", ts) is None


# ---------------------------------------------------------------------------
# Cross-section
# ---------------------------------------------------------------------------


class TestCrossSection:
    def test_scores_every_tradeable_symbol_at_date(self, provider, scorer):
        ts = provider.frames["AAA"].index[-1]
        xsec = scorer.cross_section(provider, ts)
        assert set(xsec) == {"AAA", "BBB"}
        assert all(isinstance(v, float) for v in xsec.values())

    def test_symbol_without_bar_at_date_is_not_scored(self, tmp_path, scorer):
        # PIT tradeability: CCC's series ends 30 bars early — at the ranking
        # date it has no bar and must not enter the cross-section.
        for sym, seed in (("AAA", 3), ("CCC", 5)):
            df = _bars(seed=seed)
            if sym == "CCC":
                df = df.iloc[:-30]
            df.to_parquet(tmp_path / f"{sym}.parquet")
        prov = ReplayBarsProvider(tmp_path)
        ts = prov.frames["AAA"].index[-1]
        xsec = scorer.cross_section(prov, ts)
        assert "AAA" in xsec and "CCC" not in xsec

    def test_collapse_guard_yields_empty_ranking(self, tmp_path, provider):
        # ADR-ML-COLLAPSE-01 replicated: a zero-weight model emits one
        # constant prediction for every name -> the "ranking" would be the
        # input order -> NO cross-section (lstm_strategy.py:435-472).
        collapsed_dir = _write_bundle(tmp_path / "collapsed", zero_weights=True)
        collapsed = LstmOfflineScorer(load_lstm_bundle(collapsed_dir))
        ts = provider.frames["AAA"].index[-1]
        assert collapsed.cross_section(provider, ts) == {}


# ---------------------------------------------------------------------------
# Replay integration — LSTM row in the truth table + 'lstm' ranking source
# ---------------------------------------------------------------------------


@pytest.fixture()
def replayed(provider, scorer):
    strategy = OfflineLstmStrategy(provider, scorer)
    return asyncio.run(
        replay_votes(provider, start="2024-05-27", end="2024-06-14", strategy=strategy)
    )


class TestReplayIntegration:
    def test_lstm_votes_are_real_opinions_with_agent_mapping(
        self, provider, scorer, replayed
    ):
        lstm = replayed.votes[replayed.votes["agent"] == "LSTMSignalAgent"]
        opinions = lstm[opinion_mask(lstm)]
        assert len(opinions) == len(lstm)  # every replayed bar got a score
        assert (opinions["weight"] > 0).all()
        # score pins the REAL agents.py mapping (agents.py:773-774)
        row = opinions.iloc[0]
        pred = scorer.predict(provider, row["symbol"], row["ts"])
        expected = 0.5 + 0.5 * math.tanh(pred / _LSTM_VOTE_SCALE)
        assert abs(row["score"] - expected) <= 1e-9

    def test_truth_table_measures_lstm_instead_of_excluded(self, provider, replayed):
        fwd_ic = forward_returns(provider, horizon=5)
        fwd_1d = forward_returns(provider, horizon=1)
        rows, _ = build_agent_rows(
            replayed.votes,
            fwd_ic=fwd_ic,
            fwd_1d=fwd_1d,
            exclusions=replayed.exclusions,
            cost_bps=10.0,
        )
        lstm_row = next(r for r in rows if r.agent == "LSTMSignalAgent")
        assert lstm_row.classification != "EXCLUDED"
        assert lstm_row.n_opinions > 0
        assert lstm_row.ic is not None

    def test_book_quality_lstm_ranking_source_becomes_available(self, replayed):
        specs = build_ranking_specs(replayed.votes)
        lstm_spec = next(s for s in specs if s.source == "lstm")
        assert not lstm_spec.panel.empty
        assert lstm_spec.unavailable_reason == ""

    def test_rl_confidence_side_effect_is_pinned(self, provider, scorer):
        # Production-faithful for a position-less book: RLConfidenceAgent
        # reads the SAME lstm_prediction (agents.py:898) from the active
        # strategy. top_n=1 forces one BUY (rank 1) and one HOLD per date —
        # both mappings (agents.py:900-908) are pinned so drift is visible.
        strategy = OfflineLstmStrategy(provider, scorer, top_n=1)
        result = asyncio.run(
            replay_votes(
                provider, start="2024-06-10", end="2024-06-12", strategy=strategy
            )
        )
        rl = result.votes[result.votes["agent"] == "RLConfidenceAgent"]
        # ADR-018: the RL vote is MUTED (RL_CONFIDENCE_WEIGHT default → 0.0, import-time
        # via agents.py:296) — it still COMPUTES a score off the shared lstm_prediction
        # (the echo read is unchanged; the score assertions below still pin it) but carries
        # weight 0.0, so it is excluded from the consensus mean. Before activation: weight > 0.
        assert (rl["weight"] == 0.0).all()
        ts = rl["ts"].iloc[0]
        xsec = scorer.cross_section(provider, ts)
        ranked = sorted(xsec.items(), key=lambda kv: kv[1], reverse=True)
        buy_sym, buy_pred = ranked[0]
        hold_sym, hold_pred = ranked[1]
        day = rl[rl["ts"] == ts].set_index("symbol")["score"]
        assert abs(day[buy_sym] - (0.5 + 0.5 * math.tanh(buy_pred / 2.0))) <= 1e-9
        assert abs(day[hold_sym] - (0.5 + 0.5 * math.tanh(hold_pred / 2.0))) <= 1e-9

    def test_abstaining_default_strategy_still_yields_excluded_row(self, provider):
        # Regression: WITHOUT artifacts the honest abstention path of Inc 1
        # stays intact (no fabricated LSTM opinions).
        result = asyncio.run(
            replay_votes(provider, start="2024-06-10", end="2024-06-12")
        )
        lstm = result.votes[result.votes["agent"] == "LSTMSignalAgent"]
        assert (lstm["weight"] == 0.0).all()


# ---------------------------------------------------------------------------
# CLI — --lstm-artifacts wiring + run_meta stamp
# ---------------------------------------------------------------------------


class TestCliWiring:
    def test_cli_lstm_artifacts_measures_lstm_and_stamps_run_meta(
        self, tmp_path, monkeypatch, bundle_dir, bars_dir
    ):
        import importlib
        import sys

        scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
        monkeypatch.syspath_prepend(str(scripts_dir))
        cli = importlib.import_module("rtr0_attribution_benchmark")
        out = tmp_path / "out"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "rtr0_attribution_benchmark.py",
                "--data-dir",
                str(bars_dir),
                "--start",
                "2024-06-10",
                "--end",
                "2024-06-14",
                "--lstm-artifacts",
                str(bundle_dir),
                "--out",
                str(out),
            ],
        )
        assert cli.main() == 0
        payload = json.loads((out / "RESULTS.json").read_text(encoding="utf-8"))
        meta = payload["run_meta"]["lstm"]
        assert meta["enabled"] is True
        assert meta["sequence_length"] == 20
        lstm_row = next(a for a in payload["agents"] if a["agent"] == "LSTMSignalAgent")
        assert lstm_row["classification"] != "EXCLUDED"

    def test_cli_fails_loudly_on_missing_artifacts(
        self, tmp_path, monkeypatch, bars_dir
    ):
        import importlib
        import sys

        scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
        monkeypatch.syspath_prepend(str(scripts_dir))
        cli = importlib.import_module("rtr0_attribution_benchmark")
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "rtr0_attribution_benchmark.py",
                "--data-dir",
                str(bars_dir),
                "--start",
                "2024-06-10",
                "--end",
                "2024-06-14",
                "--lstm-artifacts",
                str(tmp_path / "nowhere"),
                "--out",
                str(tmp_path / "out"),
            ],
        )
        assert cli.main() == 2


# ---------------------------------------------------------------------------
# Rev 3 (PR #2421) — intraday-stamped bars: the owner-repro regression
# ---------------------------------------------------------------------------


def _bars_intraday(
    n: int = 120, start: str = "2024-01-02", seed: int = 3, hour: int = 5
) -> pd.DataFrame:
    """Bars stamped like the engine's real ``market_data_cache``: the daily
    bar of calendar day D carries an INTRADAY UTC timestamp (04:00 EDT /
    05:00 EST), never midnight. The Red state of Rev 3: a midnight-normalized
    ``as_of`` slice excludes day D's own bar."""
    df = _bars(n=n, start=start, seed=seed)
    df.index = df.index + pd.Timedelta(hours=hour)
    return df


@pytest.fixture(scope="module")
def intraday_bars_dir(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("bars_intraday")
    for sym, seed in (("AAA", 3), ("BBB", 4)):
        _bars_intraday(seed=seed).to_parquet(d / f"{sym}.parquet")
    return d


@pytest.fixture()
def intraday_provider(intraday_bars_dir) -> ReplayBarsProvider:
    return ReplayBarsProvider(intraday_bars_dir)


class TestIntradayTimestampPit:
    """Owner repro 2026-07-23 (PR #2421 Rev 3): 195 votes / 0 opinions at
    EXIT 0 on the real panel cache — root cause is the PIT day boundary, not
    the model. These tests run the same code path on intraday-stamped bars."""

    def test_cross_section_includes_the_as_of_days_own_bar(
        self, intraday_provider, scorer
    ):
        # the replay hands the strategy a CALENDAR day (normalized midnight);
        # the day's bar sits at 05:00 — it must still be tradeable that day.
        as_of = intraday_provider.frames["AAA"].index[-1].normalize()
        xsec = scorer.cross_section(intraday_provider, as_of)
        assert set(xsec) == {"AAA", "BBB"}

    def test_predict_parity_live_ts_vs_replay_calendar_day(
        self, monkeypatch, bundle_dir, intraday_provider, scorer
    ):
        # live ranks with wall-clock >= the bar's own timestamp; the replay
        # only has the calendar day. Same day => same score (±1e-6).
        live = _live_strategy(monkeypatch, bundle_dir, intraday_provider)
        ts = intraday_provider.frames["AAA"].index[-1]
        live_pred, _ = asyncio.run(
            live._get_torch_prediction("AAA", ts.to_pydatetime(), {})
        )
        off_pred = scorer.predict(intraday_provider, "AAA", ts.normalize())
        assert live_pred is not None and off_pred is not None
        assert abs(off_pred - live_pred) <= 1e-6

    def test_replay_on_intraday_bars_yields_real_lstm_opinions(
        self, intraday_provider, scorer
    ):
        strategy = OfflineLstmStrategy(intraday_provider, scorer)
        result = asyncio.run(
            replay_votes(
                intraday_provider,
                start="2024-05-27",
                end="2024-06-14",
                strategy=strategy,
            )
        )
        lstm = result.votes[result.votes["agent"] == "LSTMSignalAgent"]
        opinions = lstm[opinion_mask(lstm)]
        assert len(lstm) > 0
        assert len(opinions) == len(lstm)  # every replayed bar got a score

    def test_score_panel_feeds_lstm_ranking_with_provenance(
        self, intraday_provider, scorer
    ):
        # Rev 3 fix 2: the book-quality 'lstm' ranking consumes the offline
        # scorer's raw per-date cross-sections, labeled with its provenance.
        strategy = OfflineLstmStrategy(intraday_provider, scorer)
        asyncio.run(
            replay_votes(
                intraday_provider,
                start="2024-06-10",
                end="2024-06-14",
                strategy=strategy,
            )
        )
        panel = strategy.score_panel()
        assert list(panel.columns) == ["ts", "symbol", "score"]
        assert not panel.empty
        specs = build_ranking_specs(
            pd.DataFrame(columns=["ts", "symbol", "agent", "score", "weight"]),
            lstm_panel=panel,
            lstm_basis="offline scorer, parity-pinned",
        )
        lstm_spec = next(s for s in specs if s.source == "lstm")
        assert lstm_spec.unavailable_reason == ""
        assert lstm_spec.basis == "offline scorer, parity-pinned"
        pd.testing.assert_frame_equal(lstm_spec.panel, panel)

    def test_cli_e2e_intraday_bars_full_rev3_contract(
        self, tmp_path, monkeypatch, bundle_dir, intraday_bars_dir
    ):
        # End-to-end mini replay (fixture bundle + intraday bars): the LSTM
        # row measures (n_opinions > 0), run_meta.lstm is stamped and the
        # book_quality 'lstm' ranking is present with offline provenance.
        import importlib
        import sys

        scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
        monkeypatch.syspath_prepend(str(scripts_dir))
        cli = importlib.import_module("rtr0_attribution_benchmark")
        out = tmp_path / "out"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "rtr0_attribution_benchmark.py",
                "--data-dir",
                str(intraday_bars_dir),
                "--start",
                "2024-06-03",
                "--end",
                "2024-06-14",
                "--lstm-artifacts",
                str(bundle_dir),
                "--book-quality",
                "--out",
                str(out),
            ],
        )
        assert cli.main() == 0
        payload = json.loads((out / "RESULTS.json").read_text(encoding="utf-8"))
        lstm_row = next(a for a in payload["agents"] if a["agent"] == "LSTMSignalAgent")
        assert lstm_row["n_opinions"] > 0
        assert lstm_row["classification"] != "EXCLUDED"
        meta = payload["run_meta"]["lstm"]
        assert meta["enabled"] is True
        assert meta["sequence_length"] == 20
        assert meta["n_features"] == 23
        bq_lstm = payload["book_quality"]["rankings"]["lstm"]
        assert bq_lstm["available"] is True
        assert bq_lstm["basis"] == "offline scorer, parity-pinned"
        md = (out / "RESULTS.md").read_text(encoding="utf-8")
        assert "lstm (offline scorer, parity-pinned)" in md

    def test_cli_fails_loudly_when_lstm_abstains_on_every_bar(
        self, tmp_path, monkeypatch, bundle_dir, capsys
    ):
        # The owner-repro failure class: artifacts requested, replay runs,
        # but every score abstains (here: bars < feature warm-up). That must
        # be a LOUD exit 2 — never an EXIT-0 artifact with a silent
        # 100%-abstention LSTM row.
        import importlib
        import sys

        short_dir = tmp_path / "short_bars"
        short_dir.mkdir()
        for sym, seed in (("AAA", 3), ("BBB", 4)):
            _bars(n=40, seed=seed).to_parquet(short_dir / f"{sym}.parquet")
        scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
        monkeypatch.syspath_prepend(str(scripts_dir))
        cli = importlib.import_module("rtr0_attribution_benchmark")
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "rtr0_attribution_benchmark.py",
                "--data-dir",
                str(short_dir),
                "--start",
                "2024-02-19",
                "--end",
                "2024-02-23",
                "--lstm-artifacts",
                str(bundle_dir),
                "--out",
                str(tmp_path / "out"),
            ],
        )
        assert cli.main() == 2
        assert "0 opinions" in capsys.readouterr().out
