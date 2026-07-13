"""Quality gate for per-symbol TFT models.

Rejects symbols whose checkpoint should not be served at inference:
  1. Missing metadata.json  → unknown validation, refuse
  2. val_loss > VAL_LOSS_MAX → likely-broken training run, refuse
  3. (After walk-forward) walkforward_ic <= IC_MIN → no out-of-sample edge
  4. (MLR-3 #1903, when the offline layers stamped the fields) fdr_passed must be
     True (Benjamini-Hochberg, scripts/apply_fdr_layer.py) and net_sharpe must
     clear TFT_NET_SHARPE_FLOOR (Almgren-Chriss cost model,
     scripts/apply_cost_model.py). Fields absent → legacy behaviour + WARNING.

Wired into core.ml.model_registry._get_engine(): symbols failing the gate
are added to _known_missing → model_registry.get_or_train() returns None
→ stock_specialist._fetch_ml_prediction() returns None
→ SpecialistReport.ml_direction stays "unavailable"
→ pipeline / senate / coordinator fall back to their existing rule-based
  paths exactly as if the model file didn't exist.

So this guard is PURELY ADDITIVE — it cannot break the live trading path,
only prevents low-quality ML signal from polluting it.

Gate decisions are logged at WARNING on first reject (per symbol) and never
again — symbol is cached in _known_missing for the engine's lifetime.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from config import get_config

logger = logging.getLogger(__name__)

# Tunable thresholds. Read at import; restart engine to pick up new values.
VAL_LOSS_MAX = float(os.environ.get("TFT_QUALITY_GATE_VAL_LOSS_MAX", "5.0"))
# IC floor: serve a model only if its walk-forward IC clears this. Explicit env
# wins; otherwise the LOCAL desktop floor is 0.0 (serve every POSITIVE-edge model
# — ~407 of 488 — excluding only the negative-IC ones that are actively
# wrong-signed), while cloud keeps the stricter 0.05 until separately validated.
_icmin_env = os.environ.get("TFT_QUALITY_GATE_IC_MIN")
if _icmin_env is not None:
    IC_MIN = float(_icmin_env)
else:
    IC_MIN = 0.0 if os.environ.get("DEPLOYMENT_MODE", "").upper() == "LOCAL" else 0.05
WILCOXON_P_MAX = float(os.environ.get("TFT_QUALITY_GATE_WILCOXON_P_MAX", "0.05"))

# Strict mode (default): every gate field must be present AND pass.
# Permissive mode: missing fields are tolerated, only present-but-bad fail.
# Strict is the right production default per the per-entity ML contract.
STRICT = os.environ.get("TFT_QUALITY_GATE_STRICT", "true").lower() in (
    "1",
    "true",
    "yes",
)

# ADR-ML-GATE-01 (amended 2026-06-03): walk-forward supersedes val_loss — DEFAULT ON.
# The v2 per-symbol training pipeline records out-of-sample walk-forward metrics
# (walkforward_ic / walkforward_sharpe) in metadata.json and NO LONGER emits a
# training-time val_loss — 0 of 488 promoted models carry the field. Strict mode
# therefore rejects EVERY model at the val_loss presence check before it ever
# reaches the (stronger, out-of-sample) walkforward_ic gate, leaving TFT signal
# dead on every deployment. When this flag is on, a model that lacks val_loss is
# still admitted to the walkforward_ic gate instead of being rejected outright —
# walk-forward IC is a strictly stronger quality signal than val_loss.
#
# Decision (Georg, 2026-06-03): default this ON EVERYWHERE, not just under
# DEPLOYMENT_MODE=LOCAL. Requiring a val_loss that no promoted model carries is a
# wrong gate condition, not a quality lever — removing it does not loosen quality.
# The actual quality bar is the IC floor (IC_MIN above), which STILL differs by
# deployment: cloud only serves walkforward_ic >= 0.05 (validated-good models),
# local serves every positive-edge model. So cloud admits ML signal for its good
# models only; the val_loss-presence rejection that killed ALL of them is removed.
# Explicit env (TFT_QUALITY_GATE_ALLOW_WALKFORWARD_ONLY) always wins. Restart to apply.
_awo = os.environ.get("TFT_QUALITY_GATE_ALLOW_WALKFORWARD_ONLY")
if _awo is not None:
    ALLOW_WALKFORWARD_ONLY = _awo.lower() in ("1", "true", "yes")
else:
    ALLOW_WALKFORWARD_ONLY = True


@dataclass(frozen=True)
class GateResult:
    """One gate decision for one symbol."""

    passed: bool
    reason: str  # human-readable, included in logs


def _read_metadata(model_dir: Path) -> Optional[dict]:
    """Read metadata.json; None if absent or malformed."""
    meta_path = model_dir / "metadata.json"
    if not meta_path.exists():
        return None
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(
            "[TFTQualityGate] %s metadata.json unreadable: %s", model_dir.name, exc
        )
        return None


# M3b: honest OOS IC metadata keys, in priority order (future-proof alias first).
_HONEST_IC_KEYS = ("walkforward_ic_honest", "walkforward_ic_oos506")


def evaluate(symbol: str, model_dir: Path) -> GateResult:
    """Apply gates in order; return first failure or PASS."""
    meta = _read_metadata(model_dir)

    if meta is None:
        if STRICT:
            return GateResult(False, "missing metadata.json (strict mode)")
        return GateResult(True, "metadata absent (permissive mode)")

    # 1. val_loss must be present and below the broken-model threshold.
    # ADR-ML-GATE-01: when val_loss is absent but the model carries a walk-forward
    # signal, fall through to the (stronger) walkforward_ic gate instead of
    # rejecting — provided the operator has opted in via ALLOW_WALKFORWARD_ONLY.
    val_loss = meta.get("val_loss")
    use_honest = get_config().TFT_QUALITY_GATE_HONEST_IC
    # The val_loss bypass must treat an honest-IC field as a walk-forward signal too, so an
    # honest-only model (M3b) isn't rejected here before it reaches the (stronger) IC gate.
    has_walkforward = meta.get("walkforward_ic") is not None or (
        use_honest and any(meta.get(k) is not None for k in _HONEST_IC_KEYS)
    )
    if val_loss is None:
        if STRICT and not (ALLOW_WALKFORWARD_ONLY and has_walkforward):
            return GateResult(False, "metadata has no val_loss field")
    elif not isinstance(val_loss, (int, float)):
        return GateResult(False, f"val_loss is not numeric: {val_loss!r}")
    elif val_loss > VAL_LOSS_MAX:
        return GateResult(
            False, f"val_loss={val_loss:.3f} exceeds threshold {VAL_LOSS_MAX:.1f}"
        )

    # 2. Walk-forward gate fields (populated by backtest_tft_per_symbol_walkforward.py).
    # Missing fields are NOT a fail until walk-forward has run for the universe —
    # this allows the gate to ship today and tighten later as walk-forward results
    # backfill. After every symbol has walkforward_ic recorded, flip STRICT_WALKFORWARD
    # to true.
    strict_wf = os.environ.get(
        "TFT_QUALITY_GATE_STRICT_WALKFORWARD", "false"
    ).lower() in ("1", "true", "yes")

    # M3b remedy: when TFT_QUALITY_GATE_HONEST_IC is ON, judge the model by the honest
    # ~506-obs decoder-step-0 OOS IC (the step #1170 serves) instead of the noisy 250-obs
    # `walkforward_ic` whose SE (~0.063) sits inside the floors. Prefer a future-proof alias
    # before the obs-count-coupled `walkforward_ic_oos506`; warn + fall back to the legacy
    # field when neither is present (e.g. pre-backfill metadata). OFF = byte-identical.
    wf_ic = None
    wf_ic_field = "walkforward_ic"
    if use_honest:
        for key in _HONEST_IC_KEYS:
            if meta.get(key) is not None:
                wf_ic = meta.get(key)
                wf_ic_field = key
                break
        if wf_ic is None:
            logger.warning(
                "[TFTQualityGate] %s: honest OOS IC "
                "(walkforward_ic_honest/walkforward_ic_oos506) missing — "
                "falling back to walkforward_ic",
                symbol,
            )
    if wf_ic is None:
        wf_ic = meta.get("walkforward_ic")
        wf_ic_field = "walkforward_ic"

    if wf_ic is not None:
        if not isinstance(wf_ic, (int, float)):
            return GateResult(False, f"{wf_ic_field} is not numeric: {wf_ic!r}")
        if wf_ic <= IC_MIN:
            return GateResult(
                False, f"{wf_ic_field}={wf_ic:.3f} <= threshold {IC_MIN:.2f}"
            )
    elif strict_wf:
        return GateResult(
            False, "metadata has no walkforward_ic field (strict_walkforward mode)"
        )

    wf_p = meta.get("wilcoxon_p")
    if wf_p is not None:
        if not isinstance(wf_p, (int, float)):
            return GateResult(False, f"wilcoxon_p is not numeric: {wf_p!r}")
        if wf_p > WILCOXON_P_MAX:
            return GateResult(
                False, f"wilcoxon_p={wf_p:.4f} > threshold {WILCOXON_P_MAX:.2f}"
            )

    # 3. MLR-3 (#1903): offline gate-stack wiring — consult the Layer-2 FDR verdict
    # and the Layer-4 net-of-cost Sharpe when the offline layers stamped them.
    #   * `fdr_passed`  — top-level bool written by scripts/apply_fdr_layer.py
    #     (Benjamini-Hochberg across the universe). The script writes `null` when a
    #     symbol had no usable p-value and was EXCLUDED from the correction — that
    #     is "not evaluated", not "failed", so None is treated as absent.
    #   * `net_sharpe`  — written by scripts/apply_cost_model.py (Almgren-Chriss
    #     costs) inside the `layer4` block; promotion may also flatten it to the
    #     top level, so both locations are checked (top level wins).
    # Fail-safe by design (feedback_sharpe_over_ic + preserve-constraint): fields
    # ABSENT (legacy metadata) → behaviour byte-identical to today, with a WARNING
    # so audit can see the model was admitted on IC alone. In particular the
    # ALLOW_WALKFORWARD_ONLY val_loss bypass above is untouched.
    fdr_passed = meta.get("fdr_passed")
    net_sharpe = meta.get("net_sharpe")
    if net_sharpe is None:
        layer4 = meta.get("layer4")
        if isinstance(layer4, dict):
            net_sharpe = layer4.get("net_sharpe")

    if fdr_passed is not None and fdr_passed is not True:
        return GateResult(
            False,
            f"fdr_passed={fdr_passed!r} — failed Benjamini-Hochberg FDR layer "
            f"(bh_adjusted_p={meta.get('bh_adjusted_p')!r})",
        )

    if net_sharpe is not None:
        if not isinstance(net_sharpe, (int, float)):
            return GateResult(False, f"net_sharpe is not numeric: {net_sharpe!r}")
        net_sharpe_floor = float(get_config().TFT_NET_SHARPE_FLOOR)
        if net_sharpe <= net_sharpe_floor:
            return GateResult(
                False,
                f"net_sharpe={net_sharpe:.3f} <= floor {net_sharpe_floor:.2f} "
                f"(net-of-cost, feedback_sharpe_over_ic)",
            )

    missing = [
        name
        for name, val in (("fdr_passed", fdr_passed), ("net_sharpe", net_sharpe))
        if val is None
    ]
    if missing:
        logger.warning(
            "[TFTQualityGate] %s: gate fields missing (%s) — serving on IC only "
            "(legacy metadata)",
            symbol,
            ", ".join(missing),
        )

    return GateResult(True, "all checks passed")


# Module-level stats (diagnostic — exposed via /health if needed)
_stats = {"checked": 0, "passed": 0, "rejected": 0, "reasons": {}}


def evaluate_and_log(symbol: str, model_dir: Path) -> GateResult:
    """Wrapper that logs first-reject per symbol and tracks counters."""
    result = evaluate(symbol, model_dir)
    _stats["checked"] += 1
    if result.passed:
        _stats["passed"] += 1
    else:
        _stats["rejected"] += 1
        _stats["reasons"][result.reason] = _stats["reasons"].get(result.reason, 0) + 1
        logger.warning("[TFTQualityGate] REJECT %s: %s", symbol, result.reason)
    return result


def stats() -> dict:
    """Diagnostic snapshot of gate counters."""
    return dict(_stats)


__all__ = [
    "evaluate",
    "evaluate_and_log",
    "stats",
    "GateResult",
    "VAL_LOSS_MAX",
    "IC_MIN",
    "STRICT",
]
