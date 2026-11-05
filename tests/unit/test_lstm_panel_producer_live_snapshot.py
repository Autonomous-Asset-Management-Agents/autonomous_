# tests/unit/test_lstm_panel_producer_live_snapshot.py
# PANEL-0 (the LAST blocker of the desktop "0 decisions" outage).
#
# THE LIVE DEFECT (verified on the running desktop engine, 2026-07-25):
#   With the producer flags armed (LSTM_RANK_PANEL_STRATEGY_INDEPENDENT +
#   REPORT_GENERATOR_V2_ENABLED) the standby LSTMDynamic ranker RUNS every cycle but
#   writes 0 rankings, so cross_section_at(today) == {} and LSTMSignalAgent's
#   cross-sectional vote abstains "0 stocks, 0 distinct scores" — blocking EVERY
#   decision.
#
#   ROOT CAUSE — lstm_strategy.py update_lstm_rankings snapshot gate:
#     the LIVE Alpaca SDK ``Trade`` (snapshot.latest_trade) exposes the trade price as
#     ``.price``; only the simulation adapter / legacy raw shape carries ``.p``
#     (simulation_adapter.py:160). The gate read ``getattr(snap, "p", None)`` ALONE,
#     which is None on the real SDK model -> ``continue`` for EVERY live symbol ->
#     empty ranking -> record_cycle never called -> empty panel.
#
#   WHY THE ISOLATED TESTS NEVER CAUGHT IT: every existing producer test mocks the
#   snapshot with a bare ``MagicMock()`` and sets ``snap.latest_trade.p = 100.0`` — a
#   MagicMock auto-creates BOTH ``.p`` and ``.price`` as truthy children, so the gate
#   passes whichever name it reads. This module models the snapshot FAITHFULLY
#   (SimpleNamespace: ``.price`` present, ``.p`` genuinely absent) so the live gate is
#   exercised for real.
#
# Fully deterministic: no network / LLM / GPU / Redis. Test 1/3/4 mock inference to
# isolate the snapshot gate; Test 2 drives the REAL _get_torch_prediction over a
# realistic 494-bar frame (the data-provider-level repro) with a tiny deterministic
# "model" — proving get_data (494 bars) is NOT the limiter, the snapshot gate was.

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.report.lstm_panel_store import get_store
from core.round_table.agents import _MIN_PANEL_SYMBOLS
from core.strategies.lstm_strategy import LSTMDynamicStrategy


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #
def _make_strategy():
    """Minimal LSTMDynamicStrategy double — heavy ML __init__ bypassed, real numpy so
    the ranking/collapse math runs for real (mirrors test_lstm_panel_producer_desktop).
    """
    import numpy as np

    strategy = LSTMDynamicStrategy.__new__(LSTMDynamicStrategy)
    strategy.torch_model = MagicMock()  # truthy → update_lstm_rankings proceeds
    strategy.np = np
    strategy._lstm_rank_cache = []
    strategy._allocation_weights = {}
    return strategy


def _now():
    return datetime(2024, 6, 1, tzinfo=timezone.utc)


def _live_snapshots(symbols, price: float = 100.0):
    """LIVE Alpaca SDK snapshot shape: ``latest_trade`` has ``.price`` (and ``.size``),
    and NO ``.p``. SimpleNamespace (not MagicMock) so ``getattr(trade, "p", None)`` is
    genuinely None — the exact object the live engine receives from
    StockHistoricalDataClient.get_stock_snapshot."""
    snaps = {}
    for s in symbols:
        trade = SimpleNamespace(price=float(price), size=500.0)
        snaps[s] = SimpleNamespace(latest_trade=trade)
    return snaps


def _sim_snapshots(symbols, price: float = 100.0):
    """SIMULATION / legacy snapshot shape: ``latest_trade`` carries ``.p`` and NO
    ``.price`` (simulation_adapter.py:146-164). The fix must keep this path working."""
    snaps = {}
    for s in symbols:
        trade = SimpleNamespace(p=float(price))
        snaps[s] = SimpleNamespace(latest_trade=trade)
    return snaps


