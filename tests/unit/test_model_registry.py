# core/ml/model_registry — dormant per-symbol TFT inference router (fusion S1)
# TDD Red → Green. implementation_plan 2026-06-09-model-registry (Issue 1).
#
# Lands DORMANT: nothing on main imports model_registry yet (the specialist wiring
# is a later brick). These tests exercise the LRU / quality-gate / load control flow
# with the engine and gate MOCKED — no real checkpoint, no torch required.
#
# The async entrypoint `get_or_train` is exercised via real `await`; the blocking
# collaborators (`engine.load` / `engine.predict`) run via `asyncio.to_thread` and
# are mocked as plain sync callables (no bare `async def` stubs — Rule 2 / §5.2).

import asyncio
import logging
import os
import threading
import time
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


def _df():
    return pd.DataFrame({"close": [1.0, 2.0, 3.0]})


def _gate(passed: bool):
    return SimpleNamespace(passed=passed, reason="test")


def _mk_checkpoint(root, symbol):
    (root / symbol).mkdir(parents=True, exist_ok=True)
    (root / symbol / "checkpoint.pt").write_bytes(b"placeholder")


@pytest.fixture(autouse=True)
def _permissive_provenance(monkeypatch):
    # This module predates the RF-3 verify-before-load gate and provisions no manifest;
    # run that gate in permissive (local-dev) mode so the load-path tests still exercise.
    # The gate itself is covered by test_model_registry_verify.py.
    monkeypatch.setenv("TFT_REQUIRE_MANIFEST", "false")


@pytest.fixture(autouse=True)
def _tft_root_via_config(monkeypatch):
    # Section 2.10: model_registry resolves TFT_MODELS_ROOT via get_config(), not
    # os.getenv. Route get_config() to a FRESH RuntimeConfigState (pydantic BaseSettings
    # re-reads the env var at instantiation) so the existing
    # monkeypatch.setenv("TFT_MODELS_ROOT", ...) calls below keep working unchanged.
    import core.ml.model_registry as _mr
    from config import RuntimeConfigState

    monkeypatch.setattr(_mr, "get_config", lambda: RuntimeConfigState())


# ---------------------------------------------------------------------------
# 1. Import is side-effect-free (no eager load, no disk I/O at import)
# ---------------------------------------------------------------------------
def test_import_side_effect_free():
    from core.ml.model_registry import model_registry

    assert isinstance(model_registry._engines, OrderedDict)
    assert len(model_registry._engines) == 0
    assert isinstance(model_registry._known_missing, set)


# ---------------------------------------------------------------------------
# 2. Empty / invalid inputs → None (no engine touched)
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_get_or_train_none_for_empty_inputs():
    from core.ml.model_registry import _TFTModelRegistry

    reg = _TFTModelRegistry()
    assert await reg.get_or_train("", _df()) is None
    assert await reg.get_or_train("AAPL", None) is None
    assert await reg.get_or_train("AAPL", pd.DataFrame()) is None


# ---------------------------------------------------------------------------
# 3. No checkpoint → None + symbol cached as missing
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_none_when_no_checkpoint(tmp_path, monkeypatch):
    from core.ml.model_registry import _TFTModelRegistry

    monkeypatch.setenv("TFT_MODELS_ROOT", str(tmp_path))
    reg = _TFTModelRegistry()
    assert await reg.get_or_train("AAPL", _df()) is None
    assert "AAPL" in reg._known_missing


# ---------------------------------------------------------------------------
# 4. Quality gate fails → None + cached missing (WARNING, never silent)
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_none_when_gate_fails(tmp_path, monkeypatch, caplog):
    from core.ml.model_registry import _TFTModelRegistry

    _mk_checkpoint(tmp_path, "AAPL")
    monkeypatch.setenv("TFT_MODELS_ROOT", str(tmp_path))
    reg = _TFTModelRegistry()
    with caplog.at_level(logging.WARNING), patch(
        "core.ml.model_registry._gate_evaluate", return_value=_gate(False)
    ):
        out = await reg.get_or_train("AAPL", _df())
    assert out is None
    assert "AAPL" in reg._known_missing
    assert any(r.levelno == logging.WARNING for r in caplog.records)


# ---------------------------------------------------------------------------
# 5. Gate passes + engine loads → prediction served and engine cached
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_serves_prediction_when_gate_passes(tmp_path, monkeypatch):
    from core.ml.model_registry import _TFTModelRegistry

    _mk_checkpoint(tmp_path, "AAPL")
    monkeypatch.setenv("TFT_MODELS_ROOT", str(tmp_path))
    pred = SimpleNamespace(direction="up", base_return_pct=1.2, confidence=0.7)
    engine = MagicMock()
    engine.load.return_value = True
    engine.predict.return_value = pred

    reg = _TFTModelRegistry()
    with patch(
        "core.ml.model_registry._gate_evaluate", return_value=_gate(True)
    ), patch("core.ml.model_registry.TFTInferenceEngine", return_value=engine):
        out = await reg.get_or_train("AAPL", _df())

    assert out is pred
    assert "AAPL" in reg._engines
    engine.load.assert_called_once()
    engine.predict.assert_called_once()


