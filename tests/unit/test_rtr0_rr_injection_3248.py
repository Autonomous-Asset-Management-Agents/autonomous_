"""#3248 Phase 2 — RR (risk-reversal) corpus injection into the RTR-0 replay.

Unlocks UpsideSkewAgent offline attribution: it ranks a symbol's 25Δ
risk-reversal against the PREVIOUS session's cross-section (agents.py:2328, AC-7
no-look-ahead). Mirrors the #3228 IV injection exactly.

  * load_rr_table / rr_reference_for — the plumbing (fail-safe, no look-ahead).
  * build_symbol_eval_state — the three RR channels default None (byte-identical).
  * replay_votes(rr_table=...) — with the flag ARMED (monkeypatched, never in code)
    UpsideSkew votes with weight>0 and only from a prior session; without rr_table
    the vote panel is byte-identical (the dark agent abstains regardless).
"""

import numpy as np
import pandas as pd
import pytest

from core.analysis.attribution.replay import (
    ReplayBarsProvider,
    build_symbol_eval_state,
    load_rr_table,
    replay_votes,
    rr_reference_for,
)
from core.round_table import agents


def _bar():
    return pd.Series(
        {"open": 1.0, "high": 2.0, "low": 1.0, "close": 1.5, "volume": 100.0},
        name=pd.Timestamp("2024-01-03"),
    )


# ---------------------------------------------------------------------------
# State contract — RR channels
# ---------------------------------------------------------------------------


class TestStateRRKeys:
    def test_default_none_is_byte_identical_contract(self):
        s = build_symbol_eval_state("AAPL", _bar())
        assert s["risk_reversal"] is None
        assert s["risk_reversal_reference"] is None
        assert s["risk_reversal_reference_date"] is None

    def test_injected_values_present(self):
        s = build_symbol_eval_state(
            "AAPL",
            _bar(),
            risk_reversal=0.05,
            risk_reversal_reference=[-0.02, 0.03],
            risk_reversal_reference_date="2024-01-02",
        )
        assert s["risk_reversal"] == 0.05
        assert s["risk_reversal_reference"] == [-0.02, 0.03]
        assert s["risk_reversal_reference_date"] == "2024-01-02"


# ---------------------------------------------------------------------------
# load_rr_table
# ---------------------------------------------------------------------------


