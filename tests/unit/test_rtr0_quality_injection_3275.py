"""#3275 (Part 4) — composite-quality injection into the RTR-0 replay.

Unlocks QualityAgent offline attribution: it ranks a symbol's composite-quality score
against the PREVIOUS session's cross-section (AC-7, no look-ahead). The quality
cross-section is FEED-derived per date (analogous to the LSTM panel, NOT an external
corpus table like IV/RR).

  * quality_reference_for — the plumbing (strictly previous session, no look-ahead).
  * build_symbol_eval_state — the three quality channels default None (byte-identical).
  * replay_votes(quality_table=...) — with the flag ARMED (monkeypatched, never in code)
    QualityAgent votes with weight>0 and only from a prior session; with the flag OFF
    the panel is byte-identical (the dark agent abstains, no table is built).
"""

import numpy as np
import pandas as pd
import pytest

from core.analysis.attribution.replay import (
    ReplayBarsProvider,
    build_symbol_eval_state,
    quality_reference_for,
    replay_votes,
)
from core.round_table import agents


def _bar():
    return pd.Series(
        {"open": 1.0, "high": 2.0, "low": 1.0, "close": 1.5, "volume": 100.0},
        name=pd.Timestamp("2024-01-03"),
    )


class TestStateQualityKeys:
    def test_default_none_is_byte_identical_contract(self):
        s = build_symbol_eval_state("AAPL", _bar())
        assert s["quality_score"] is None
        assert s["quality_reference"] is None
        assert s["quality_reference_date"] is None

    def test_injected_values_present(self):
        s = build_symbol_eval_state(
            "AAPL",
            _bar(),
            quality_score=0.42,
            quality_reference=[0.2, 0.8],
            quality_reference_date="2024-01-02",
        )
        assert s["quality_score"] == 0.42
        assert s["quality_reference"] == [0.2, 0.8]
        assert s["quality_reference_date"] == "2024-01-02"


class TestQualityReference:
    BY = {
        pd.Timestamp("2024-01-02"): {"AAA": 0.20, "BBB": 0.80},
        pd.Timestamp("2024-01-03"): {"AAA": 0.55},
    }
    DATES = sorted(BY)

    def test_prev_session_cross_section(self):
        ref, d = quality_reference_for(self.DATES, self.BY, pd.Timestamp("2024-01-03"))
        assert sorted(ref) == [0.20, 0.80]
        assert d == "2024-01-02"

    def test_none_for_earliest(self):
        ref, d = quality_reference_for(self.DATES, self.BY, pd.Timestamp("2024-01-02"))
        assert ref is None and d is None

    def test_strictly_before_not_same_day(self):
        ref, d = quality_reference_for(
            self.DATES, self.BY, pd.Timestamp("2024-01-02T15:00")
        )
        assert ref is None and d is None


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


def _quality_table():
    by_date = {
        pd.Timestamp("2024-01-02"): {"AAA": 0.20, "BBB": 0.80},
        pd.Timestamp("2024-01-03"): {"AAA": 0.55, "BBB": 0.40},
        pd.Timestamp("2024-01-04"): {"AAA": 0.90, "BBB": 0.10},
    }
    return by_date, sorted(by_date)


@pytest.mark.asyncio
async def test_quality_votes_with_quality_table(monkeypatch, provider):
    monkeypatch.setattr(agents, "_quality_agent_enabled", lambda: True)
    result = await replay_votes(
        provider, start="2024-01-02", end="2024-01-04", quality_table=_quality_table()
    )
    q = result.votes[result.votes["agent"] == "QualityAgent"]
    assert not q.empty
    # n_days>0: at least one real (weight>0) Quality vote once a prior session exists.
    assert (q["weight"] > 0.0).any()


@pytest.mark.asyncio
async def test_reference_strictly_previous_session(monkeypatch, provider):
    monkeypatch.setattr(agents, "_quality_agent_enabled", lambda: True)
    result = await replay_votes(
        provider, start="2024-01-02", end="2024-01-04", quality_table=_quality_table()
    )
    q = result.votes[result.votes["agent"] == "QualityAgent"]
    first_day = q[q["ts"] == pd.Timestamp("2024-01-02")]
    # The earliest session has NO prior cross-section -> QualityAgent abstains.
    assert (first_day["weight"] == 0.0).all()


@pytest.mark.asyncio
async def test_dark_flag_off_is_byte_identical(monkeypatch, provider):
    # Flag OFF (default): QualityAgent abstains regardless; no quality_table built.
    monkeypatch.setattr(agents, "_quality_agent_enabled", lambda: False)
    result = await replay_votes(provider, start="2024-01-02", end="2024-01-04")
    q = result.votes[result.votes["agent"] == "QualityAgent"]
    assert not q.empty  # it is still invoked (visible abstention)
    assert (q["weight"] == 0.0).all()
