# Copyright 2026 Andreas Apeldorn, Georg Apeldorn / Autonomous Asset Management Agents UG
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""TDD — #1956 Inc 0: v2-vs-v3 kill-gate benchmark harness.

The real archive models (C:/Users/andre/models-archive/rl-lineage/) are NOT a
test dependency: every model-touching test builds a throwaway mini RecurrentPPO
zip of the same class/obs contract, so CI never needs local artifacts.

Era parity anchors (archived era code; v2 = trading_environment.py, v3 =
DSRTradingEnv in train_rl_dsr.py — obs construction identical, see module
docstring of rl_candidate_bench.py):
  * 8 market features uniformly clipped to +-10 (:222) — RSI/ADX saturate,
    and the benchmark MUST replicate that saturation (the policies were
    trained on it), NOT the live rsi/100 scale (rl_signal.py:721/:733).
  * position block: position (0/1), time_in_position = min(days/20, 1)
    (:229-233), unrealized_pnl clipped +-0.5 (:236-247).
  * v3-only 12th feature: volatility regime score 0/0.33/0.67/1.0 with
    thresholds 0.015/0.025/0.04 (trading_environment.py:71, :249-265 ==
    train_rl_dsr.py:240-291, the ACTUAL v3 training env).
  * action semantics long/flat: 1=BUY enters if flat, 2=SELL exits if held,
    0=HOLD keeps (:346-407).
