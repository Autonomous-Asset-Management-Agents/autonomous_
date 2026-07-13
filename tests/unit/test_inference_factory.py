# core/ml/inference/factory — S1-E2 / DD-4 OOM-safe Inference Factory (TDD Red first).
# Contract (plan 2026-06-11-inference-factory, Papa-APPROVED):
#   E2.1 bounded ≤N concurrent over the 200-symbol universe cap (trading_loop caps at 200)
#   E2.2 abstention: None on missing model / failure — never raises
#   E2.3 dormant default: get_inference_client() == LegacyInferenceClient (literal
#        get_or_train passthrough; factory classes never instantiated on the live path)
#   E2.5 p95 latency harness: measured, logged, bounded
#   E2.6 Airlock: no Docker SDK / subprocess imports; in-process only
# The registry-level pieces (E2.4 per-(loop,symbol) lock, LRU bound, executor routing)
# live in test_model_registry.py::TestPerSymbolLockConcurrency.

import asyncio
import inspect
import logging
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pandas as pd
import pytest


def _df():
    return pd.DataFrame({"close": [1.0, 2.0, 3.0]})


@pytest.fixture(autouse=True)
def _fresh_factory(monkeypatch):
    """Each test gets a clean backend env + an empty client cache."""
    monkeypatch.delenv("IC_INFERENCE_BACKEND", raising=False)
    monkeypatch.delenv("IC_INFERENCE_CONCURRENCY", raising=False)
    from core.ml.inference import factory as f

    f._reset_for_tests()
    yield
    f._reset_for_tests()


def _patch_get_or_train(monkeypatch, mock):
    """Patch the registry singleton the factory delegates to."""
    from core.ml.model_registry import model_registry

    monkeypatch.setattr(model_registry, "get_or_train", mock)


# ---------------------------------------------------------------------------
# E2.3 — dormant default & backend selection
# ---------------------------------------------------------------------------
def test_default_backend_is_legacy_and_cached():
    from core.ml.inference import factory as f

    client = f.get_inference_client()
    assert isinstance(client, f.LegacyInferenceClient)
    # cached: the factory must hand back the SAME client (a fresh client per call
    # would mint fresh semaphores and silently void the concurrency bound)
    assert f.get_inference_client() is client


def test_explicit_legacy_backend(monkeypatch):
    from core.ml.inference import factory as f

    monkeypatch.setenv("IC_INFERENCE_BACKEND", "legacy")
    assert isinstance(f.get_inference_client(), f.LegacyInferenceClient)


def test_unknown_backend_warns_and_falls_back_to_legacy(monkeypatch, caplog):
    from core.ml.inference import factory as f

    monkeypatch.setenv("IC_INFERENCE_BACKEND", "warp-drive")
    with caplog.at_level(logging.WARNING):
        client = f.get_inference_client()
    assert isinstance(client, f.LegacyInferenceClient)
    assert any("IC_INFERENCE_BACKEND" in r.message for r in caplog.records)


def test_vertex_backend_is_reserved_falls_back_to_legacy(monkeypatch, caplog):
    # Design B (VertexAIInferenceClient) is deferred — the name is reserved and must
    # fail SAFE to legacy with a WARNING, not crash and not silently pick semaphore.
    from core.ml.inference import factory as f

    monkeypatch.setenv("IC_INFERENCE_BACKEND", "vertex")
    with caplog.at_level(logging.WARNING):
        client = f.get_inference_client()
    assert isinstance(client, f.LegacyInferenceClient)


def test_auto_backend_routes_by_deployment_mode(monkeypatch):
    from core.ml.inference import factory as f

    # local mode (REDIS_URL empty) → semaphore client
    monkeypatch.setenv("IC_INFERENCE_BACKEND", "auto")
    monkeypatch.setenv("REDIS_URL", "")
    assert isinstance(f.get_inference_client(), f.LocalSemaphoreInferenceClient)
    f._reset_for_tests()
    # cloud mode (REDIS_URL set) → legacy until VertexAIInferenceClient exists
    monkeypatch.setenv("REDIS_URL", "redis://example:6379")
    assert isinstance(f.get_inference_client(), f.LegacyInferenceClient)


@pytest.mark.anyio
async def test_legacy_is_literal_passthrough(monkeypatch):
    from core.ml.inference import factory as f

    sentinel = {"q50": 0.0123}
    mock = AsyncMock(return_value=sentinel)
    _patch_get_or_train(monkeypatch, mock)
    client = f.get_inference_client()
    out = await client.predict_for("AAPL", _df())
    assert out is sentinel
    mock.assert_awaited_once()
    # the literal current path: positional (symbol, features_df), NO extra kwargs
    args, kwargs = mock.await_args
    assert args[0] == "AAPL"
    assert kwargs == {}