class TestLoadRRTable:
    def test_parses_by_date(self, tmp_path):
        df = pd.DataFrame(
            {
                "sym": ["AAPL", "MSFT", "AAPL"],
                "tag": ["2024-01-02", "2024-01-02", "2024-01-03"],
                "rr": [0.05, -0.02, 0.03],
            }
        )
        p = tmp_path / "rr.parquet"
        df.to_parquet(p)
        by_date, dates = load_rr_table(str(p))
        assert dates == [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-03")]
        assert by_date[pd.Timestamp("2024-01-02")] == {"AAPL": 0.05, "MSFT": -0.02}

    def test_missing_file_is_failsafe(self):
        assert load_rr_table("C:/no/such/rr.parquet") == ({}, [])

    def test_bad_columns_is_failsafe(self, tmp_path):
        p = tmp_path / "bad.parquet"
        pd.DataFrame({"a": [1], "b": [2]}).to_parquet(p)
        assert load_rr_table(str(p)) == ({}, [])

    def test_negative_rr_kept_but_nonfinite_dropped(self, tmp_path):
        # RR is SIGNED (unlike IV) — negatives are legitimate downside skew and must
        # survive; only non-finite values are dropped.
        df = pd.DataFrame(
            {
                "sym": ["AAPL", "MSFT", "GOOG"],
                "tag": ["2024-01-02", "2024-01-02", "2024-01-02"],
                "rr": [-0.04, float("nan"), 0.02],
            }
        )
        p = tmp_path / "rr.parquet"
        df.to_parquet(p)
        by_date, _ = load_rr_table(str(p))
        assert by_date[pd.Timestamp("2024-01-02")] == {"AAPL": -0.04, "GOOG": 0.02}


# ---------------------------------------------------------------------------
# rr_reference_for — strictly previous session
# ---------------------------------------------------------------------------


class TestRRReference:
    BY = {
        pd.Timestamp("2024-01-02"): {"AAPL": 0.05, "MSFT": -0.02},
        pd.Timestamp("2024-01-03"): {"AAPL": 0.03},
    }
    DATES = sorted(BY)

    def test_prev_session_cross_section(self):
        ref, d = rr_reference_for(self.DATES, self.BY, pd.Timestamp("2024-01-03"))
        assert sorted(ref) == [-0.02, 0.05]
        assert d == "2024-01-02"

    def test_none_for_earliest(self):
        ref, d = rr_reference_for(self.DATES, self.BY, pd.Timestamp("2024-01-02"))
        assert ref is None and d is None

    def test_strictly_before_not_same_day(self):
        ref, d = rr_reference_for(self.DATES, self.BY, pd.Timestamp("2024-01-02T15:00"))
        assert ref is None and d is None


# ---------------------------------------------------------------------------
# replay_votes(rr_table=...) end-to-end
# ---------------------------------------------------------------------------


def _bars(dates, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.DatetimeIndex(dates)
    n = len(idx)
    close = 100.0 + np.cumsum(rng.normal(0.05, 1.0, n))
    open_ = close + rng.normal(0, 0.3, n)
    high = np.maximum(open_, close) + 0.5
    low = np.minimum(open_, close) - 0.5
    vol = rng.integers(1_000_000, 5_000_000, n).astype(float)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": vol},
        index=idx,
    )


@pytest.fixture()
def provider(tmp_path):
    dates = ["2024-01-02", "2024-01-03", "2024-01-04"]
    frames = {"AAA": _bars(dates, seed=3), "BBB": _bars(dates, seed=4)}
    for sym, df in frames.items():
        df.to_parquet(tmp_path / f"{sym}.parquet")
    return ReplayBarsProvider(tmp_path)


def _rr_table():
    by_date = {
        pd.Timestamp("2024-01-02"): {"AAA": -0.02, "BBB": 0.05},
        pd.Timestamp("2024-01-03"): {"AAA": 0.03, "BBB": 0.04},
        pd.Timestamp("2024-01-04"): {"AAA": 0.06, "BBB": 0.01},
    }
    return by_date, sorted(by_date)


@pytest.mark.asyncio
async def test_upside_skew_votes_with_rr_table(monkeypatch, provider):
    # Arm the dark agent for THIS test only (never in code) — mirrors the #3095 tests.
    monkeypatch.setattr(agents, "_upside_skew_enabled", lambda: True)
    result = await replay_votes(
        provider, start="2024-01-02", end="2024-01-04", rr_table=_rr_table()
    )
    us = result.votes[result.votes["agent"] == "UpsideSkewAgent"]
    assert not us.empty
    # n_days>0: at least one real (weight>0) UpsideSkew vote once a prior session exists.
    assert (us["weight"] > 0.0).any()


@pytest.mark.asyncio
async def test_reference_strictly_previous_session(monkeypatch, provider):
    monkeypatch.setattr(agents, "_upside_skew_enabled", lambda: True)
    result = await replay_votes(
        provider, start="2024-01-02", end="2024-01-04", rr_table=_rr_table()
    )
    us = result.votes[result.votes["agent"] == "UpsideSkewAgent"]
    first_day = us[us["ts"] == pd.Timestamp("2024-01-02")]
    # The earliest session has NO prior cross-section -> UpsideSkew abstains (no
    # look-ahead into its own or a future day).
    assert (first_day["weight"] == 0.0).all()
    later = us[us["ts"] == pd.Timestamp("2024-01-03")]
    assert (later["weight"] > 0.0).any()


@pytest.mark.asyncio
async def test_without_rr_table_is_byte_identical(monkeypatch, provider):
    # While the agent is dark (flag off, the production default) the injection is
    # inert: the vote panel with and without rr_table is identical.
    monkeypatch.setattr(agents, "_upside_skew_enabled", lambda: False)
    r_off = await replay_votes(provider, start="2024-01-02", end="2024-01-04")
    r_inj = await replay_votes(
        provider, start="2024-01-02", end="2024-01-04", rr_table=_rr_table()
    )
    pd.testing.assert_frame_equal(r_off.votes, r_inj.votes)