"""

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from core.analysis.attribution.metrics import portfolio_sharpe
from core.analysis.attribution.rl_candidate_bench import (
    ERA_FEATURE_COLUMNS,
    EraObsAdapter,
    VecNormalizeStats,
    _daily_net_returns,
    buy_and_hold_positions,
    era_features_from_bars,
    evaluate_candidates,
    kill_gate_verdict,
    replay_candidate,
    run_benchmark,
    verify_model_hash,
)
from core.ml.deflated_sharpe import deflated_sharpe_ratio, sharpe_moments

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _synthetic_bars(n: int = 200, seed: int = 7) -> pd.DataFrame:
    """Deterministic OHLCV random walk, business-day index."""
    rng = np.random.default_rng(seed)
    close = 100.0 * np.cumprod(1.0 + rng.normal(0.0004, 0.015, n))
    idx = pd.bdate_range("2024-01-02", periods=n)
    return pd.DataFrame(
        {
            "open": close * (1 + rng.normal(0, 0.002, n)),
            "high": close * (1 + np.abs(rng.normal(0, 0.004, n))),
            "low": close * (1 - np.abs(rng.normal(0, 0.004, n))),
            "close": close,
            "volume": rng.integers(1_000_000, 5_000_000, n).astype(float),
        },
        index=idx,
    )


def _era_row(**overrides) -> pd.Series:
    base = {
        "returns": 0.01,
        "rsi_14": 55.0,
        "macd": 0.2,
        "bb_pct": 0.5,
        "volume_ratio": 1.2,
        "volatility_20d": 0.02,
        "momentum_10d": 0.03,
        "adx_14": 25.0,
        "Close": 100.0,
    }
    base.update(overrides)
    return pd.Series(base)


def _make_mini_model_zip(tmp_path: Path, obs_dim: int, name: str) -> Path:
    """Save a real (untrained) RecurrentPPO of the archive obs contract."""
    import gymnasium as gym
    from gymnasium import spaces
    from sb3_contrib import RecurrentPPO

    class _NullEnv(gym.Env):
        def __init__(self):
            super().__init__()
            self.observation_space = spaces.Box(-np.inf, np.inf, (obs_dim,), np.float32)
            self.action_space = spaces.Discrete(3)

        def reset(self, *, seed=None, options=None):
            super().reset(seed=seed)
            return np.zeros(obs_dim, np.float32), {}

        def step(self, action):
            return np.zeros(obs_dim, np.float32), 0.0, True, False, {}

    model = RecurrentPPO(
        "MlpLstmPolicy",
        _NullEnv(),
        n_steps=8,
        batch_size=8,
        policy_kwargs={"lstm_hidden_size": 8, "net_arch": [8]},
        seed=0,
        device="cpu",
    )
    path = tmp_path / name
    model.save(path)
    return path.with_suffix(".zip")


def _write_sha256sums(tmp_path: Path, *files: Path) -> Path:
    lines = []
    for f in files:
        digest = hashlib.sha256(f.read_bytes()).hexdigest()
        lines.append(f"{digest} *{f.name}")
    out = tmp_path / "SHA256SUMS.txt"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


class _ScriptedModel:
    """Deterministic stub mirroring RecurrentPPO.predict (cf. test_rl_execution.py:478)."""

    def __init__(self, actions):
        self.actions = list(actions)
        self.calls = 0
        self.seen_obs_dims = set()

    def predict(self, obs, state=None, episode_start=None, deterministic=True):
        assert deterministic is True
        self.seen_obs_dims.add(np.asarray(obs).shape[-1])
        action = self.actions[self.calls % len(self.actions)]
        self.calls += 1
        return np.int64(action), state


# ---------------------------------------------------------------------------
# Hash verification (models stay OUT of the repo — path + hash only)
# ---------------------------------------------------------------------------


class TestVerifyModelHash:
    def test_ok_returns_digest(self, tmp_path):
        f = tmp_path / "model.zip"
        f.write_bytes(b"payload")
        sums = _write_sha256sums(tmp_path, f)
        digest = verify_model_hash(f, sums)
        assert digest == hashlib.sha256(b"payload").hexdigest()

    def test_mismatch_raises(self, tmp_path):
        f = tmp_path / "model.zip"
        f.write_bytes(b"payload")
        sums = _write_sha256sums(tmp_path, f)
        f.write_bytes(b"tampered")
        with pytest.raises(ValueError, match="SHA256 mismatch"):
            verify_model_hash(f, sums)

    def test_missing_entry_raises(self, tmp_path):
        f = tmp_path / "model.zip"
        f.write_bytes(b"payload")
        other = tmp_path / "other.zip"
        other.write_bytes(b"x")
        sums = _write_sha256sums(tmp_path, other)
        with pytest.raises(ValueError, match="no SHA256SUMS entry"):
            verify_model_hash(f, sums)


# ---------------------------------------------------------------------------
# Era observation adapter (reconstructed, NOT the live rl_signal builder)
# ---------------------------------------------------------------------------


class TestEraObsAdapter:
    def test_v2_is_11_dim_with_uniform_clip(self):
        adapter = EraObsAdapter("v2")
        assert adapter.obs_dim == 11
        obs = adapter.build(
            _era_row(rsi_14=90.0, adx_14=45.0), position=0, days_held=0, entry_price=0.0
        )
        assert obs.shape == (11,)
        assert obs.dtype == np.float32
        # Era saturation replicated: clip(90, -10, 10) == clip(45, ...) == 10
        assert obs[1] == pytest.approx(10.0)
        assert obs[7] == pytest.approx(10.0)
        # Sub-clip features pass through untouched
        assert obs[0] == pytest.approx(0.01)
        assert obs[3] == pytest.approx(0.5)

    def test_nan_market_feature_becomes_zero(self):
        adapter = EraObsAdapter("v2")
        obs = adapter.build(
            _era_row(macd=float("nan")), position=0, days_held=0, entry_price=0.0
        )
        assert obs[2] == 0.0

    def test_v3_regime_thresholds(self):
        adapter = EraObsAdapter("v3")
        assert adapter.obs_dim == 12
        expected = {0.010: 0.0, 0.020: 0.33, 0.030: 0.67, 0.050: 1.0}
        for vol, score in expected.items():
            obs = adapter.build(
                _era_row(volatility_20d=vol), position=0, days_held=0, entry_price=0.0
            )
            assert obs.shape == (12,)
            assert obs[11] == pytest.approx(score)

    def test_position_block(self):
        adapter = EraObsAdapter("v2")
        flat = adapter.build(_era_row(), position=0, days_held=0, entry_price=0.0)
        assert list(flat[8:11]) == [0.0, 0.0, 0.0]

        held = adapter.build(
            _era_row(Close=105.0), position=1, days_held=10, entry_price=100.0
        )
        assert held[8] == 1.0
        assert held[9] == pytest.approx(0.5)  # 10 / 20
        assert held[10] == pytest.approx(0.05)

        capped = adapter.build(
            _era_row(Close=300.0), position=1, days_held=40, entry_price=100.0
        )
        assert capped[9] == 1.0  # time cap
        assert capped[10] == pytest.approx(0.5)  # pnl clip

    def test_unknown_variant_raises(self):
        with pytest.raises(ValueError, match="variant"):
            EraObsAdapter("v6")


# ---------------------------------------------------------------------------
# Feature panel — era column mapping over repo indicator SSOT
# ---------------------------------------------------------------------------


class TestEraFeaturesFromBars:
    def test_mapping_matches_prepare_training_data_lineage(self):
        from models.torch_model import create_live_features

        bars = _synthetic_bars()
        feats = era_features_from_bars(bars)
        assert feats is not None
        for col in ERA_FEATURE_COLUMNS + ["Close"]:
            assert col in feats.columns
        raw = create_live_features(bars.copy())
        joined = feats.join(raw[["log_ret", "volatility_20", "returns_cumsum_5"]])
        # prepare_training_data.py:138-148 mapping — the surviving era lineage
        assert np.allclose(joined["returns"], joined["log_ret"], equal_nan=True)
        assert np.allclose(
            joined["volatility_20d"], joined["volatility_20"], equal_nan=True
        )
        assert np.allclose(
            joined["momentum_10d"], joined["returns_cumsum_5"], equal_nan=True
        )
        assert np.allclose(feats["Close"], bars.loc[feats.index, "close"])

    def test_missing_ohlcv_returns_none(self):
        df = pd.DataFrame({"close": [1.0, 2.0]})
        assert era_features_from_bars(df) is None


# ---------------------------------------------------------------------------
# Deterministic replay
# ---------------------------------------------------------------------------


class TestReplay:
    def test_scripted_action_semantics(self):
        frames = {"AAA": era_features_from_bars(_synthetic_bars(120))}
        model = _ScriptedModel([1, 0, 2, 2, 1])  # BUY HOLD SELL SELL(noop) BUY ...
        positions, info = replay_candidate(model, EraObsAdapter("v2"), frames)
        pos = positions[positions["symbol"] == "AAA"]["position"].tolist()
        # cycle of 5: 1(buy) 1(hold) 0(sell) 0(sell in cash -> noop) 1(buy)
        assert pos[:5] == [1, 1, 0, 0, 1]
        assert set(pos) <= {0, 1}
        assert info["AAA"]["n_trades"] >= 2
        assert model.seen_obs_dims == {11}

    def test_recurrentppo_replay_is_deterministic(self, tmp_path):
        from sb3_contrib import RecurrentPPO

        zip_path = _make_mini_model_zip(tmp_path, 11, "mini_v2")
        model = RecurrentPPO.load(zip_path, device="cpu")
        frames = {"AAA": era_features_from_bars(_synthetic_bars(120))}
        run1, _ = replay_candidate(model, EraObsAdapter("v2"), frames)
        run2, _ = replay_candidate(model, EraObsAdapter("v2"), frames)
        pd.testing.assert_frame_equal(run1, run2)
        assert set(run1["position"].unique()) <= {0, 1}

    def test_window_slicing(self):
        frames = {"AAA": era_features_from_bars(_synthetic_bars(120))}
        start, end = "2024-04-01", "2024-05-01"
        positions, _ = replay_candidate(
            _ScriptedModel([0]), EraObsAdapter("v2"), frames, start=start, end=end
        )
        assert positions["ts"].min() >= pd.Timestamp(start)
        assert positions["ts"].max() <= pd.Timestamp(end)

    def test_buy_and_hold_baseline_is_all_one(self):
        frames = {"AAA": era_features_from_bars(_synthetic_bars(120))}
        positions = buy_and_hold_positions(frames)
        assert (positions["position"] == 1).all()
        assert set(positions.columns) == {"ts", "symbol", "position"}


# ---------------------------------------------------------------------------
# VecNormalize serving parity (rl_signal.py:398-405 formula)
# ---------------------------------------------------------------------------


class TestVecNormalizeStats:
    def test_normalize_matches_live_formula(self, tmp_path):
        import gymnasium as gym
        from gymnasium import spaces
        from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize

        class _Env(gym.Env):
            def __init__(self):
                super().__init__()
                self.observation_space = spaces.Box(-np.inf, np.inf, (11,), np.float32)
                self.action_space = spaces.Discrete(3)

            def reset(self, *, seed=None, options=None):
                super().reset(seed=seed)
                return np.ones(11, np.float32), {}

            def step(self, action):
                return np.ones(11, np.float32), 0.0, True, False, {}

        venv = VecNormalize(DummyVecEnv([_Env]), norm_obs=True, clip_obs=10.0)
        venv.obs_rms.mean = np.full(11, 2.0)
        venv.obs_rms.var = np.full(11, 4.0)
        stats_path = tmp_path / "rl_stats_v2.pkl"
        venv.save(str(stats_path))

        stats = VecNormalizeStats.load(stats_path)
        raw = np.full(11, 6.0, dtype=np.float32)
        expected = np.clip((raw - 2.0) / np.sqrt(4.0 + 1e-8), -10.0, 10.0)
        assert np.allclose(stats.normalize(raw), expected)


# ---------------------------------------------------------------------------
# Metrics wiring — net Sharpe and DSR MUST come from the merged modules
# ---------------------------------------------------------------------------


def _toy_positions_and_fwd():
    ts = pd.bdate_range("2024-01-02", periods=60)
    rng = np.random.default_rng(3)
    fwd = pd.DataFrame(
        {"ts": ts, "symbol": "AAA", "fwd_ret": rng.normal(0.001, 0.01, 60)}
    )
    hold = pd.DataFrame({"ts": ts, "symbol": "AAA", "position": 1})
    alt = pd.DataFrame({"ts": ts, "symbol": "AAA", "position": (np.arange(60) % 2)})
    return hold, alt, fwd


class TestEvaluateCandidates:
    def test_net_sharpe_equals_metrics_portfolio_sharpe(self):
        """Review Finding 2 (Rev 2): pin the LOCAL net-return copy against the
        metrics-SSOT convention — NOT ``evaluate_candidates`` against itself.

        ``_daily_net_returns`` (the DSR input) duplicates the net-return
        convention of ``portfolio_sharpe`` (metrics.py). The old assertion
        compared ``evaluate_candidates()["net_sharpe"]`` with
        ``portfolio_sharpe()`` — tautological, because ``evaluate_candidates``
        CALLS ``portfolio_sharpe`` for that field. This pin recomputes the
        Sharpe from ``_daily_net_returns`` itself (mean/std(ddof=1)*sqrt(252),
        the documented metrics.py convention) and requires it to equal
        ``portfolio_sharpe().sharpe`` to 1e-9 — if either side ever changes
        convention (e.g. a future PR touching metrics.py), this fails LOUDLY
        instead of the gate JSON silently mixing two conventions.
        """
        hold, alt, fwd = _toy_positions_and_fwd()
        results = evaluate_candidates(
            {"rl_agent_v2": alt, "baseline_buy_hold": hold},
            fwd,
            cost_bps=10.0,
            model_names=["rl_agent_v2"],
        )
        direct = portfolio_sharpe(alt, fwd, cost_bps=10.0)
        assert results["rl_agent_v2"]["net_sharpe"] == pytest.approx(direct.sharpe)
        assert results["rl_agent_v2"]["total_cost"] == pytest.approx(direct.total_cost)
        assert results["rl_agent_v2"]["n_days"] == direct.n_days

        # The non-tautological pin: Sharpe recomputed from the local daily
        # series must equal the metrics-SSOT Sharpe (same annualisation,
        # same ddof) — this is the divergence guard the docstring claims.
        daily = _daily_net_returns(alt, fwd, cost_bps=10.0)
        assert int(daily.size) == direct.n_days
        recomputed = float(daily.mean()) / float(daily.std(ddof=1)) * np.sqrt(252.0)
        assert recomputed == pytest.approx(direct.sharpe, abs=1e-9)

    def test_dsr_comes_from_deflated_sharpe_module(self):
        hold, alt, fwd = _toy_positions_and_fwd()
        results = evaluate_candidates(
            {"rl_agent_v2": alt, "rl_agent_v3_dsr": hold, "baseline_buy_hold": hold},
            fwd,
            cost_bps=10.0,
            model_names=["rl_agent_v2", "rl_agent_v3_dsr"],
        )
        srs = [results[n]["sr_per_period"] for n in ("rl_agent_v2", "rl_agent_v3_dsr")]
        sr_var = float(np.var(srs, ddof=1))
        for name in ("rl_agent_v2", "rl_agent_v3_dsr"):
            moments = sharpe_moments(results[name]["daily_net_returns"])
            expected = deflated_sharpe_ratio(
                moments["sr"],
                moments["n_obs"],
                moments["skew"],
                moments["kurtosis"],
                n_trials=2,
                sr_variance=sr_var,
            )
            assert results[name]["dsr_prob"] == pytest.approx(expected)
        # Baseline is the benchmark, not a selected trial — no DSR deflation
        assert results["baseline_buy_hold"]["dsr_prob"] is None


# ---------------------------------------------------------------------------
# Kill-gate verdict — Rev-2 addendum decision tree
# ---------------------------------------------------------------------------


class TestKillGateVerdict:
    @staticmethod
    def _res(v2, v3, base):
        return {
            "rl_agent_v2": {"net_sharpe": v2},
            "rl_agent_v3_dsr": {"net_sharpe": v3},
            "baseline_buy_hold": {"net_sharpe": base},
        }

    def test_v2_reactivation_branch(self):
        verdict = kill_gate_verdict(self._res(v2=1.0, v3=0.2, base=0.3))
        assert verdict["verdict"] == "v2_reactivation_candidate"

    def test_option_c_drop_branch(self):
        verdict = kill_gate_verdict(self._res(v2=0.1, v3=-0.2, base=0.5))
        assert verdict["verdict"] == "option_c_drop_rl"

    def test_v6_hypothesis_branch(self):
        # v3 clears baseline but v2 does not dominate -> neither reactivate nor drop
        verdict = kill_gate_verdict(self._res(v2=0.2, v3=0.9, base=0.3))
        assert verdict["verdict"] == "v6_hypothesis_open"

    def test_margin_is_noise_band(self):
        # v2 above baseline by LESS than the noise band -> not a reactivation proof
        verdict = kill_gate_verdict(self._res(v2=0.34, v3=0.1, base=0.3))
        assert verdict["verdict"] != "v2_reactivation_candidate"


# ---------------------------------------------------------------------------
# End-to-end: run_benchmark with mini zips (CI proxy for the archive run)
# ---------------------------------------------------------------------------


class TestRunBenchmark:
    def test_gate_payload_end_to_end(self, tmp_path):
        data_dir = tmp_path / "bars"
        data_dir.mkdir()
        for i, sym in enumerate(("AAA", "BBB")):
            _synthetic_bars(200, seed=10 + i).to_csv(data_dir / f"{sym}.csv")

        v2 = _make_mini_model_zip(tmp_path, 11, "mini_v2")
        v3 = _make_mini_model_zip(tmp_path, 12, "mini_v3")
        sums = _write_sha256sums(tmp_path, v2, v3)

        payload = run_benchmark(
            data_dir=data_dir,
            v2_model=v2,
            v3_model=v3,
            sha256sums=sums,
            start="2024-06-03",
            end="2024-09-30",
            cost_bps=10.0,
        )

        meta = payload["run_meta"]
        assert meta["issue"] == "#1956"
        assert meta["cost_bps"] == 10.0
        assert meta["deterministic"] is True
        # Archon-Auflage 3 (Rev 2): the gate artifact must carry the panel /
        # survivorship limitation (#1907: removals-only membership, no PIT
        # universe) so the absolute "beats baseline" reading is caveated
        # in the artifact itself, not only in the runbook prose.
        caveat = meta["panel_caveat"]
        assert "#1907" in caveat
        assert "survivorship" in caveat.lower()
        assert "PIT" in caveat
        for name in ("rl_agent_v2", "rl_agent_v3_dsr"):
            model_meta = meta["models"][name]
            assert len(model_meta["sha256"]) == 64
            assert model_meta["obs_normalization"] == "raw (VecNormalize stats missing)"

        results = payload["results"]
        assert set(results) == {"rl_agent_v2", "rl_agent_v3_dsr", "baseline_buy_hold"}
        for name in results:
            assert "net_sharpe" in results[name]
            assert "daily_net_returns" not in results[name]  # JSON-safe payload
        assert payload["kill_gate"]["verdict"] in {
            "v2_reactivation_candidate",
            "option_c_drop_rl",
            "v6_hypothesis_open",
        }

    def test_tampered_model_aborts(self, tmp_path):
        data_dir = tmp_path / "bars"
        data_dir.mkdir()
        _synthetic_bars(200).to_csv(data_dir / "AAA.csv")
        v2 = _make_mini_model_zip(tmp_path, 11, "mini_v2")
        v3 = _make_mini_model_zip(tmp_path, 12, "mini_v3")
        sums = _write_sha256sums(tmp_path, v3)  # v2 entry missing
        with pytest.raises(ValueError, match="no SHA256SUMS entry"):
            run_benchmark(
                data_dir=data_dir,
                v2_model=v2,
                v3_model=v3,
                sha256sums=sums,
                start="2024-06-03",
                end="2024-09-30",
            )