# ---------------------------------------------------------------------------
# 6. Engine load fails → None + cached missing + WARNING (Rule 5, never silent)
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_none_when_load_fails(tmp_path, monkeypatch, caplog):
    from core.ml.model_registry import _TFTModelRegistry

    _mk_checkpoint(tmp_path, "AAPL")
    monkeypatch.setenv("TFT_MODELS_ROOT", str(tmp_path))
    engine = MagicMock()
    engine.load.return_value = False
    engine._load_error = "boom"

    reg = _TFTModelRegistry()
    with caplog.at_level(logging.WARNING), patch(
        "core.ml.model_registry._gate_evaluate", return_value=_gate(True)
    ), patch("core.ml.model_registry.TFTInferenceEngine", return_value=engine):
        out = await reg.get_or_train("AAPL", _df())

    assert out is None
    assert "AAPL" in reg._known_missing
    assert any(r.levelno == logging.WARNING for r in caplog.records)


# ---------------------------------------------------------------------------
# 7. LRU evicts the oldest engine past the cap
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_lru_eviction(tmp_path, monkeypatch):
    from core.ml import model_registry as mr

    monkeypatch.setenv("TFT_MODELS_ROOT", str(tmp_path))
    syms = [f"S{i}" for i in range(mr._LRU_MAX + 5)]
    for s in syms:
        _mk_checkpoint(tmp_path, s)

    def _fresh_engine(*_a, **_k):
        e = MagicMock()
        e.load.return_value = True
        e.predict.return_value = SimpleNamespace(direction="up")
        return e

    reg = mr._TFTModelRegistry()
    with patch(
        "core.ml.model_registry._gate_evaluate", return_value=_gate(True)
    ), patch("core.ml.model_registry.TFTInferenceEngine", side_effect=_fresh_engine):
        for s in syms:
            await reg.get_or_train(s, _df())

    assert len(reg._engines) <= mr._LRU_MAX
    assert "S0" not in reg._engines  # oldest evicted


# ---------------------------------------------------------------------------
# 8. TFT_MODELS_ROOT env override (provenance-epic seam)
# ---------------------------------------------------------------------------
def test_models_root_configurable(tmp_path, monkeypatch):
    from core.ml.model_registry import _models_root

    monkeypatch.setenv("TFT_MODELS_ROOT", str(tmp_path))
    assert _models_root() == tmp_path
    monkeypatch.delenv("TFT_MODELS_ROOT", raising=False)
    assert _models_root().name == "models"


# ---------------------------------------------------------------------------
# 9. predict() raising → None + WARNING (the "always returns None" contract)
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_get_or_train_swallows_predict_errors(tmp_path, monkeypatch, caplog):
    from core.ml.model_registry import _TFTModelRegistry

    _mk_checkpoint(tmp_path, "AAPL")
    monkeypatch.setenv("TFT_MODELS_ROOT", str(tmp_path))
    engine = MagicMock()
    engine.load.return_value = True
    engine.predict.side_effect = RuntimeError("boom")

    reg = _TFTModelRegistry()
    with caplog.at_level(logging.WARNING), patch(
        "core.ml.model_registry._gate_evaluate", return_value=_gate(True)
    ), patch("core.ml.model_registry.TFTInferenceEngine", return_value=engine):
        out = await reg.get_or_train("AAPL", _df())

    assert out is None
    assert any(r.levelno == logging.WARNING for r in caplog.records)


# ---------------------------------------------------------------------------
# 10. LRU move_to_end: a re-accessed symbol is protected from eviction
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_lru_move_to_end_protects_reaccessed(tmp_path, monkeypatch):
    from core.ml import model_registry as mr

    monkeypatch.setenv("TFT_MODELS_ROOT", str(tmp_path))
    syms = [f"S{i}" for i in range(mr._LRU_MAX)]  # fills exactly to cap
    for s in syms:
        _mk_checkpoint(tmp_path, s)
    _mk_checkpoint(tmp_path, "NEW")

    def _fresh_engine(*_a, **_k):
        e = MagicMock()
        e.load.return_value = True
        e.predict.return_value = SimpleNamespace(direction="up")
        return e

    reg = mr._TFTModelRegistry()
    with patch(
        "core.ml.model_registry._gate_evaluate", return_value=_gate(True)
    ), patch("core.ml.model_registry.TFTInferenceEngine", side_effect=_fresh_engine):
        for s in syms:
            await reg.get_or_train(s, _df())  # cache full; S0 is oldest
        await reg.get_or_train("S0", _df())  # re-access → move_to_end → S1 now oldest
        await reg.get_or_train("NEW", _df())  # evicts the oldest = S1, not S0

    assert "S0" in reg._engines
    assert "S1" not in reg._engines


