# tests/unit/test_rtr0_iv_injection_3228.py
"""#3228 Phase 1 — corpus IV series injection into the RTR-0 replay state.

Unlocks VIXAwareRiskAgent offline attribution: its IV path ranks a symbol's
ATM-30 IV against the PREVIOUS session's cross-section (agents.py:1141, AC-7
no-look-ahead). These cover the pure plumbing; end-to-end (VIXAware votes with
weight>0) is the smoke run on sim_corpus.
"""

import pandas as pd
import pytest

from core.analysis.attribution.replay import (
    build_symbol_eval_state,
    iv_reference_for,
    load_iv_table,
)


def _bar():
    return pd.Series(
        {"open": 1.0, "high": 2.0, "low": 1.0, "close": 1.5, "volume": 100.0},
        name=pd.Timestamp("2024-01-03"),
    )


class TestStateIVKeys:
    def test_default_none_is_byte_identical_contract(self):
        s = build_symbol_eval_state("AAPL", _bar())
        assert s["implied_vol"] is None
        assert s["implied_vol_reference"] is None
        assert s["implied_vol_reference_date"] is None

    def test_injected_values_present(self):
        s = build_symbol_eval_state(
            "AAPL",
            _bar(),
            implied_vol=0.30,
            implied_vol_reference=[0.2, 0.4],
            implied_vol_reference_date="2024-01-02",
        )
        assert s["implied_vol"] == 0.30
        assert s["implied_vol_reference"] == [0.2, 0.4]
        assert s["implied_vol_reference_date"] == "2024-01-02"


class TestLoadIVTable:
    def test_parses_by_date(self, tmp_path):
        df = pd.DataFrame(
            {
                "sym": ["AAPL", "MSFT", "AAPL"],
                "tag": ["2024-01-02", "2024-01-02", "2024-01-03"],
                "iv": [0.30, 0.25, 0.31],
            }
        )
        p = tmp_path / "iv.parquet"
        df.to_parquet(p)
        by_date, dates = load_iv_table(str(p))
        assert dates == [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-03")]
        assert by_date[pd.Timestamp("2024-01-02")] == {"AAPL": 0.30, "MSFT": 0.25}

    def test_missing_file_is_failsafe(self):
        assert load_iv_table("C:/no/such/iv.parquet") == ({}, [])

    def test_bad_columns_is_failsafe(self, tmp_path):
        p = tmp_path / "bad.parquet"
        pd.DataFrame({"a": [1], "b": [2]}).to_parquet(p)
        assert load_iv_table(str(p)) == ({}, [])

    def test_nonpositive_iv_skipped(self, tmp_path):
        df = pd.DataFrame(
            {
                "sym": ["AAPL", "MSFT"],
                "tag": ["2024-01-02", "2024-01-02"],
                "iv": [0.0, 0.25],
            }
        )
        p = tmp_path / "iv.parquet"
        df.to_parquet(p)
        by_date, _ = load_iv_table(str(p))
        assert by_date[pd.Timestamp("2024-01-02")] == {"MSFT": 0.25}


class TestIVReference:
    BY = {
        pd.Timestamp("2024-01-02"): {"AAPL": 0.30, "MSFT": 0.25},
        pd.Timestamp("2024-01-03"): {"AAPL": 0.31},
    }
    DATES = sorted(BY)

    def test_prev_session_cross_section(self):
        ref, d = iv_reference_for(self.DATES, self.BY, pd.Timestamp("2024-01-03"))
        assert sorted(ref) == [0.25, 0.30]
        assert d == "2024-01-02"

    def test_none_for_earliest(self):
        ref, d = iv_reference_for(self.DATES, self.BY, pd.Timestamp("2024-01-02"))
        assert ref is None and d is None

    def test_strictly_before_not_same_day(self):
        # ts on 2024-01-02 must NOT use its own day (no look-ahead / no self-inclusion)
        ref, d = iv_reference_for(self.DATES, self.BY, pd.Timestamp("2024-01-02T15:00"))
        assert ref is None and d is None
