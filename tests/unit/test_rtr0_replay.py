"""
test_rtr0_replay.py — RTR-0 (#1947) deterministic offline vote replay.

Covered (plan #1947 §8):
  * R6 replay_votes — one vote row per replayable agent per bar from a fake
        deterministic provider; abstentions (weight 0) kept and marked;
        NewsSentiment (LLM) and SpecialistAlpha (dormant) reported EXCLUDED,
        never silently dropped.
  * R7 state contract — the SymbolEvalState the replay builds carries exactly
        what agents.py reads (ohlc scalars, symbol, current_time ISO, vix key)
        — regression guard against silent KeyErrors.
  * forward_returns — PIT-safe forward window, no look-ahead past the frame.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from core.analysis.attribution.replay import (
    EXCLUDED_AGENTS,
    ReplayBarsProvider,
    build_symbol_eval_state,
    forward_returns,
    replay_votes,
)

# ---------------------------------------------------------------------------
# Fixtures — deterministic bars, no I/O
# ---------------------------------------------------------------------------


def _bars(n: int = 80, start: str = "2024-01-02", seed: int = 3) -> pd.DataFrame:
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


@pytest.fixture()
def provider(tmp_path):
    frames = {"AAA": _bars(seed=3), "BBB": _bars(seed=4)}
    for sym, df in frames.items():
        df.to_parquet(tmp_path / f"{sym}.parquet")
    return ReplayBarsProvider(tmp_path)


# ---------------------------------------------------------------------------
# ReplayBarsProvider — point-in-time get_data contract (data_provider.py:283)
# ---------------------------------------------------------------------------


class TestReplayBarsProvider:
    def test_symbols_discovered_from_files(self, provider):
        assert set(provider.symbols) == {"AAA", "BBB"}

    def test_get_data_is_point_in_time(self, provider):
        end = datetime(2024, 3, 1)
        df = provider.get_data("AAA", end, days=30)
        assert not df.empty
        assert df.index.max() <= pd.Timestamp(end)
        assert df.index.min() >= pd.Timestamp(end) - pd.Timedelta(days=31)

    def test_get_data_columns_are_lowercase_ohlcv(self, provider):
        df = provider.get_data("AAA", datetime(2024, 3, 1), days=30)
        assert list(df.columns) == ["open", "high", "low", "close", "volume"]

    def test_unknown_symbol_returns_empty_frame(self, provider):
        assert provider.get_data("ZZZ", datetime(2024, 3, 1), days=30).empty


# ---------------------------------------------------------------------------
# R7 — SymbolEvalState contract
# ---------------------------------------------------------------------------


class TestStateContract:
    def test_state_carries_exactly_what_agents_read(self):
        bar = pd.Series(
            {"open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 1e6},
            name=pd.Timestamp("2024-06-03"),
        )
        state = build_symbol_eval_state("AAA", bar, vix=17.5)
        # agents.py reads: state["ohlc"] scalars, state["symbol"],
        # state.get("current_time") ISO, state.get("vix")
        assert state["symbol"] == "AAA"
        for key in ("open", "high", "low", "close", "volume"):
            assert isinstance(state["ohlc"][key], float)
        datetime.fromisoformat(state["current_time"])  # must parse
        assert state["vix"] == 17.5

    def test_vix_none_stays_none_for_honest_abstention(self):
        bar = pd.Series(
            {"open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 1e6},
            name=pd.Timestamp("2024-06-03"),
        )
        state = build_symbol_eval_state("AAA", bar, vix=None)
        assert state["vix"] is None


# ---------------------------------------------------------------------------
# R6 — replay_votes
# ---------------------------------------------------------------------------


class TestReplayVotes:
    async def test_one_row_per_replayable_agent_per_bar(self, provider):
        result = await replay_votes(provider, start="2024-03-01", end="2024-03-28")
        votes = result.votes
        n_bars = votes.groupby("symbol")["ts"].nunique()
        agents_per_bar = votes.groupby(["symbol", "ts"])["agent"].nunique()
        assert (n_bars > 0).all()
        # every replayed (symbol, bar) has the same, full agent roster
        assert agents_per_bar.nunique() == 1
        replayed = set(votes["agent"].unique())
        assert "NewsSentimentAgent" not in replayed  # LLM — never invoked offline
        assert "DrawdownGuardAgent" in replayed
        assert "MomentumAgent" in replayed
        assert "RegimeDetectionAgent" in replayed
        assert "FundamentalsAgent" in replayed

    async def test_exclusions_are_explicit_not_silent(self, provider):
        result = await replay_votes(provider, start="2024-03-01", end="2024-03-14")
        assert "NewsSentimentAgent" in result.exclusions
        assert "SpecialistAlphaAgent" in result.exclusions
        for reason in result.exclusions.values():
            assert reason  # a reason string, never empty
        assert set(EXCLUDED_AGENTS) <= set(result.exclusions)

    async def test_abstentions_are_marked_weight_zero(self, provider):
        # without a VIX series the VIXAwareRiskAgent must abstain (weight 0)
        result = await replay_votes(provider, start="2024-03-01", end="2024-03-14")
        vix_votes = result.votes[result.votes["agent"] == "VIXAwareRiskAgent"]
        assert not vix_votes.empty
        assert (vix_votes["weight"] == 0.0).all()

    async def test_replay_is_deterministic(self, provider):
        r1 = await replay_votes(provider, start="2024-03-01", end="2024-03-14")
        r2 = await replay_votes(provider, start="2024-03-01", end="2024-03-14")
        pd.testing.assert_frame_equal(r1.votes, r2.votes)

    async def test_global_registry_is_restored(self, provider):
        from core.agent_registry import get_global_registry

        before = get_global_registry()
        await replay_votes(provider, start="2024-03-01", end="2024-03-14")
        assert get_global_registry() is before


# ---------------------------------------------------------------------------
# forward_returns — the target definition
# ---------------------------------------------------------------------------


class TestForwardReturns:
    def test_forward_return_is_future_close_over_close(self, provider):
        fwd = forward_returns(provider, horizon=5)
        frame = provider.frames["AAA"]
        row = fwd[(fwd["symbol"] == "AAA")].iloc[0]
        i = frame.index.get_loc(row["ts"])
        expected = frame["close"].iloc[i + 5] / frame["close"].iloc[i] - 1.0
        assert row["fwd_ret"] == pytest.approx(expected)

    def test_no_lookahead_rows_beyond_frame_end(self, provider):
        fwd = forward_returns(provider, horizon=5)
        frame = provider.frames["AAA"]
        last_allowed = frame.index[-6]
        assert fwd[fwd["symbol"] == "AAA"]["ts"].max() == last_allowed


# ---------------------------------------------------------------------------
# Airlock guard (plan §8): pure finance-domain imports, no dev-tools
# ---------------------------------------------------------------------------


class TestAirlock:
    def test_attribution_package_imports_no_dev_tools(self):
        import pathlib

        import core.analysis.attribution as pkg

        root = pathlib.Path(pkg.__file__).parent
        forbidden = (
            "import docker",
            "import langsmith",
            "from docker",
            "from langsmith",
        )
        for py in root.glob("*.py"):
            src = py.read_text(encoding="utf-8")
            for needle in forbidden:
                assert needle not in src, f"{py.name} imports dev-tools: {needle}"
