"""
core.ml.validation — canonical purged + embargoed walk-forward / CPCV harness.

Single validation entry point for all model families (Issue #1906, MLR-6).
See :mod:`core.ml.validation.harness` for the implementation and rationale.

DORMANT: this package is additive and not yet wired into any trainer. RL /
LSTM / TFT adopt it in the follow-up PRs described in the #1906 plan.
"""

from core.ml.validation.harness import (
    Fold,
    LeakageResult,
    apply_embargo,
    combinatorial_purged_cv,
    purge_overlapping_labels,
    purged_walk_forward,
    shuffle_target_leakage_test,
)

__all__ = [
    "Fold",
    "LeakageResult",
    "apply_embargo",
    "combinatorial_purged_cv",
    "purge_overlapping_labels",
    "purged_walk_forward",
    "shuffle_target_leakage_test",
]
