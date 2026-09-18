# tests/unit/test_rtr0_lstm_panel_3228.py
"""#3228 Phase 2 — LSTM cross-sectional panel wiring for the RTR-0 replay.

The production LSTMSignalAgent (a) resolves its producer via
``registry.get("LSTMDynamic")`` under ROUND_TABLE_DISTINCT_ML_SOURCES (config
default TRUE) and (b) votes on the LstmPanelStore standing AS OF engine_now().
``replay_votes`` therefore, when given a cross-section-capable strategy, must:
register it under "LSTMDynamic", record each date's cross-section into the panel
store, and drive the sim clock — all restored afterwards (no global-state leak).

Model-independent: a fake strategy supplies the cross-section, so these run in CI
without the LSTM bundle. End-to-end (real agent produces weight>0 opinions) is the
smoke run on sim_corpus.
"""

import asyncio

import numpy as np
import pandas as pd

from core.analysis.attribution.replay import ReplayBarsProvider, replay_votes


class _FakeLstmStrategy:
    """Cross-section-capable stub (duck-types OfflineLstmStrategy for the replay)."""

    def __init__(self, provider):
        self.data_provider = provider
        self.seen_dates = []

    def cross_section_for(self, current_time):
        self.seen_dates.append(pd.Timestamp(current_time).normalize())
        return {"AAA": 2.0, "BBB": 1.0, "CCC": 0.5}

    async def evaluate_for_symbol(self, symbol, ohlc_data, market_data, current_time):
        return None  # agent abstains; this test targets the WIRING, not the model


def _provider(tmp_path):
    idx = pd.bdate_range("2024-01-01", periods=80)
    rng = np.random.default_rng(0)
    for sym in ("AAA", "BBB", "CCC"):
        base = 100 + rng.standard_normal(80).cumsum()
        df = pd.DataFrame(
            {
                "open": base,
                "high": base + 1,
                "low": base - 1,
                "close": base + 0.5,
                "volume": 1_000_000.0,
            },
            index=idx,
        )
        df.to_parquet(tmp_path / f"{sym}.parquet")
    return ReplayBarsProvider(str(tmp_path))


def test_panel_recorded_and_sim_mode_restored(tmp_path):
    import config
    from core.report.lstm_panel_store import get_store

    prov = _provider(tmp_path)
    strat = _FakeLstmStrategy(prov)
    orig_sim_mode = getattr(config.get_config(), "SIM_MODE", False)

    # Window spans > SNAPSHOT_HISTORY_LEN (~20) trading days so the retention-bump is
    # exercised: without it the store evicts all but the last ~20 dates and the EARLIEST
    # recorded date's panel would be gone.
    asyncio.run(
        replay_votes(prov, start="2024-01-15", end="2024-03-28", strategy=strat)
    )

    # (1) isolation: SIM_MODE restored, no global-state leak
    assert getattr(config.get_config(), "SIM_MODE", False) == orig_sim_mode
    # (2) the EARLIEST recorded date's panel survived (retention bump worked) — this
    # is the regression guard for the SNAPSHOT_HISTORY_LEN eviction that capped LSTM
    # attribution at ~17 days.
    assert len(strat.seen_dates) > 20
    earliest = min(strat.seen_dates)
    xsec = get_store().cross_section_at(earliest)
    assert set(xsec) == {"AAA", "BBB", "CCC"}


def test_no_strategy_is_byte_identical_no_sim_mode(tmp_path):
    """Without a cross-section strategy the LSTM-panel path is inert (SIM_MODE off)."""
    import config

    prov = _provider(tmp_path)
    orig = getattr(config.get_config(), "SIM_MODE", False)
    asyncio.run(replay_votes(prov, start="2024-04-01", end="2024-04-05"))
    assert getattr(config.get_config(), "SIM_MODE", False) == orig
