# tests/unit/test_data_cache_bounded.py
"""HistoricalDataProvider.data_cache must be BOUNDED — the OOM-over-days root cause.

The cache key is ``f"{symbol}_{YYYY-MM-DD}_{days}"`` (core/data_provider.py:347), so a long-running
engine (grounding/report fetches + warm-universe cache run 24/7 since #2529/#2519) mints a fresh key
per symbol EVERY calendar day. The old plain dict never evicted, so it grew by one universe of
multi-year DataFrames per day → RAM climbed to ~95% over days → OOM (Windows exit 0xFFFFFFFF) →
orphan holds :8001 → "cannot restart". A symbol only ever needs its LATEST-date frame per days-param.
"""

from unittest.mock import MagicMock

import pandas as pd

from core.data_provider import HistoricalDataProvider, _BoundedDataCache


def _dp():
    return HistoricalDataProvider(api=MagicMock())


def test_stale_date_evicted_per_symbol_and_days():
    """Advancing the calendar date for the SAME (symbol, days) keeps only the latest — no per-day growth."""
    dp = _dp()
    for day in ("2026-07-25", "2026-07-26", "2026-07-27", "2026-07-28"):
        dp.data_cache[f"AAPL_{day}_1825"] = pd.DataFrame({"c": [1]})
    held = [k for k in dp.data_cache if k.startswith("AAPL_") and k.endswith("_1825")]
    assert held == ["AAPL_2026-07-28_1825"]  # RED (plain dict): all 4 retained


def test_distinct_symbol_and_days_are_kept():
    """Eviction is scoped to the SAME (symbol, days) — different symbols / days-params coexist."""
    dp = _dp()
    dp.data_cache["AAPL_2026-07-28_1825"] = pd.DataFrame()
    dp.data_cache["MSFT_2026-07-28_1825"] = pd.DataFrame()  # different symbol
    dp.data_cache["AAPL_2026-07-28_60"] = pd.DataFrame()  # different days-param
    assert len(dp.data_cache) == 3


def test_dotted_symbol_key_shape():
    """A class-share symbol (BRK.B) has no underscore, so rsplit still isolates symbol/date/days."""
    dp = _dp()
    dp.data_cache["BRK.B_2026-07-27_60"] = pd.DataFrame()
    dp.data_cache["BRK.B_2026-07-28_60"] = (
        pd.DataFrame()
    )  # newer date → evicts the older
    assert list(dp.data_cache) == ["BRK.B_2026-07-28_60"]


def test_lru_ceiling_hard_caps_total_entries():
    """Distinct symbols never trigger sibling-eviction, so a hard LRU ceiling backstops pathological growth."""
    c = _BoundedDataCache(maxsize=3)
    for i in range(10):
        c[f"S{i}_2026-07-28_60"] = i  # 10 distinct symbols, no stale-date siblings
    assert len(c) == 3
    assert list(c) == [
        "S7_2026-07-28_60",
        "S8_2026-07-28_60",
        "S9_2026-07-28_60",
    ]  # oldest evicted


def test_malformed_key_does_not_crash():
    """A key without the expected shape must not raise — it is still stored and LRU-capped."""
    c = _BoundedDataCache(maxsize=5)
    c["weird-key"] = 1
    assert c["weird-key"] == 1
