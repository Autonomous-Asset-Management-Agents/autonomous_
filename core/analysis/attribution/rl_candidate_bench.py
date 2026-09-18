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

"""rl_candidate_bench.py — #1956 Inc 0: v2-vs-v3 kill-gate benchmark harness.

Deterministically replays archived RL candidate policies (RecurrentPPO zips,
Rev-2 addendum: ``rl_agent_v2`` obs (11,) / ``rl_agent_v3_dsr`` obs (12,))
over a panel of daily bars, compares them net-of-cost against the executor
baseline (immediate buy-and-hold of the LSTM-Top-N book — plan §12.7), and
emits the gate JSON the #1956 decision tree consumes.

OFFLINE ONLY — nothing in the product imports this module. The model zips are
NEVER committed: they stay in the local archive and enter only as CLI path
parameters, hash-verified against the archive's ``SHA256SUMS.txt`` before any
load (fail-hard on mismatch).

Era observation parity (ZERO-GUESSING, anchored on the archived era code):
  * ``rl_agent_v2`` was trained on the era env's ``_get_features``
    (``code-era-jan2026/trading_environment.py:186-267``); ``rl_agent_v3_dsr``
    was trained on **``DSRTradingEnv``** (``train_rl_dsr.py:159 ff.``, its
    ``_get_features`` at ``:240-291``) — whose obs construction is IDENTICAL
    to ``trading_environment.py`` (verified line-by-line in review): 8 market
    features uniformly clipped to +-10 (era :222; RSI/ADX saturate at 10),
    position 0/1, time_in_position = min(days/20, 1) (era :229-233),
    unrealized PnL clipped +-0.5 (era :236-247); v3 adds the volatility
    regime score 0/0.33/0.67/1.0 at thresholds 0.015/0.025/0.04
    (``trading_environment.py:71, :249-265`` == ``train_rl_dsr.py:240-291``).
    :class:`EraObsAdapter` reconstructs exactly that — it is an ADAPTER for
    the benchmark, deliberately NOT the live builder
    (``rl_signal.py:721/:733`` serves RSI/ADX on a DIFFERENT /100 scale —
    that train/serve mismatch is root-cause #4 of the plan, and reproducing
    it here would corrupt the benchmark).
  * Both candidates were trained under ``VecNormalize(norm_obs=True,
    clip_obs=10)`` (era ``train_rl_clean.py:329-336`` -> ``rl_stats_v2.pkl``,
    save at ``:390``, ``STATS_SAVE_PATH`` ``:44``; era
    ``train_rl_dsr.py:579-585`` -> ``rl_stats_v4.pkl``). Neither stats
    pkl survived in the archive. When ``--v2-stats``/``--v3-stats`` are
    provided, :class:`VecNormalizeStats` applies the exact live serving
    formula (``rl_signal.py:398-405``); without them the replay serves RAW
    observations — identical to the live fallback (``rl_signal.py:395-397``)
    and identical for BOTH candidates (internally fair), but the limitation
    is stamped per-model into the gate JSON (``obs_normalization``).

Feature panel: indicators come from the repo SSOT ``create_live_features``
(models/torch_model.py) with the era RL column mapping preserved in
``scripts/prepare_training_data.py:133-148`` (returns=log_ret,
volatility_20d=volatility_20, momentum_10d=returns_cumsum_5,
bb_pct=bb_position). The era's own ``prepare_clean_data.py`` did not survive;
this in-repo lineage is the closest reconstruction and is stamped into
``run_meta.feature_source``.

Metrics: net Sharpe comes from the merged RTR-0 module
(``core.analysis.attribution.metrics.portfolio_sharpe``, 10 bps one-way
#1969 convention) and DSR from the merged #2400 module
(``core.ml.deflated_sharpe``) — never re-derived here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import pickle
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from core.analysis.attribution.metrics import (
    DELTA_SHARPE_NOISE_BAND,
    SharpeResult,
    portfolio_sharpe,
)
from core.ml.deflated_sharpe import (
    deflated_sharpe_excess,
    deflated_sharpe_ratio,
    sharpe_moments,
)

logger = logging.getLogger(__name__)

ISSUE = "#1956"

# Era env market feature order — code-era-jan2026/trading_environment.py:53-56.
ERA_FEATURE_COLUMNS: List[str] = [
    "returns",
    "rsi_14",
    "macd",
    "bb_pct",
    "volume_ratio",
    "volatility_20d",
    "momentum_10d",
    "adx_14",
]

# Era v3 volatility regime thresholds — code-era-jan2026/trading_environment.py:71.
_VOL_THRESHOLDS = {"low": 0.015, "normal": 0.025, "elevated": 0.04}

# Annualisation for daily bars, matching metrics.py:_TRADING_DAYS_PER_YEAR.
_TRADING_DAYS_PER_YEAR = 252.0

_CANDIDATE_V2 = "rl_agent_v2"
_CANDIDATE_V3 = "rl_agent_v3_dsr"
_BASELINE = "baseline_buy_hold"


# ---------------------------------------------------------------------------
# Hash verification — models never enter the repo, only verified paths
# ---------------------------------------------------------------------------


def verify_model_hash(model_path: Path | str, sha256sums_path: Path | str) -> str:
    """SHA256-verify ``model_path`` against a ``SHA256SUMS.txt`` (by basename).

    Returns the verified digest. Raises ``ValueError`` when the file has no
    entry or the digest mismatches — a tampered/wrong artifact must never be
    benchmarked silently.
    """
    model_path = Path(model_path)
    sums: Dict[str, str] = {}
    for line in Path(sha256sums_path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        digest, _, name = line.partition(" ")
        sums[name.lstrip("*").strip()] = digest.lower()

    expected = sums.get(model_path.name)
    if expected is None:
        raise ValueError(
            f"no SHA256SUMS entry for '{model_path.name}' in {sha256sums_path}"
        )
    actual = hashlib.sha256(model_path.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError(
            f"SHA256 mismatch for '{model_path.name}': "
            f"expected {expected}, got {actual}"
        )
    return actual


# ---------------------------------------------------------------------------
# Era feature panel
# ---------------------------------------------------------------------------


def era_features_from_bars(bars: pd.DataFrame) -> Optional[pd.DataFrame]:
    """Era RL feature frame from OHLCV bars (lowercase ohlcv columns).

    Indicator SSOT = ``create_live_features`` (models/torch_model.py); the
    era column mapping mirrors ``scripts/prepare_training_data.py:133-148``.
    Returns ``None`` when OHLCV columns are missing (create_live_features
    contract), never raises.
    """
    from models.torch_model import create_live_features

    df = create_live_features(bars.copy())
    if df is None:
        logger.warning(
            "era_features_from_bars: create_live_features failed "
            "(missing OHLCV columns?) — symbol skipped"
        )
        return None
    out = pd.DataFrame(index=df.index)
    out["Close"] = df["close"]
    out["returns"] = df["log_ret"]
    out["rsi_14"] = df["rsi_14"]
    out["macd"] = df["macd"]
    out["bb_pct"] = df["bb_position"]
    out["volume_ratio"] = df["volume_ratio"]
    out["volatility_20d"] = df["volatility_20"]
    out["momentum_10d"] = df["returns_cumsum_5"]
    out["adx_14"] = df["adx_14"]
    return out


# ---------------------------------------------------------------------------
# Era observation adapter (benchmark-only — NOT the live path)
# ---------------------------------------------------------------------------


class EraObsAdapter:
    """Reconstruction of the era env ``_get_features`` (era :186-267).

    ``variant``: ``"v2"`` -> 11-dim, ``"v3"`` -> 12-dim (+ regime score).
    """

    def __init__(self, variant: str):
        if variant not in ("v2", "v3"):
            raise ValueError(f"unknown era obs variant: {variant!r} (want 'v2'|'v3')")
        self.variant = variant
        self.obs_dim = 12 if variant == "v3" else 11

    def build(
        self,
        row: pd.Series,
        position: int,
        days_held: int,
        entry_price: float,
    ) -> np.ndarray:
        """One observation vector for a bar + trade state, era semantics."""
        features: List[float] = []
        for col in ERA_FEATURE_COLUMNS:
            val = row.get(col, 0.0)
            val = 0.0 if pd.isna(val) else float(val)
            # Era :222 — UNIFORM clip; RSI/ADX saturation is intentional
            # parity with the training distribution, not a bug here.
            features.append(float(np.clip(val, -10, 10)))

        features.append(float(position))
        if position == 1:
            features.append(min(days_held / 20.0, 1.0))  # era :229-231
        else:
            features.append(0.0)
        if position == 1 and entry_price > 0:
            close = float(row.get("Close", entry_price))
            pnl = (close - entry_price) / entry_price
            features.append(float(np.clip(pnl, -0.5, 0.5)))  # era :244-245
        else:
            features.append(0.0)

        if self.variant == "v3":  # era :249-265
            vol = row.get("volatility_20d", 0.02)
            vol = 0.02 if pd.isna(vol) else float(vol)
            if vol < _VOL_THRESHOLDS["low"]:
                regime = 0.0
            elif vol < _VOL_THRESHOLDS["normal"]:
                regime = 0.33
            elif vol < _VOL_THRESHOLDS["elevated"]:
                regime = 0.67
            else:
                regime = 1.0
            features.append(regime)

        return np.asarray(features, dtype=np.float32)


# ---------------------------------------------------------------------------
# VecNormalize serving parity
# ---------------------------------------------------------------------------


class VecNormalizeStats:
    """Obs normalization from a saved ``VecNormalize`` pkl (``env.save``).

    Applies the exact live serving formula (rl_signal.py:398-405):
    ``clip((raw - mean) / sqrt(var + 1e-8), -10, 10)``.
    """

    def __init__(self, mean: np.ndarray, var: np.ndarray):
        self.mean = np.asarray(mean, dtype=np.float64)
        self.var = np.asarray(var, dtype=np.float64)

    @classmethod
    def load(cls, path: Path | str) -> "VecNormalizeStats":
        with open(path, "rb") as f:
            venv = pickle.load(f)
        rms = venv.obs_rms
        return cls(rms.mean, rms.var)

    def normalize(self, obs: np.ndarray) -> np.ndarray:
        return np.clip(
            (obs - self.mean) / np.sqrt(self.var + 1e-8), -10.0, 10.0
        ).astype(np.float32)


# ---------------------------------------------------------------------------
# Candidate loading
# ---------------------------------------------------------------------------


def load_candidate(
    model_path: Path | str, sha256sums_path: Path | str, device: str = "cpu"
) -> Tuple[Any, str]:
    """Hash-verify then load a RecurrentPPO candidate zip. Returns (model, sha)."""
    digest = verify_model_hash(model_path, sha256sums_path)
    from sb3_contrib import RecurrentPPO  # lazy — heavy import

    model = RecurrentPPO.load(str(model_path), device=device)
    return model, digest


# ---------------------------------------------------------------------------
# Deterministic replay
# ---------------------------------------------------------------------------


def _slice_window(
    frame: pd.DataFrame, start: Optional[str], end: Optional[str]
) -> pd.DataFrame:
    if start is not None:
        frame = frame[frame.index >= pd.Timestamp(start)]
    if end is not None:
        frame = frame[frame.index <= pd.Timestamp(end)]
    return frame


def replay_candidate(
    model: Any,
    adapter: EraObsAdapter,
    frames: Dict[str, pd.DataFrame],
    start: Optional[str] = None,
    end: Optional[str] = None,
    stats: Optional[VecNormalizeStats] = None,
) -> Tuple[pd.DataFrame, Dict[str, Dict[str, int]]]:
    """Deterministic policy replay over feature frames, era action semantics.

    Per symbol: one episode over the window (LSTM state threaded,
    ``episode_start`` only on the first bar, ``deterministic=True``).
    Actions map long/flat exactly like the era env step (era :346-407):
    1=BUY enters if flat, 2=SELL exits if held, 0=HOLD keeps. The recorded
    position at ``ts`` is post-action, so it earns ``fwd_ret(ts)`` and pays
    turnover cost in ``portfolio_sharpe`` (metrics.py:510-553 convention).
    """
    rows: List[Tuple[pd.Timestamp, str, int]] = []
    info: Dict[str, Dict[str, int]] = {}
    for symbol in sorted(frames):
        window = _slice_window(frames[symbol], start, end)
        if window.empty:
            logger.warning(
                "replay_candidate: %s has no bars in [%s, %s] — skipped",
                symbol,
                start,
                end,
            )
            continue
        position = 0
        entry_price = 0.0
        entry_step = 0
        n_trades = 0
        lstm_state = None
        episode_start = True
        for step, (ts, row) in enumerate(window.iterrows()):
            days_held = step - entry_step if position == 1 else 0
            obs = adapter.build(row, position, days_held, entry_price)
            if stats is not None:
                obs = stats.normalize(obs)
            action, lstm_state = model.predict(
                obs,
                state=lstm_state,
                episode_start=np.array([episode_start]),
                deterministic=True,
            )
            episode_start = False
            action = int(action)
            if action == 1 and position == 0:  # era :349-355 BUY from flat
                position = 1
                entry_price = float(row.get("Close", 0.0))
                entry_step = step
                n_trades += 1
            elif action == 2 and position == 1:  # era :362-395 SELL from held
                position = 0
                entry_price = 0.0
                entry_step = 0
                n_trades += 1
            rows.append((ts, symbol, position))
        info[symbol] = {"n_trades": n_trades, "n_bars": len(window)}
    positions = pd.DataFrame(rows, columns=["ts", "symbol", "position"])
    return positions, info


def buy_and_hold_positions(
    frames: Dict[str, pd.DataFrame],
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> pd.DataFrame:
    """Executor baseline (plan §12.7): immediate full entry into the panel
    book (the LSTM-Top-N selection IS the panel), held for the window."""
    rows: List[Tuple[pd.Timestamp, str, int]] = []
    for symbol in sorted(frames):
        window = _slice_window(frames[symbol], start, end)
        for ts in window.index:
            rows.append((ts, symbol, 1))
    return pd.DataFrame(rows, columns=["ts", "symbol", "position"])


# ---------------------------------------------------------------------------
# Evaluation — Sharpe via metrics.portfolio_sharpe, DSR via #2400 module
# ---------------------------------------------------------------------------


def _daily_net_returns(
    positions_df: pd.DataFrame, fwd_df: pd.DataFrame, cost_bps: float
) -> pd.Series:
    """Daily book net-return series — SAME convention as portfolio_sharpe
    (metrics.py:525-540); used solely as the DSR input.

    Consistency with the metrics SSOT is pinned NON-tautologically (review
    Finding 2): test_net_sharpe_equals_metrics_portfolio_sharpe recomputes
    the Sharpe from THIS series (mean/std(ddof=1)*sqrt(252)) and requires it
    to equal ``portfolio_sharpe().sharpe`` to 1e-9 — if either convention
    drifts, that pin fails instead of the gate JSON silently mixing two
    conventions."""
    merged = positions_df.merge(fwd_df, on=["ts", "symbol"], how="inner").sort_values(
        ["symbol", "ts"]
    )
    if merged.empty:
        return pd.Series(dtype=float)
    cost_rate = float(cost_bps) / 10_000.0
    turnover = (
        merged.groupby("symbol")["position"].diff().fillna(merged["position"]).abs()
    )
    merged["net_ret"] = merged["position"] * merged["fwd_ret"] - turnover * cost_rate
    return merged.groupby("ts")["net_ret"].mean()


def evaluate_candidates(
    candidates: Dict[str, pd.DataFrame],
    fwd_df: pd.DataFrame,
    cost_bps: float = 10.0,
    model_names: Optional[List[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Net Sharpe + DSR per candidate book.

    ``model_names`` are the SELECTED trials for DSR deflation (Bailey/LdP
    ``n_trials``): the RL candidates under comparison. The baseline is the
    benchmark, not a trial — its ``dsr_prob``/``dsr_excess`` are ``None``.
    """
    model_names = list(model_names or candidates.keys())
    results: Dict[str, Dict[str, Any]] = {}
    for name, positions_df in candidates.items():
        sharpe: SharpeResult = portfolio_sharpe(positions_df, fwd_df, cost_bps)
        daily = _daily_net_returns(positions_df, fwd_df, cost_bps)
        moments = sharpe_moments(daily.tolist())
        results[name] = {
            "net_sharpe": sharpe.sharpe,
            "mean_daily_ret": sharpe.mean_daily_ret,
            "daily_vol": sharpe.daily_vol,
            "n_days": sharpe.n_days,
            "total_cost": sharpe.total_cost,
            "sr_per_period": moments["sr"] if moments else float("nan"),
            "daily_net_returns": daily.tolist(),
            "dsr_prob": None,
            "dsr_excess_ann": None,
        }

    trial_srs = [
        results[n]["sr_per_period"]
        for n in model_names
        if n in results and np.isfinite(results[n]["sr_per_period"])
    ]
    n_trials = len(model_names)
    sr_variance = float(np.var(trial_srs, ddof=1)) if len(trial_srs) >= 2 else 0.0

    for name in model_names:
        if name not in results:
            continue
        daily = results[name]["daily_net_returns"]
        moments = sharpe_moments(daily)
        if moments is None:
            continue
        results[name]["dsr_prob"] = deflated_sharpe_ratio(
            moments["sr"],
            moments["n_obs"],
            moments["skew"],
            moments["kurtosis"],
            n_trials=n_trials,
            sr_variance=sr_variance,
        )
        results[name]["dsr_excess_ann"] = deflated_sharpe_excess(
            moments["sr"], n_trials, sr_variance
        ) * float(np.sqrt(_TRADING_DAYS_PER_YEAR))
    return results