def _dispersive_symbols(n):
    syms = [f"S{i:03d}" for i in range(n)]
    preds = {s: float(i) * 0.05 - 1.0 for i, s in enumerate(syms)}
    return syms, preds


class _ReportCfg:
    """get_config() double carrying only the flag update_lstm_rankings reads."""

    def __init__(self, report_on: bool = True):
        self.REPORT_GENERATOR_V2_ENABLED = report_on


async def _run_producer_mocked_pred(strategy, symbols, preds, snapshots):
    """Drive update_lstm_rankings → record_cycle with inference mocked, so the ONLY
    variable under test is the snapshot gate."""

    async def fake_pred(symbol, current_time, market_data):
        return preds[symbol], None

    strategy._get_torch_prediction = AsyncMock(side_effect=fake_pred)
    with patch("config.get_config", return_value=_ReportCfg(True)):
        await strategy.update_lstm_rankings(symbols, snapshots, {"vix": 20.0}, _now())


@pytest.fixture(autouse=True)
def _clean_panel():
    get_store().reset()
    yield
    get_store().reset()


# =========================================================================== #
# 1. THE REPRODUCTION — live SDK snapshots (.price, NO .p) over a broad universe.
#    RED before the fix (0 symbols pass the .p gate → empty panel); GREEN after
#    (>= _MIN_PANEL_SYMBOLS, >= 2 distinct scores). This is the exact live path.
# =========================================================================== #
@pytest.mark.anyio
async def test_live_snapshot_shape_populates_broad_panel(monkeypatch):
    n = _MIN_PANEL_SYMBOLS + 5
    symbols, preds = _dispersive_symbols(n)
    strategy = _make_strategy()

    await _run_producer_mocked_pred(strategy, symbols, preds, _live_snapshots(symbols))

    xsec = get_store().cross_section_at(datetime.now(timezone.utc).date())
    assert len(xsec) >= _MIN_PANEL_SYMBOLS, (
        "LIVE Alpaca snapshots (latest_trade.price) must populate a broad panel; "
        f"got {len(xsec)} stocks — the .p-only gate dropped every symbol."
    )
    assert len(set(xsec.values())) >= 2, "panel must be dispersive, not collapsed"


# =========================================================================== #
# 2. DATA-PROVIDER-LEVEL REPRO — the reproduction the isolated tests never did.
#    Drive the REAL _get_torch_prediction over a realistic ~494-bar frame per symbol
#    (like the live get_bars) with a tiny deterministic model, live snapshot shape.
#    Proves: get_data (494 bars) is NOT the limiter — once the snapshot gate reads
#    .price, the full producer path writes a broad, dispersive panel.
# =========================================================================== #
@pytest.mark.anyio
async def test_real_prediction_path_over_494_bars_populates_panel(monkeypatch):
    import numpy as np
    import pandas as pd
    import torch

    n = _MIN_PANEL_SYMBOLS + 5
    symbols = [f"S{i:03d}" for i in range(n)]

    strategy = _make_strategy()
    strategy.torch = torch
    strategy.pd = pd
    strategy.device = torch.device("cpu")
    strategy.sequence_length = 20
    strategy.features_list = ["close"]
    strategy.scaler_y = None  # z_to_return degrades to identity
    strategy.client = MagicMock()  # NOT a SimulationAdapter → data_provider path
    # scaler_x: identity transform over the (seq_len, 1) close window
    strategy.scaler_x = SimpleNamespace(transform=lambda X: np.asarray(X, dtype=float))
    # a tiny deterministic "model": prediction = mean of the scaled close window, so
    # each symbol's distinct price level yields a distinct, well-separated score.
    strategy.torch_model = lambda X: torch.tensor(
        [[float(np.asarray(X).mean())]], dtype=torch.float32
    )

    # 494 daily bars per symbol, each symbol at a distinct price level (dispersion).
    idx = pd.date_range("2023-01-01", periods=494, freq="D")

    def fake_get_data(symbol, end_date, days, *a, **kw):
        base = 50.0 + symbols.index(symbol)  # distinct level per symbol
        close = pd.Series(base + np.sin(np.arange(494) / 7.0), index=idx)
        return pd.DataFrame(
            {
                "open": close.values,
                "high": close.values + 1.0,
                "low": close.values - 1.0,
                "close": close.values,
                "volume": 1_000_000.0,
            },
            index=idx,
        )

    strategy.data_provider = SimpleNamespace(get_data=fake_get_data)

    with patch("config.get_config", return_value=_ReportCfg(True)):
        await strategy.update_lstm_rankings(
            symbols, _live_snapshots(symbols), {"vix": 20.0}, _now()
        )

    xsec = get_store().cross_section_at(datetime.now(timezone.utc).date())
    assert len(xsec) >= _MIN_PANEL_SYMBOLS, (
        "the full producer path (real _get_torch_prediction over 494 bars) must write "
        f"a broad panel once the snapshot gate reads .price; got {len(xsec)} stocks."
    )
    assert len(set(xsec.values())) >= 2, "panel must be dispersive, not collapsed"


