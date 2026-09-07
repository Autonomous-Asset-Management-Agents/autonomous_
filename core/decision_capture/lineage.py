"""#2113 §5.5 — Lineage helpers: git_sha / config_sha / feature_list_hash.

Answers "which code, which config, which feature schema produced this
decision?" for the ``decision_outcomes`` capture row. Deterministic, read-only,
edition-neutral (no GCP imports; config via ``config.get_config()`` only —
CODING_POLICY §2.10).
"""

from __future__ import annotations

import hashlib
import json
import logging

logger = logging.getLogger(__name__)

# ADR-CAP-01: config_sha basis — the execution-GATE-relevant config keys
# (sizing, compliance caps, HITL limits, kill-switch-adjacent trading flags).
# Rationale: the capture row must make reproducible WHICH gate posture judged
# the decision; hashing the full config would churn the sha on every unrelated
# key (e.g. UI flags) and leak breadth. Deterministically sorted before hashing.
# Basis: plan #2113 §5.5. Annual review (CODING_POLICY §5.5).
GATE_CONFIG_KEYS = (
    "PAPER_TRADING",
    "SHADOW_MODE",
    "HITL_ENABLED",
    "HITL_AUTONOMOUS_UNLIMITED",
    "HITL_MAX_VALUE_PER_TRADE",
    "HITL_MAX_VALUE_PER_DAY",
    "HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS",
    "USE_LIMIT_ORDERS",
    "LIMIT_ORDER_SPREAD_BUFFER_PCT",
    "ENABLE_DYNAMIC_SIZING",
    "MIN_POSITION_PERCENT",
    "MAX_POSITION_PERCENT",
    "MAX_POSITION_PERCENT_SIZING",
    "DRAWDOWN_GUARD_CONDITIONER_ENABLED",
    "DRAWDOWN_CONVICTION_DAMP_MAX",
    "FULL_UNIVERSE_TRADING_ENABLED",
    "BYPASS_MARKET_HOURS",
)

# ADR-CAP-02: feature_list_hash basis — the ORDERED eval-input scalar fields of
# the DecisionContext that #2389 (TA-Feature-Snapshot) fills. Hashing the FIELD
# LIST (schema), not the values, pins which feature schema the decision saw —
# when #2389 extends the snapshot, the hash changes and downstream training can
# split by schema generation. Basis: plan #2113 §5.5 (Kopplung an #2389).
# Annual review (CODING_POLICY §5.5).
FEATURE_FIELDS = (
    "rsi_14",
    "macd",
    "macd_signal",
    "adx_14",
    "bb_pct",
    "volume_ratio",
    "volatility_20d",
    "atr_14d",
    "vix_level",
    "market_regime",
)


def get_git_sha() -> str:
    """Build provenance: AAA_APP_VERSION (desktop) → GIT_COMMIT (cloud) →
    'unknown' — single source: core.telemetry.get_service_version()."""
    from core.telemetry import get_service_version

    return get_service_version()


def compute_config_sha() -> str:
    """SHA-256 over the deterministically sorted gate-relevant config keys.

    Missing keys hash as ``null`` (stable across editions); values are read via
    ``config.get_config()`` only. Never raises — on any error returns "" and
    logs at WARNING (§5.6), so lineage can never touch the trading path."""
    try:
        import config as _config

        cfg = _config.get_config()
        payload = {k: getattr(cfg, k, None) for k in sorted(GATE_CONFIG_KEYS)}
        blob = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()
    except Exception as exc:  # noqa: BLE001 — observation must never raise
        logger.warning("lineage: config_sha failed → '' (capture degraded): %s", exc)
        return ""


def compute_feature_list_hash() -> str:
    """SHA-256 over the ordered FEATURE_FIELDS schema list (ADR-CAP-02)."""
    try:
        blob = ",".join(FEATURE_FIELDS)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()
    except Exception as exc:  # noqa: BLE001 — observation must never raise
        logger.warning(
            "lineage: feature_list_hash failed → '' (capture degraded): %s", exc
        )
        return ""