# ---------------------------------------------------------------------------
# 11. Event-Loop Lock Re-Creation (Multi-Loop Restart Crash Test)
# ---------------------------------------------------------------------------
def test_event_loop_lock_recreation(tmp_path, monkeypatch):
    import asyncio

    from core.ml.model_registry import _TFTModelRegistry

    monkeypatch.setenv("TFT_MODELS_ROOT", str(tmp_path))
    reg = _TFTModelRegistry()

    def run_in_fresh_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # Should not crash with RuntimeError on the lock
            loop.run_until_complete(reg.get_or_train("AAPL", _df()))
        finally:
            loop.close()

    run_in_fresh_loop()
    run_in_fresh_loop()


# ---------------------------------------------------------------------------
# 12. Concurrency regression guards — S1-E2/DD-4 Inference Factory.
#     The per-(loop,symbol) lock relaxation was DEFERRED per the 2026-06-11
#     empirical audit (sequential N=1 measured fastest for this workload; the
#     factory semaphore is a memory bound, not a throughput lever). These
#     guards pin the invariants any future relaxation must preserve:
#     same-symbol inference NEVER overlaps, and the LRU bound holds even when
#     get_or_train is awaited concurrently.
# ---------------------------------------------------------------------------
class _Tracker:
    """Thread-safe concurrency tracker shared by the sleepy stub engines."""

    def __init__(self, sleep_s):
        self.lock = threading.Lock()
        self.sleep_s = sleep_s
        self.now = 0
        self.peak = 0
        self.intervals = []  # (symbol, start, end)
        self.threads = []


def _sleepy_engine_factory(tracker):
    def _make(symbol, _model_dir):
        e = MagicMock()
        e.load.return_value = True

        def _predict(_df_arg):
            with tracker.lock:
                tracker.now += 1
                tracker.peak = max(tracker.peak, tracker.now)
            start = time.perf_counter()
            time.sleep(tracker.sleep_s)
            end = time.perf_counter()
            with tracker.lock:
                tracker.now -= 1
                tracker.intervals.append((symbol, start, end))
                tracker.threads.append(threading.current_thread().name)
            return SimpleNamespace(direction="up", symbol=symbol)

        e.predict.side_effect = _predict
        return e

    return _make


class TestPerSymbolLockConcurrency:
    @pytest.mark.anyio
    async def test_same_symbol_still_serializes(self, tmp_path, monkeypatch):
        """Same-symbol predicts must NEVER overlap (thread-safety contract)."""
        from core.ml import model_registry as mr

        monkeypatch.setenv("TFT_MODELS_ROOT", str(tmp_path))
        _mk_checkpoint(tmp_path, "AAPL")
        tracker = _Tracker(sleep_s=0.02)
        reg = mr._TFTModelRegistry()
        with patch(
            "core.ml.model_registry._gate_evaluate", return_value=_gate(True)
        ), patch(
            "core.ml.model_registry.TFTInferenceEngine",
            side_effect=_sleepy_engine_factory(tracker),
        ):
            results = await asyncio.gather(
                *[reg.get_or_train("AAPL", _df()) for _ in range(3)]
            )
        assert all(r is not None for r in results)
        ivs = sorted([(s, e) for sym, s, e in tracker.intervals if sym == "AAPL"])
        assert len(ivs) == 3
        for (_s1, e1), (s2, _e2) in zip(ivs, ivs[1:]):
            assert s2 >= e1 - 1e-6, "same-symbol predicts overlapped — NOT serialized"

    @pytest.mark.anyio
    async def test_lru_bound_holds_under_concurrent_fanout(self, tmp_path, monkeypatch):
        """Resident engines never exceed _LRU_MAX even when 60 symbols load
        concurrently (E2.1's memory bound at the registry layer)."""
        from core.ml import model_registry as mr

        monkeypatch.setenv("TFT_MODELS_ROOT", str(tmp_path))
        syms = [f"L{i:02d}" for i in range(mr._LRU_MAX + 10)]
        for s in syms:
            _mk_checkpoint(tmp_path, s)
        tracker = _Tracker(sleep_s=0.001)
        reg = mr._TFTModelRegistry()
        with patch(
            "core.ml.model_registry._gate_evaluate", return_value=_gate(True)
        ), patch(
            "core.ml.model_registry.TFTInferenceEngine",
            side_effect=_sleepy_engine_factory(tracker),
        ):
            results = await asyncio.gather(*[reg.get_or_train(s, _df()) for s in syms])
        assert all(r is not None for r in results)
        assert len(reg._engines) <= mr._LRU_MAX