# ---------------------------------------------------------------------------
# Kill-gate verdict — Rev-2 addendum decision tree
# ---------------------------------------------------------------------------


def kill_gate_verdict(
    results: Dict[str, Dict[str, Any]],
    margin: float = DELTA_SHARPE_NOISE_BAND,
    v2_name: str = _CANDIDATE_V2,
    v3_name: str = _CANDIDATE_V3,
    baseline_name: str = _BASELINE,
) -> Dict[str, Any]:
    """Rev-2 addendum decision tree over net Sharpe (margin = the RTR-0
    noise band, metrics.py:DELTA_SHARPE_NOISE_BAND — beating by less than
    the band is not a proof, prove-it-or-kill-it):

      * v2 > baseline + margin AND v2 > v3 + margin -> v2_reactivation_candidate
      * neither candidate > baseline + margin       -> option_c_drop_rl
      * anything else                               -> v6_hypothesis_open
    """
    v2 = float(results[v2_name]["net_sharpe"])
    v3 = float(results[v3_name]["net_sharpe"])
    base = float(results[baseline_name]["net_sharpe"])

    if v2 > base + margin and v2 > v3 + margin:
        verdict = "v2_reactivation_candidate"
        reason = (
            f"v2 net Sharpe {v2:.3f} clears baseline {base:.3f} and "
            f"v3 {v3:.3f} by more than the noise band {margin}"
        )
    elif v2 <= base + margin and v3 <= base + margin:
        verdict = "option_c_drop_rl"
        reason = (
            f"neither v2 ({v2:.3f}) nor v3 ({v3:.3f}) clears baseline "
            f"({base:.3f}) by the noise band {margin} — no net evidence"
        )
    else:
        verdict = "v6_hypothesis_open"
        reason = (
            f"mixed evidence (v2 {v2:.3f}, v3 {v3:.3f}, baseline {base:.3f}) "
            f"— v6 hypothesis remains open, separate go required"
        )
    return {
        "verdict": verdict,
        "reason": reason,
        "margin": margin,
        "net_sharpe": {v2_name: v2, v3_name: v3, baseline_name: base},
    }


