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

# core/round_table/meta_label.py
# #1955 TRD-4 — Meta-Labeling (López de Prado, AFML Ch. 3.6): the SECONDARY
# model that decides Trade/No-Trade over the primary model's BUY signals.
#
# The primary model (Round-Table consensus > SIGNAL_BUY_THRESHOLD) says the
# DIRECTION; this filter says whether THIS particular BUY has net-of-cost edge.
# It can only ever DROP a BUY (→ audited no_trade via the runner's existing
# approved=False plumbing) — it can never flip a direction and never touches
# SELL/HOLD (the runner gate carves those out before calling in).
#
# Dormant by default: META_LABEL_FILTER_ENABLED=False ⇒ never imported on the
# hot path (runner short-circuits) ⇒ byte-identical behaviour (BORA §9).
#
# Observation mode default when unconfigured: a missing/corrupt/incompatible model
# artifact passes through without dropping BUYs. When META_LABEL_REQUIRE_MODEL=True,
# any model or feature defect fails CLOSED (trade=False).
# Config is read via config.get_config() only —
# never from environment variables in the finance-core (CODING_POLICY §2.10).

from __future__ import annotations

import hashlib
import logging
import pickle
from pathlib import Path
from typing import Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ADR-ML-META-02 (#1955): feature whitelist = {RTR-0 harness available} ∩ {live
# REALLY filled}. Live forensics (13.–22.07.2026, 8017 decisions) proved that
# rsi_14=50, atr_14d=0, lstm_prediction=0 and rl_raw_action=0 are 100 %
# neutral-default on the desktop — a model trained on them learns NOISE and
# cannot generalise. Only the consensus score and the DrawdownGuard/Regime
# conditioner votes are real at inference time today. Extending this tuple
# requires PROOF the field is really filled live (#2388/#2389) AND a retrained
# artifact — the loader rejects any artifact whose feature list differs.
FEATURE_WHITELIST: Tuple[str, ...] = (
    "consensus_score",
    "drawdown_score",
    "regime_score",
)

# Whitelist feature → vote agent (class names, matching VoteResult.agent_name).
_DRAWDOWN_AGENT = "DrawdownGuardAgent"
_REGIME_AGENT = "RegimeDetectionAgent"

# Artifact contract (written by core/analysis/attribution/meta_label_training.py).
_ARTIFACT_SCHEMA_VERSION = 1


def extract_features(
    consensus_score: float, valid_votes: list
) -> Optional[List[float]]:
    """Feature vector in FEATURE_WHITELIST order, or ``None`` when a whitelist
    vote is missing.

    NEVER neutral-fills a missing feature (plan §12.2): a fabricated 0.5 would
    be exactly the neutral-default leak the whitelist exists to prevent.
    """
    drawdown = next(
        (v for v in valid_votes if getattr(v, "agent_name", "") == _DRAWDOWN_AGENT),
        None,
    )
    regime = next(
        (v for v in valid_votes if getattr(v, "agent_name", "") == _REGIME_AGENT),
        None,
    )
    if drawdown is None or regime is None:
        return None
    return [float(consensus_score), float(drawdown.score), float(regime.score)]


class MetaLabelFilter:
    """Lazy-loading Trade/No-Trade filter over the meta-label model artifact.

    The artifact (``META_LABEL_MODEL_PATH``) is a pickle of
    ``{"schema_version": 1, "model": <sklearn predict_proba estimator>,
    "features": list(FEATURE_WHITELIST), ...}``. Load results (success AND
    failure) are cached per path so a broken artifact warns once, not once per
    BUY symbol per cycle (latency + log-flood guard; cycle median is ~15 s).
    """

    def __init__(self) -> None:
        self._model: Optional[Any] = None
        self._loaded_path: Optional[str] = None
        self._failed_path: Optional[str] = None

    # -- artifact loading ---------------------------------------------------
    def _load_model(self, path: str) -> Optional[Any]:
        if self._loaded_path == path and self._model is not None:
            return self._model
        if self._failed_path == path:
            return None  # already warned for this path
        self._model = None
        self._loaded_path = None
        try:
            artifact_path = Path(path)
            raw = artifact_path.read_bytes()
            # ADR-SEC-ML2 (#2418 review P1): optional SHA256 sidecar pin.
            # If `<artifact>.sha256` exists (sha256sum format — first token =
            # hex digest), the artifact bytes MUST match it; mismatch ⇒ the
            # artifact is unusable (existing fail path, WARNING). No sidecar ⇒
            # load as before, but flag the unpinned artifact once (per-path
            # load cache — no per-BUY log flood).
            sidecar = artifact_path.with_name(artifact_path.name + ".sha256")
            if sidecar.exists():
                expected = (
                    sidecar.read_text(encoding="utf-8").split()[0].strip().lower()
                )
                actual = hashlib.sha256(raw).hexdigest()
                if actual != expected:
                    raise ValueError(
                        f"sha256 mismatch: artifact {actual} != pinned {expected}"
                    )
            else:
                logger.warning(
                    "MetaLabelFilter: no %s sidecar — loading UNPINNED artifact %r.",
                    sidecar.name,
                    path,
                )
            # ADR-SEC-ML1: pickle accepted because — operator-provisioned LOCAL
            # artifact, feature dark by default (META_LABEL_FILTER_ENABLED=False),
            # path from config not user input, no network source; revisit to
            # skops/ONNX before any arm (condition tracked in #1955). Annual
            # review.
            payload = pickle.loads(raw)  # noqa: S301 — see ADR-SEC-ML1 above
            if not isinstance(payload, dict):
                raise ValueError("artifact is not a dict payload")
            if payload.get("schema_version") != _ARTIFACT_SCHEMA_VERSION:
                raise ValueError(
                    f"unsupported schema_version={payload.get('schema_version')!r}"
                )
            if list(payload.get("features") or []) != list(FEATURE_WHITELIST):
                raise ValueError(
                    f"artifact features {payload.get('features')!r} != "
                    f"whitelist {list(FEATURE_WHITELIST)!r} — retrain required"
                )
            model = payload.get("model")
            if not hasattr(model, "predict_proba"):
                raise ValueError("artifact model has no predict_proba")
        except Exception as exc:  # noqa: BLE001 — handle artifact loading error
            self._failed_path = path
            # #2418 review P2: exc_info=True for the full stack trace; level
            # stays WARNING (fallback path, CODING_POLICY §5.6 — not ERROR).
            logger.warning(
                "MetaLabelFilter: model artifact %r unusable (%s).",
                path,
                exc,
                exc_info=True,
            )
            return None
        self._model = model
        self._loaded_path = path
        self._failed_path = None
        return model

    # -- decision -----------------------------------------------------------
    def should_trade(
        self, state: dict, valid_votes: list, consensus_score: float
    ) -> Tuple[bool, float, str]:
        """``(trade, proba, reason)`` for one approved BUY.

        ``trade=False`` means: drop this BUY (p(net-of-cost profitable) below
        META_LABEL_MIN_PROBA).
        """
        from config import get_config

        cfg = get_config()
        min_proba = float(getattr(cfg, "META_LABEL_MIN_PROBA", 0.55))
        model_path = str(
            getattr(cfg, "META_LABEL_MODEL_PATH", "data/meta_label_model.pkl")
        )
        require_model = bool(getattr(cfg, "META_LABEL_REQUIRE_MODEL", False))

        model = self._load_model(model_path)
        if model is None:
            if require_model:
                return False, 0.0, "model artifact unavailable — fail-closed"
            return True, 1.0, "model artifact unavailable — unblocked"

        features = extract_features(consensus_score, valid_votes)
        if features is None:
            if require_model:
                return False, 0.0, "whitelist features unavailable — fail-closed"
            logger.warning(
                "MetaLabelFilter: whitelist votes (DrawdownGuard/Regime) missing "
                "for %s — unblocked observation mode, no neutral-default is ever fabricated.",
                state.get("symbol"),
            )
            return True, 1.0, "whitelist features unavailable — unblocked"

        try:
            proba = float(model.predict_proba([features])[0][1])
        except Exception as exc:  # noqa: BLE001 — handle inference error gracefully
            logger.warning(
                "MetaLabelFilter: inference failed for %s (%s).",
                state.get("symbol"),
                exc,
                exc_info=True,
            )
            if require_model:
                return False, 0.0, "inference failed — fail-closed"
            return True, 1.0, "inference failed — unblocked"

        if proba < min_proba:
            return (
                False,
                proba,
                f"p={proba:.2f} < {min_proba:.2f} (no edge net-of-cost)",
            )
        return True, proba, f"p={proba:.2f} >= {min_proba:.2f}"


# Process-wide singleton: the artifact is deserialized once, not per BUY per
# cycle (plan §12.4 latency budget — inference must stay ≪ the 14.8 s cycle).
_filter_singleton: Optional[MetaLabelFilter] = None


def get_meta_label_filter() -> MetaLabelFilter:
    global _filter_singleton
    if _filter_singleton is None:
        _filter_singleton = MetaLabelFilter()
    return _filter_singleton


def reset_meta_label_filter() -> None:
    """Test hook: drop the cached singleton (fresh artifact load)."""
    global _filter_singleton
    _filter_singleton = None
