# tests/unit/test_compute_features_node.py
# #2389 (Epic RTR #1958) — TDD Red→Green: _compute_features_node (Inc 2, flag-gated).
#
# The MiFID feature snapshot was 100% neutral defaults over 8017 live decisions
# (rsi_14=50, atr_14d=0, ... each distinct=1): _fetch_context_node computes no TA
# and compute_technical_features had no live consumer. The new node (gated by
# DESKTOP_FEATURE_SNAPSHOT_ENABLED, default OFF) fetches the 30d OHLCV window
# (shared with DrawdownGuardAgent via the provider cycle cache), computes
# core/round_table/features.py::compute_technical_features and attaches the
# last-row SCALARS as state["features"] (§5.9: Dict[str, float], no DataFrame).
#
# Gherkin (plan #2389 §7):
#   R1  flag on + history available  → state["features"] carries REAL values
#   R2  §5.9: only scalars in state["features"] (no DataFrame/ndarray)
#   R3  missing history              → fail-open (features=None) + WARNING log
#   R4  flag off (default)           → no-op: no fetch, no features key
#   +   config parity: DESKTOP_FEATURE_SNAPSHOT_ENABLED in BOTH editions, default False

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_state(symbol="AAPL"):
    return {
        "symbol": symbol,
        "ohlc": {
            "open": 150.0,
            "high": 152.0,
            "low": 149.0,
            "close": 151.0,
            "volume": 1000.0,
        },
        "market_data_keys": [],
        "current_time": "2026-07-22T14:00:00+00:00",
        "signal": None,
        "error": None,
    }


def _mk_ohlcv(rows: int = 40, start: float = 100.0) -> pd.DataFrame:
    """Synthetic-but-varying OHLCV so RSI/ATR/MACD come out non-neutral."""
    idx = pd.date_range("2026-05-01", periods=rows, freq="B")
    rng = np.random.default_rng(42)
    close = pd.Series(start + np.cumsum(rng.normal(0.4, 1.5, rows)), index=idx)
    high = close + rng.uniform(0.5, 2.0, rows)
    low = close - rng.uniform(0.5, 2.0, rows)
    open_ = close.shift(1).fillna(start)
    volume = pd.Series(rng.uniform(1e6, 5e6, rows), index=idx)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume}
    )


def _arm_flag(monkeypatch, enabled: bool):
    import config

    monkeypatch.setattr(
        config,
        "get_config",
        lambda: SimpleNamespace(DESKTOP_FEATURE_SNAPSHOT_ENABLED=enabled),
    )


def _arm_provider(monkeypatch, df):
    """Patch the module-level get_global_registry used by the node with a registry
    whose active strategy carries a data_provider returning `df`."""
    import core.orchestration.graph as g

    provider = MagicMock()
    provider.get_data.return_value = df
    active = SimpleNamespace(data_provider=provider)
    registry = MagicMock()
    registry.get_active.return_value = active
    monkeypatch.setattr(g, "get_global_registry", lambda: registry)
    return provider


# ---------------------------------------------------------------------------
# R1: node fills state["features"] with real values
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_r1_node_fills_features_with_real_values(monkeypatch):
    from core.orchestration.graph import _compute_features_node

    _arm_flag(monkeypatch, True)
    provider = _arm_provider(monkeypatch, _mk_ohlcv())

    result = await _compute_features_node(_minimal_state())

    feats = result.get("features")
    assert feats, "state['features'] must be filled when flag on + history available"
    provider.get_data.assert_called_once()
    # The neutral-default smoking gun: rsi real (≠ 50 exactly), atr_14d > 0.
    assert 0.0 < feats["rsi_14"] < 100.0
    assert feats["rsi_14"] != 50.0
    assert feats["atr_14d"] > 0.0
    assert "macd_hist" in feats
    assert "bb_position" in feats
    assert "vol_anomaly" in feats


@pytest.mark.anyio
async def test_r1b_graph_topology_contains_compute_features_node(monkeypatch):
    """The node must be wired between fetch_context and run_strategy."""
    pytest.importorskip("langgraph")  # graph build needs langgraph (CI has it)
    from unittest.mock import patch as _patch

    from core.orchestration.graph import build_symbol_eval_graph

    with _patch("core.orchestration.graph._build_checkpointer", return_value=None):
        graph = build_symbol_eval_graph()
    assert "compute_features" in graph.nodes