# =========================================================================== #
# 3. BACKWARD COMPAT — the simulation/legacy shape (.p, NO .price) must still
#    populate the panel. Guards against a naive fix that swaps .p for .price only.
# =========================================================================== #
@pytest.mark.anyio
async def test_sim_snapshot_shape_still_populates_panel(monkeypatch):
    n = _MIN_PANEL_SYMBOLS + 5
    symbols, preds = _dispersive_symbols(n)
    strategy = _make_strategy()

    await _run_producer_mocked_pred(strategy, symbols, preds, _sim_snapshots(symbols))

    xsec = get_store().cross_section_at(datetime.now(timezone.utc).date())
    assert len(xsec) >= _MIN_PANEL_SYMBOLS, (
        "simulation-shape snapshots (latest_trade.p) must keep populating the panel; "
        f"got {len(xsec)} stocks."
    )
    assert len(set(xsec.values())) >= 2


# =========================================================================== #
# 4. CLOSED-MARKET / THIN-SNAPSHOT ROBUSTNESS — a minority of names carry NO live
#    trade (latest_trade is None: only a stale daily_bar). The tradeability gate must
#    still drop exactly those, while the broad majority with a live price populate the
#    panel (>= _MIN_PANEL_SYMBOLS). Proves the fix does not weaken the gate.
# =========================================================================== #
@pytest.mark.anyio
async def test_mixed_snapshots_drop_no_trade_symbols_keep_broad_panel(monkeypatch):
    n_priced = _MIN_PANEL_SYMBOLS + 3
    n_notrade = 5
    priced = [f"P{i:03d}" for i in range(n_priced)]
    notrade = [f"N{i:03d}" for i in range(n_notrade)]
    symbols = priced + notrade
    preds = {s: float(i) * 0.05 - 1.0 for i, s in enumerate(symbols)}

    snaps = _live_snapshots(priced)
    for s in notrade:  # market-closed / non-tradeable: no live trade at all
        snaps[s] = SimpleNamespace(latest_trade=None)

    strategy = _make_strategy()
    await _run_producer_mocked_pred(strategy, symbols, preds, snaps)

    xsec = get_store().cross_section_at(datetime.now(timezone.utc).date())
    assert (
        len(xsec) >= _MIN_PANEL_SYMBOLS
    ), f"priced names must populate a broad panel; got {len(xsec)} stocks."
    for s in notrade:
        assert (
            s not in xsec
        ), f"{s} has no live trade → must be excluded (gate preserved)"
