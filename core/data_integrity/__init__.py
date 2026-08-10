# core/data_integrity/__init__.py
# RPAR Epic #1262, Task T6a (#1268, Closes #1270) - data-integrity guard package.
"""Pure, deterministic data-integrity guard for the stock specialist.

This package owns the *producer* for the already-existing (Task V0, #1275)
``SpecialistReport.data_quality`` / ``.degraded`` schema fields. It assesses the
freshness / completeness of the gathered raw data and derives those two
DISPLAY-ONLY fields. It is intentionally free of any agent / network / LLM / clock
side-effects so it can be unit-tested standalone and called read-only by the #76
shadow-harness.

The whole guard is gated behind ``DATA_INTEGRITY_GUARD_ENABLED`` (default OFF) in
the caller; OFF reproduces the V0 schema defaults (``data_quality=1.0``,
``degraded=False``) so the report DTO stays byte-identical.
"""

from core.data_integrity.guard import (
    DEFAULT_THRESHOLDS,
    DataIntegrityResult,
    DataIntegrityThresholds,
    assess,
)

__all__ = [
    "assess",
    "DataIntegrityResult",
    "DataIntegrityThresholds",
    "DEFAULT_THRESHOLDS",
]