# ---------------------------------------------------------------------------
# R2: §5.9 — only scalars in state["features"]
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_r2_features_are_scalars_only(monkeypatch):
    from core.orchestration.graph import _compute_features_node

    _arm_flag(monkeypatch, True)
    _arm_provider(monkeypatch, _mk_ohlcv())

    result = await _compute_features_node(_minimal_state())

    feats = result.get("features")
    assert isinstance(feats, dict)
    for key, value in feats.items():
        assert isinstance(key, str)
        assert isinstance(value, float), (
            f"features['{key}'] must be a float scalar, got {type(value).__name__} "
            "(§5.9: no DataFrame/ndarray in the checkpointed state)"
        )
        assert np.isfinite(value), f"features['{key}'] must be finite"


# ---------------------------------------------------------------------------
# R3: missing history → fail-open + WARNING (never DEBUG, never state["error"])
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_r3_missing_history_fails_open_with_warning(monkeypatch, caplog):
    from core.orchestration.graph import _compute_features_node

    _arm_flag(monkeypatch, True)
    _arm_provider(monkeypatch, pd.DataFrame())  # empty history

    with caplog.at_level(logging.WARNING, logger="core.orchestration.graph"):
        result = await _compute_features_node(_minimal_state())

    assert result.get("features") is None
    assert result.get("error") is None, "a TA gap must NEVER kill the decision"
    assert any(
        r.levelno == logging.WARNING and "_compute_features_node" in r.getMessage()
        for r in caplog.records
    ), "fail-open must log at WARNING (§5.6)"


@pytest.mark.anyio
async def test_r3b_provider_exception_fails_open_with_warning(monkeypatch, caplog):
    from core.orchestration.graph import _compute_features_node

    _arm_flag(monkeypatch, True)
    provider = _arm_provider(monkeypatch, None)
    provider.get_data.side_effect = RuntimeError("feed down")

    with caplog.at_level(logging.WARNING, logger="core.orchestration.graph"):
        result = await _compute_features_node(_minimal_state())

    assert result.get("features") is None
    assert result.get("error") is None
    assert any(r.levelno == logging.WARNING for r in caplog.records)


# ---------------------------------------------------------------------------
# R4: flag off (default) → no-op, no fetch
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_r4_flag_off_is_noop_no_fetch(monkeypatch):
    from core.orchestration.graph import _compute_features_node

    _arm_flag(monkeypatch, False)
    provider = _arm_provider(monkeypatch, _mk_ohlcv())

    state = _minimal_state()
    result = await _compute_features_node(state)

    assert result.get("features") is None
    provider.get_data.assert_not_called()


@pytest.mark.anyio
async def test_r4b_error_state_short_circuits(monkeypatch):
    from core.orchestration.graph import _compute_features_node

    _arm_flag(monkeypatch, True)
    provider = _arm_provider(monkeypatch, _mk_ohlcv())

    state = {**_minimal_state(), "error": "boom"}
    result = await _compute_features_node(state)

    assert result.get("error") == "boom"
    provider.get_data.assert_not_called()


# ---------------------------------------------------------------------------
# Config parity: DESKTOP_FEATURE_SNAPSHOT_ENABLED in BOTH editions, default False
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).resolve().parents[2]


def _load_oss_config():
    spec = importlib.util.spec_from_file_location(
        "config_oss_2389", str(_ROOT / "config.oss.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_flag_default_false_enterprise(monkeypatch):
    monkeypatch.delenv("DESKTOP_FEATURE_SNAPSHOT_ENABLED", raising=False)
    from config import RuntimeConfigState

    assert RuntimeConfigState().DESKTOP_FEATURE_SNAPSHOT_ENABLED is False


def test_flag_default_false_oss(monkeypatch):
    monkeypatch.delenv("DESKTOP_FEATURE_SNAPSHOT_ENABLED", raising=False)
    cfg = _load_oss_config()
    assert cfg.DESKTOP_FEATURE_SNAPSHOT_ENABLED is False