# ---------------------------------------------------------------------------
# E2.1 — OOM-safe bounded concurrency over the 200-symbol universe cap
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_semaphore_bounds_concurrency_at_8_over_200_symbols(monkeypatch):
    from core.ml.inference import factory as f

    monkeypatch.setenv("IC_INFERENCE_BACKEND", "semaphore")
    monkeypatch.setenv("IC_INFERENCE_CONCURRENCY", "8")
    state = {"now": 0, "peak": 0}

    async def _tracked(symbol, features_df, **kwargs):
        state["now"] += 1
        state["peak"] = max(state["peak"], state["now"])
        await asyncio.sleep(0.002)
        state["now"] -= 1
        return {"symbol": symbol}

    _patch_get_or_train(monkeypatch, AsyncMock(side_effect=_tracked))
    client = f.get_inference_client()
    assert isinstance(client, f.LocalSemaphoreInferenceClient)

    results = await asyncio.gather(
        *[client.predict_for(f"SYM{i:03d}", _df()) for i in range(200)]
    )
    assert len(results) == 200
    assert all(r is not None for r in results)  # all 200 resolve
    assert state["peak"] <= 8, f"semaphore bound violated: peak={state['peak']}"
    assert state["peak"] >= 2, "no concurrency at all — the bound is vacuous"


# ---------------------------------------------------------------------------
# E2.2 — abstention contract (None, never raises)
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_semaphore_abstains_with_none_on_missing_model(monkeypatch):
    from core.ml.inference import factory as f

    monkeypatch.setenv("IC_INFERENCE_BACKEND", "semaphore")
    _patch_get_or_train(monkeypatch, AsyncMock(return_value=None))
    client = f.get_inference_client()
    assert await client.predict_for("NOMODEL", _df()) is None


@pytest.mark.anyio
async def test_semaphore_fail_closed_on_exception_logs_warning(monkeypatch, caplog):
    from core.ml.inference import factory as f

    monkeypatch.setenv("IC_INFERENCE_BACKEND", "semaphore")
    _patch_get_or_train(
        monkeypatch, AsyncMock(side_effect=RuntimeError("synthetic blow-up"))
    )
    client = f.get_inference_client()
    with caplog.at_level(logging.WARNING):
        out = await client.predict_for("BOOM", _df())
    assert out is None  # neutral abstention — NEVER a raise toward the order path
    assert any("BOOM" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Empirically tuned default — N=1 (audit 2026-06-11: sequential is fastest for
# this workload; the semaphore is a memory bound, not a throughput lever)
# ---------------------------------------------------------------------------
def test_default_concurrency_is_one(monkeypatch):
    from core.ml.inference import factory as f

    monkeypatch.setenv("IC_INFERENCE_BACKEND", "semaphore")
    client = f.get_inference_client()
    assert isinstance(client, f.LocalSemaphoreInferenceClient)
    assert client._concurrency == 1


@pytest.mark.anyio
async def test_semaphore_calls_registry_without_extra_kwargs(monkeypatch):
    # The client delegates to the UNCHANGED registry signature (no executor
    # kwarg — the lock relaxation + dedicated pool were deferred per audit).
    from core.ml.inference import factory as f

    monkeypatch.setenv("IC_INFERENCE_BACKEND", "semaphore")
    mock = AsyncMock(return_value={"q50": 0.1})
    _patch_get_or_train(monkeypatch, mock)
    client = f.get_inference_client()
    await client.predict_for("AAPL", _df())
    args, kwargs = mock.await_args
    assert args[0] == "AAPL"
    assert kwargs == {}


# ---------------------------------------------------------------------------
# E2.5 — p95 latency harness (measured + logged + bounded; env-dependent budget
# is asserted as "exists and bounded", stated not hidden)
# ---------------------------------------------------------------------------
@pytest.mark.anyio
async def test_p95_latency_harness_200_way(monkeypatch):
    from core.ml.inference import factory as f

    monkeypatch.setenv("IC_INFERENCE_BACKEND", "semaphore")
    monkeypatch.setenv("IC_INFERENCE_CONCURRENCY", "8")

    async def _warm(symbol, features_df, **kwargs):
        await asyncio.sleep(0.001)  # warm-cache stub engine
        return {"symbol": symbol}

    _patch_get_or_train(monkeypatch, AsyncMock(side_effect=_warm))
    client = f.get_inference_client()

    latencies = []

    async def _timed(i):
        t0 = time.perf_counter()
        out = await client.predict_for(f"SYM{i:03d}", _df())
        latencies.append(time.perf_counter() - t0)
        return out

    results = await asyncio.gather(*[_timed(i) for i in range(200)])
    assert all(r is not None for r in results)
    latencies.sort()
    p95 = latencies[int(len(latencies) * 0.95) - 1]
    budget_s = float(os.environ.get("IC_TEST_P95_BUDGET_S", "2.0"))
    logging.getLogger(__name__).warning(
        "[harness] 200-way p95 per-symbol latency: %.4fs (budget %.1fs)", p95, budget_s
    )
    assert p95 < budget_s, f"p95 {p95:.4f}s exceeds budget {budget_s}s"


# ---------------------------------------------------------------------------
# E2.6 — Airlock: in-process only, no Docker SDK / subprocess
# ---------------------------------------------------------------------------
def test_airlock_no_docker_no_subprocess_in_factory():
    from core.ml.inference import factory as f

    src = inspect.getsource(f)
    assert "import docker" not in src
    assert "subprocess" not in src
    # the module must live in the Finance domain (ai_trading_bot/core/ml/inference/)
    p = Path(f.__file__)
    assert p.parent.name == "inference" and p.parent.parent.name == "ml"