# ---------------------------------------------------------------------------
# Orchestration + gate payload
# ---------------------------------------------------------------------------


def _git_sha() -> str:
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=Path(__file__).resolve().parents[3],
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
    except Exception:  # noqa: BLE001 — metadata only, never fatal
        return "unknown"


def run_benchmark(
    data_dir: Path | str,
    v2_model: Path | str,
    v3_model: Path | str,
    sha256sums: Path | str,
    start: str,
    end: str,
    cost_bps: float = 10.0,
    symbols: Optional[List[str]] = None,
    v2_stats: Optional[Path | str] = None,
    v3_stats: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """Full Inc-0 benchmark: verify, load, replay, evaluate, gate-verdict."""
    from core.analysis.attribution.replay import ReplayBarsProvider, forward_returns

    provider = ReplayBarsProvider(data_dir, symbols=symbols)
    if not provider.symbols:
        raise ValueError(f"no readable bar files in {data_dir}")

    frames: Dict[str, pd.DataFrame] = {}
    for symbol in provider.symbols:
        feats = era_features_from_bars(provider.frames[symbol])
        if feats is not None:
            frames[symbol] = feats
    if not frames:
        raise ValueError("no symbol produced a feature frame — check bar data")

    # 1-bar mark-to-market P&L target (portfolio_sharpe daily convention).
    fwd_df = forward_returns(provider, horizon=1)

    candidates_meta: Dict[str, Dict[str, Any]] = {}
    books: Dict[str, pd.DataFrame] = {}
    replay_info: Dict[str, Dict[str, Dict[str, int]]] = {}
    specs = [
        (_CANDIDATE_V2, v2_model, "v2", v2_stats),
        (_CANDIDATE_V3, v3_model, "v3", v3_stats),
    ]
    for name, model_path, variant, stats_path in specs:
        model, digest = load_candidate(model_path, sha256sums)
        stats = VecNormalizeStats.load(stats_path) if stats_path else None
        obs_norm = (
            "vecnormalize (obs_rms applied, rl_signal.py:398-405 formula)"
            if stats
            else "raw (VecNormalize stats missing)"
        )
        if stats is None:
            logger.warning(
                "%s: VecNormalize stats missing — serving RAW observations "
                "(identical to live fallback rl_signal.py:395-397); absolute "
                "levels are caveated, the v2-vs-v3 comparison stays fair",
                name,
            )
        positions, info = replay_candidate(
            model, EraObsAdapter(variant), frames, start=start, end=end, stats=stats
        )
        books[name] = positions
        replay_info[name] = info
        candidates_meta[name] = {
            "path": str(model_path),
            "sha256": digest,
            "obs_dim": EraObsAdapter(variant).obs_dim,
            "obs_normalization": obs_norm,
            "n_trades_total": int(sum(i["n_trades"] for i in info.values())),
        }

    books[_BASELINE] = buy_and_hold_positions(frames, start=start, end=end)

    results = evaluate_candidates(
        books, fwd_df, cost_bps=cost_bps, model_names=[_CANDIDATE_V2, _CANDIDATE_V3]
    )
    gate = kill_gate_verdict(results)

    # JSON-safe payload: drop the raw daily series (audit via re-run, not blob)
    json_results = {
        name: {k: v for k, v in res.items() if k != "daily_net_returns"}
        for name, res in results.items()
    }

    import sb3_contrib
    import torch

    payload: Dict[str, Any] = {
        "run_meta": {
            "issue": ISSUE,
            "increment": "Inc 0 — v2-vs-v3 kill-gate benchmark (Rev-2 addendum)",
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "git_sha": _git_sha(),
            "data_dir": str(data_dir),
            "symbols": sorted(frames),
            "start": start,
            "end": end,
            "cost_bps": float(cost_bps),
            "deterministic": True,
            "python": sys.version.split()[0],
            "sb3_contrib": sb3_contrib.__version__,
            "torch": torch.__version__,
            "feature_source": (
                "create_live_features (models/torch_model.py) + era RL column "
                "mapping (scripts/prepare_training_data.py:133-148); era "
                "prepare_clean_data.py not archived — closest lineage"
            ),
            "obs_adapter": (
                "EraObsAdapter — code-era-jan2026/trading_environment.py:186-267 "
                "reconstruction (uniform +-10 clip, era saturation preserved); "
                "rl_agent_v3_dsr was trained on DSRTradingEnv (train_rl_dsr.py"
                ":240-291 _get_features) whose obs construction is identical"
            ),
            # Archon-Auflage 3: the panel/survivorship limitation lives in the
            # ARTIFACT, not only in runbook prose — every gate JSON says under
            # which universe its absolute numbers were produced.
            "panel_caveat": (
                "panel universe is NOT point-in-time (#1907 pending: "
                "removals-only membership, no PIT universe reconstruction) — "
                "survivorship bias inflates absolute levels; the RELATIVE "
                "v2-vs-v3 comparison shares the panel and stays fair, the "
                "absolute beats-baseline reading is caveated until the #1907 "
                "PIT panel exists"
            ),
            "baseline": (
                "immediate buy-and-hold of the panel book (executor baseline, "
                "plan §12.7 — the LSTM-Top-N selection IS the panel)"
            ),
            "models": candidates_meta,
        },
        "results": json_results,
        "replay_info": replay_info,
        "kill_gate": gate,
    }
    return payload


# ---------------------------------------------------------------------------
# CLI — offline analysis, all parameters are arguments (BORA: no config keys)
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir", required=True, help="dir of {SYMBOL}.parquet|.csv daily bars"
    )
    parser.add_argument("--v2-model", required=True, help="path to rl_agent_v2.zip")
    parser.add_argument("--v3-model", required=True, help="path to rl_agent_v3_dsr.zip")
    parser.add_argument(
        "--sha256sums", required=True, help="archive SHA256SUMS.txt (mandatory)"
    )
    parser.add_argument("--start", required=True, help="OOS start (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="OOS end (YYYY-MM-DD)")
    parser.add_argument(
        "--cost-bps", type=float, default=10.0, help="one-way cost (bps, #1969)"
    )
    parser.add_argument(
        "--symbols", default=None, help="comma-separated subset (default: all files)"
    )
    parser.add_argument(
        "--v2-stats", default=None, help="optional VecNormalize pkl for v2"
    )
    parser.add_argument(
        "--v3-stats", default=None, help="optional VecNormalize pkl for v3"
    )
    parser.add_argument(
        "--out", default=None, help="output dir (default: print JSON to stdout)"
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    payload = run_benchmark(
        data_dir=args.data_dir,
        v2_model=args.v2_model,
        v3_model=args.v3_model,
        sha256sums=args.sha256sums,
        start=args.start,
        end=args.end,
        cost_bps=args.cost_bps,
        symbols=args.symbols.split(",") if args.symbols else None,
        v2_stats=args.v2_stats,
        v3_stats=args.v3_stats,
    )

    text = json.dumps(payload, indent=2, default=float)
    if args.out:
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / "rl_candidate_gate.json"
        out_file.write_text(text, encoding="utf-8")
        print(f"gate artifact written: {out_file}")
    else:
        print(text)
    print(f"kill-gate verdict: {payload['kill_gate']['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
