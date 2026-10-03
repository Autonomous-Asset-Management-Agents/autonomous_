"""Inference Factory (S1-E2 / DD-4) — the OOM-safe bounded-concurrency seam.

Why this exists (plan ``2026-06-11-inference-factory``, Papa-APPROVED; defaults
re-tuned per the 2026-06-11 empirical audit): the universe fan-out
(``trading_loop``, capped at 200 symbols) does not reach TFT inference today.
The moment per-symbol TFT joins a concurrent fan-out, an UNBOUNDED path would
load up to ~200 engines at once (OOM risk on the 8 GB box). This module is the
seam that bounds it.

**The semaphore is a MEMORY bound, not a throughput lever.** Measured on the
target i7-11700K (16 real checkpoints through the production predict path,
2026-06-11): sequential execution with torch's default intra-op threads is the
fastest configuration; every thread-pool variant was equal or worse (pool=8:
0.84x). Root cause: ~85% of each predict call is GIL-held Lightning-Trainer
construction inside ``BaseModel.predict`` — only ~3% is torch compute. Hence
``IC_INFERENCE_CONCURRENCY`` defaults to **1**; the real throughput lever is
the (separate, future) ``model.predict``-bypass brick (~6.6x). Do NOT call
``torch.set_num_threads`` at boot — torch's default intra-op parallelism is
what makes the sequential path fast.

* ``LegacyInferenceClient`` — the **default** (``IC_INFERENCE_BACKEND=legacy``):
  the literal current ``model_registry.get_or_train`` call. Byte-identical;
  the other clients are never instantiated on the live path.
* ``LocalSemaphoreInferenceClient`` — Design A: a per-loop
  ``asyncio.Semaphore(IC_INFERENCE_CONCURRENCY, default 1)`` bounds concurrent
  inference (the OOM guarantee for any future fan-out consumer); every failure
  maps to the **None → neutral abstention** contract (never raises toward the
  order path).
* ``vertex`` — reserved name, fails safe to ``legacy`` with a WARNING. Per the
  audited ADR it is **rejected-with-evidence** for TFT serving (per-endpoint
  economics for ~500 small CPU models); the cloud serving story is lazy GCS
  fetch + the registry's LRU.

Airlock (CLAUDE §5.7 / ADR-AUT9): in-process only — no Docker SDK, no
sub-process spawning. Cancel-safety: ``predict_for`` awaits are cancellable, so
a consumer's per-symbol ``asyncio.wait_for`` timeout yields HOLD cleanly.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Optional, Protocol, runtime_checkable

from core.ml.model_registry import model_registry

if TYPE_CHECKING:  # typing only — keeps the module import-light
    import pandas as pd

    from core.ml.tft_inference import TFTPrediction

logger = logging.getLogger(__name__)

# Default 1 (empirical audit 2026-06-11, supersedes the plan's memory-math 8):
# sequential is the FASTEST configuration for this workload (pool=8 measured at
# 0.84x sequential; 85% of each call is GIL-held Lightning plumbing), and N=1
# also dominates on memory. The semaphore exists as the OOM bound for any
# future concurrent consumer; raising N is an activation-gate decision that
# requires a fresh benchmark (and only makes sense after the predict-bypass).
# Papa re-blessing note (2026-06-11): at N=1 the semaphore is functionally a
# lock. The configurable bound exists so a future model.predict bypass (6.6x
# throughput) or architecture change can raise N without caller changes.
_DEFAULT_CONCURRENCY = 1

_VALID_BACKENDS = ("legacy", "semaphore", "auto", "vertex")


def _is_local_mode() -> bool:
    """Desktop/local mode = no Redis configured (mirror of
    ``core.redis_client._is_local_mode``, redis_client.py:41 — duplicated to
    keep this module import-light and free of the Redis dependency chain)."""
    return not os.environ.get("REDIS_URL", "").strip()


@runtime_checkable
class InferenceClient(Protocol):
    """The seam every backend implements. Shaped for Design B (Vertex /
    batching) to slot in later WITHOUT touching callers."""

    async def predict_for(
        self, symbol: str, features_df: "pd.DataFrame"
    ) -> Optional["TFTPrediction"]: ...  # pragma: no cover — Protocol signature


class LegacyInferenceClient:
    """The literal current path — byte-identical default (E2.3).

    No extra concurrency control, no extra kwargs: exactly what
    ``stock_specialist._fetch_ml_prediction`` does today.
    """

    async def predict_for(
        self, symbol: str, features_df: "pd.DataFrame"
    ) -> Optional["TFTPrediction"]:
        return await model_registry.get_or_train(symbol, features_df)


class LocalSemaphoreInferenceClient:
    """Design A — semaphore-bounded, abstaining client (the OOM bound).

    The semaphore is per event loop (asyncio primitives must not be shared
    across loops — the engine runs both a main loop and the specialist
    registry's background-thread loop). Inference itself stays on the
    registry's existing path (``get_or_train`` → ``asyncio.to_thread``), whose
    per-loop lock serializes inference — consistent with the measured optimum
    of N=1 for this workload.
    """

    def __init__(self, concurrency: Optional[int] = None) -> None:
        if concurrency is None:
            raw = os.environ.get("IC_INFERENCE_CONCURRENCY", "")
            try:
                concurrency = int(raw) if raw.strip() else _DEFAULT_CONCURRENCY
            except ValueError:
                logger.warning(
                    "[InferenceFactory] invalid IC_INFERENCE_CONCURRENCY=%r — "
                    "falling back to %d",
                    raw,
                    _DEFAULT_CONCURRENCY,
                )
                concurrency = _DEFAULT_CONCURRENCY
        self._concurrency = max(1, concurrency)
        self._per_loop_semaphores: dict[int, asyncio.Semaphore] = {}

    def _semaphore(self) -> asyncio.Semaphore:
        loop_id = id(asyncio.get_running_loop())
        sem = self._per_loop_semaphores.get(loop_id)
        if sem is None:
            sem = asyncio.Semaphore(self._concurrency)
            self._per_loop_semaphores[loop_id] = sem
        return sem

    async def predict_for(
        self, symbol: str, features_df: "pd.DataFrame"
    ) -> Optional["TFTPrediction"]:
        try:
            async with self._semaphore():
                return await model_registry.get_or_train(symbol, features_df)
        except Exception as exc:
            # Fail-closed (§5.6 WARNING, never DEBUG): the neutral abstention —
            # the consumer maps None → ml_direction='unavailable' / weight 0.
            # NEVER re-raise toward a path that can reach order_executor.
            logger.warning(
                "[InferenceFactory] predict_for(%s) failed → neutral abstention: %s",
                symbol,
                exc,
            )
            return None


# Cached per backend: a fresh client per call would mint fresh semaphores and
# silently void the concurrency bound.
_client_cache: dict[str, InferenceClient] = {}


def get_inference_client() -> InferenceClient:
    """Resolve the inference backend (dormant default: ``legacy``).

    ``IC_INFERENCE_BACKEND``: ``legacy`` (default) | ``semaphore`` | ``auto``
    (deployment-aware: local → semaphore, cloud → legacy until the Vertex
    client exists) | ``vertex`` (reserved — Design B, falls safe to legacy).
    Unknown values fall back to ``legacy`` at WARNING.
    """
    backend = os.environ.get("IC_INFERENCE_BACKEND", "legacy").strip().lower()
    if not backend:
        backend = "legacy"
    if backend not in _VALID_BACKENDS:
        logger.warning(
            "[InferenceFactory] unknown IC_INFERENCE_BACKEND=%r — legacy fallback",
            backend,
        )
        backend = "legacy"
    if backend == "vertex":
        logger.warning(
            "[InferenceFactory] IC_INFERENCE_BACKEND=vertex is reserved (Design B, "
            "deferred) — legacy fallback"
        )
        backend = "legacy"
    if backend == "auto":
        backend = "semaphore" if _is_local_mode() else "legacy"

    client = _client_cache.get(backend)
    if client is None:
        if backend == "semaphore":
            client = LocalSemaphoreInferenceClient()
            logger.info(
                "[InferenceFactory] LocalSemaphoreInferenceClient active "
                "(concurrency=%d)",
                client._concurrency,
            )
        else:
            client = LegacyInferenceClient()
        _client_cache[backend] = client
    return client


def _reset_for_tests() -> None:
    """Test hook: drop cached clients and shut down their executors."""
    for client in _client_cache.values():
        executor = getattr(client, "_executor", None)
        if executor is not None:
            executor.shutdown(wait=False)
    _client_cache.clear()
