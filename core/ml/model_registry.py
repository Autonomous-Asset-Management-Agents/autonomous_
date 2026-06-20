# Copyright 2026 Andreas Apeldorn, Georg Apeldorn / Autonomous Asset Management Agents UG
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Model registry — singleton inference router for the per-symbol TFT specialist.

Caller contract::

    from core.ml.model_registry import model_registry
    prediction = await model_registry.get_or_train(symbol, features_df)
    # prediction is a TFTPrediction or None

The name ``get_or_train`` is historical — this implementation never trains.
Training happens offline; the registry only LOADS existing checkpoints from
``<TFT_MODELS_ROOT>/<SYM>/`` and routes inference. Every error path returns
``None``; the caller treats that as "no ML signal" and leaves
``SpecialistReport.ml_direction = "unavailable"``.

**Fusion note (dormant, W-4 updated):** the specialist wiring imports this
module flag-gated (``stock_specialist._fetch_ml_prediction`` behind
``ML_PREDICTION_ENABLED`` default False, #1139), and the inference factory
(``core/ml/inference``, behind ``IC_INFERENCE_BACKEND`` default ``legacy``)
delegates to it — both dormant by default. Importing is side-effect-free:
``__init__`` only builds empty in-memory containers, no torch import (pytorch
is imported lazily inside ``TFTInferenceEngine.load``) and no disk access at
import time.

**Concurrency (W-5):** ``get_or_train`` is async; the blocking PyTorch
load+predict runs via ``asyncio.to_thread`` so it never blocks the event loop,
and an ``asyncio.Lock`` serializes inference (pytorch-forecasting ``predict``
is not thread-safe and rebuilds a DataLoader each call). The singleton assumes
**single-event-loop** access (the specialist registry's background-thread loop
calls it strictly sequentially); a multi-loop / cloud inference path is a
separate Inference-Factory epic and would need a per-loop lock.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from collections import OrderedDict
from pathlib import Path
from typing import Optional

import pandas as pd

from config import get_config
from core.ml.quality_gate import evaluate_and_log as _gate_evaluate
from core.ml.quality_gate import stats as _gate_stats
from core.ml.tft_inference import TFTInferenceEngine, TFTPrediction

logger = logging.getLogger(__name__)

# LRU cap. Each engine holds a TFT model + training_ds ≈ ~3 MB on CPU, so
# 50 cached engines ≈ ~150 MB peak. The universe is ~500 symbols and refresh
# traffic is dominated by high-priority symbols, so a 50-entry LRU hits well.
_LRU_MAX = 50


def _models_root() -> Path:
    """Per-symbol checkpoint root: ``<TFT_MODELS_ROOT>/<SYM>/`` when configured, else
    module-relative ``core/ml/models/``. Section 2.10: read via ``get_config()`` (the
    single config surface, parity-mirrored in config.oss.py) — NEVER ``os.getenv`` in
    core. Read each call — cheap, and keeps tests hermetic via a patched get_config."""
    override = get_config().TFT_MODELS_ROOT
    return Path(override) if override else Path(__file__).parent / "models"


def _manifest_path() -> Path:
    """Provenance manifest location: ``TFT_MANIFEST_PATH`` if set, else
    ``<TFT_MODELS_ROOT>/tft_models_manifest.json`` (provisioned alongside the
    checkpoints by the model-provenance epic)."""
    override = os.getenv("TFT_MANIFEST_PATH")
    return Path(override) if override else _models_root() / "tft_models_manifest.json"


def _require_manifest() -> bool:
    """Whether an ABSENT manifest is fatal. Explicit ``TFT_REQUIRE_MANIFEST`` wins;
    otherwise strict everywhere EXCEPT ``DEPLOYMENT_MODE=LOCAL`` (mirrors the
    quality_gate IC-floor split) so local dev without provisioned checkpoints works."""
    override = os.getenv("TFT_REQUIRE_MANIFEST")
    if override is not None:
        return override.strip().lower() in ("1", "true", "yes")
    return os.environ.get("DEPLOYMENT_MODE", "").upper() != "LOCAL"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    """SHA-256 of an in-memory buffer (D3: hash the read-once bytes, not the path)."""
    return hashlib.sha256(data).hexdigest()


class _TFTModelRegistry:
    def __init__(self) -> None:
        self._engines: OrderedDict[str, TFTInferenceEngine] = OrderedDict()
        self._known_missing: set[str] = set()
        self._per_loop_locks: dict[int, asyncio.Lock] = {}
        # Provenance manifest (RF-3 verify-before-load), parsed + indexed lazily on the
        # first verify and cached for the process — a re-provisioned manifest needs a
        # restart, matching the gate-cache semantics below.
        self._manifest_index: Optional[dict] = None
        self._manifest_present: bool = False

    def _load_manifest_index(self) -> dict:
        """Lazily parse the provenance manifest into ``{symbol: [entry, …]}`` and cache
        it; sets ``self._manifest_present``. Returns ``{}`` when no readable manifest.
        """
        if self._manifest_index is not None:
            return self._manifest_index
        index: dict = {}
        path = _manifest_path()
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                for entry in data.get("models", []):
                    index.setdefault(entry.get("symbol"), []).append(entry)
                self._manifest_present = True
            except Exception as exc:
                logger.warning(
                    "[ModelRegistry] provenance manifest %s unreadable → treating as "
                    "absent: %s",
                    path,
                    exc,
                )
                self._manifest_present = False
        else:
            self._manifest_present = False
        self._manifest_index = index
        return index

    def _verify_before_load(
        self, symbol: str, model_dir: Path, engine: TFTInferenceEngine
    ) -> bool:
        """RF-3: SHA-256-verify the artifacts the engine will UNPICKLE — ``checkpoint.pt``
        (``torch.load(weights_only=False)``) and the matched ``training_ds`` (``pickle.load``,
        W-4) — against the provenance manifest BEFORE the load. Returns False (→ caller
        fail-closes to None) on any mismatch / absence. Only paths INSIDE the models root
        are hashed (traversal guard)."""
        self._load_manifest_index()
        if not self._manifest_present:
            if _require_manifest():
                logger.warning(
                    "[ModelRegistry] no provenance manifest at %s and "
                    "TFT_REQUIRE_MANIFEST → refusing %s (RF-3 fail-closed)",
                    _manifest_path(),
                    symbol,
                )
                return False
            logger.warning(
                "[ModelRegistry] no provenance manifest — loading %s UNVERIFIED "
                "(local-dev; set TFT_REQUIRE_MANIFEST=1 to enforce)",
                symbol,
            )
            return True

        # Everything below is wrapped so ANY unexpected error (resolver / hashing /
        # stat raising, a file deleted mid-flight, a malformed entry) fails CLOSED —
        # an exception must never skip the gate and reach the unpickle.
        try:
            entries = (self._manifest_index or {}).get(symbol, [])
            if not entries:
                logger.warning(
                    "[ModelRegistry] %s absent from provenance manifest → refusing "
                    "(RF-3)",
                    symbol,
                )
                return False

            root = _models_root().resolve()
            expected: dict = {}
            for entry in entries:
                filename = entry.get("filename") or ""
                sha = entry.get("sha256")
                if not filename or not sha:
                    logger.warning(
                        "[ModelRegistry] %s: malformed manifest entry (missing "
                        "filename/sha256) → refusing",
                        symbol,
                    )
                    return False
                local = (root / filename).resolve()
                if not local.is_relative_to(root):
                    logger.warning(
                        "[ModelRegistry] %s: manifest path escapes models root — "
                        "refusing",
                        symbol,
                    )
                    return False
                expected[local] = sha

            # Verify EXACTLY the two files the engine will unpickle: checkpoint + the
            # resolved training_ds (ADR-ML-DS-01). D3 (TOCTOU close): read each artifact's
            # bytes ONCE, hash the BUFFER (not the path), and PIN the verified buffers on
            # the engine — load() then unpickles exactly these bytes, so swapping the
            # underlying file in the bucket AFTER the hash (real over a read-only FUSE
            # mount) is wirkungslos. Replaces the earlier _pinned_ds_path (path-only) pin.
            ckpt_path = (model_dir / "checkpoint.pt").resolve()
            ds_path = engine._resolve_training_ds_path()
            ds_resolved = Path(ds_path).resolve() if ds_path is not None else None
            required = [ckpt_path] + ([ds_resolved] if ds_resolved is not None else [])

            verified: dict = {}
            for art in required:
                want = expected.get(art)
                if want is None:
                    logger.warning(
                        "[ModelRegistry] %s: %s not covered by manifest → refusing "
                        "(RF-3/W-4)",
                        symbol,
                        art.name,
                    )
                    return False
                try:
                    data = art.read_bytes()  # read ONCE
                except OSError as exc:
                    logger.warning(
                        "[ModelRegistry] %s: read failed for %s → refusing "
                        "(RF-3 fail-closed): %s",
                        symbol,
                        art.name,
                        exc,
                    )
                    return False
                if _sha256_bytes(data) != want:
                    logger.warning(
                        "[ModelRegistry] %s: SHA-256 verify FAILED for %s → refusing "
                        "(RF-3)",
                        symbol,
                        art.name,
                    )
                    return False
                verified[art] = data

            # Pin the read-once verified buffers; load() unpickles ONLY these (never
            # re-opens the path). DS is None only when the engine resolved no ds path.
            engine._pinned_ckpt_bytes = verified.get(ckpt_path)
            engine._pinned_ds_bytes = (
                verified.get(ds_resolved) if ds_resolved is not None else None
            )
            return True
        except Exception as exc:
            logger.warning(
                "[ModelRegistry] verify-before-load errored for %s → refusing "
                "(RF-3 fail-closed): %s",
                symbol,
                exc,
            )
            return False

    async def _ensure_engine(self, symbol: str) -> Optional[TFTInferenceEngine]:
        """Resolve (and cache) the per-symbol engine. Caller MUST hold ``self._lock``."""
        if symbol in self._known_missing:
            return None
        if symbol in self._engines:
            self._engines.move_to_end(symbol)
            return self._engines[symbol]

        model_dir = _models_root() / symbol
        if not (model_dir / "checkpoint.pt").exists():
            # Expected absence for symbols without a per-symbol model (~half the
            # universe) — NOT an error fallback, cached once. Genuine degradations
            # (gate/load failure) below log at WARNING per §5.6.
            self._known_missing.add(symbol)
            logger.debug(
                "[ModelRegistry] no checkpoint for %s — marking missing", symbol
            )
            return None

        # Quality gate: refuse broken / unvalidated checkpoints (additive guard —
        # gate-failing symbols join _known_missing exactly like missing ones).
        # NOTE: gate failures are cached until the process restarts; a newly
        # promoted checkpoint is picked up on the next restart. Runtime
        # invalidation is out of scope here and tracked with the model-provenance
        # epic (which owns checkpoint promotion).
        gate = _gate_evaluate(symbol, model_dir)
        if not gate.passed:
            self._known_missing.add(symbol)
            logger.warning(
                "[ModelRegistry] quality gate FAILED for %s → rule fallback: %s",
                symbol,
                gate.reason,
            )
            return None

        engine = TFTInferenceEngine(symbol, model_dir)
        # ADR-SEC (RF-3, CLOSED): TFTInferenceEngine.load() uses torch.load(weights_only=
        # False) — REQUIRED by pytorch-forecasting's TemporalFusionTransformer (it unpickles
        # the full module + TimeSeriesDataSet; weights_only=True cannot load it) — and the
        # matched training_ds via pickle.load (equal RCE risk, W-4). Both are SHA-256-verified
        # against the model-provenance manifest BEFORE the load below; an unverified /
        # mismatching artifact is refused (fail-closed), so the dangerous unpickle is never
        # reached on a tampered file.
        if not self._verify_before_load(symbol, model_dir, engine):
            self._known_missing.add(symbol)
            return None
        loaded = await asyncio.to_thread(engine.load)
        if not loaded:
            self._known_missing.add(symbol)
            logger.warning(
                "[ModelRegistry] per-symbol load FAILED for %s → rule fallback: %s",
                symbol,
                getattr(engine, "_load_error", None),
            )
            return None

        self._engines[symbol] = engine
        while len(self._engines) > _LRU_MAX:
            evicted_sym, _ = self._engines.popitem(last=False)
            logger.debug("[ModelRegistry] LRU evicted %s", evicted_sym)
        return engine

    async def get_or_train(
        self, symbol: str, features_df: pd.DataFrame
    ) -> Optional[TFTPrediction]:
        """Async entrypoint. Returns a ``TFTPrediction`` or ``None`` on any failure.

        The lock is held across both the (blocking, thread-offloaded) load and
        predict so inference is serialized — matching pytorch-forecasting's
        non-thread-safe predict path.
        """
        if not symbol or features_df is None or features_df.empty:
            return None
        try:
            loop = asyncio.get_running_loop()
            loop_id = id(loop)
            if loop_id not in self._per_loop_locks:
                self._per_loop_locks[loop_id] = asyncio.Lock()
            lock = self._per_loop_locks[loop_id]

            async with lock:
                engine = await self._ensure_engine(symbol)
                if engine is None:
                    return None
                return await asyncio.to_thread(engine.predict, features_df)
        except Exception as exc:
            # Honour the "every error path returns None" contract even on an
            # unexpected failure (e.g. predict() raising, or a cross-event-loop
            # lock mismatch if the single-loop assumption is ever violated).
            logger.warning(
                "[ModelRegistry] get_or_train failed for %s → rule fallback: %s",
                symbol,
                exc,
            )
            return None

    def coverage(self) -> dict:
        """Diagnostic: how many symbols have checkpoints vs are loaded/missing."""
        try:
            root = _models_root()
            all_dirs = (
                [p.name for p in root.iterdir() if p.is_dir()] if root.exists() else []
            )
            with_ckpt = [
                name for name in all_dirs if (root / name / "checkpoint.pt").exists()
            ]
            return {
                "per_symbol_models_root": str(root),
                "per_symbol_total_dirs": len(all_dirs),
                "per_symbol_with_checkpoint": len(with_ckpt),
                "per_symbol_loaded_cached": len(self._engines),
                "per_symbol_known_missing": len(self._known_missing),
                "quality_gate": _gate_stats(),
            }
        except Exception as exc:  # diagnostic only — never raise, but never silent
            logger.warning("[ModelRegistry] coverage() failed: %s", exc)
            return {"error": str(exc)}


# Module-level singleton (dormant until the specialist wiring brick imports it).
model_registry = _TFTModelRegistry()

__all__ = ["model_registry"]
