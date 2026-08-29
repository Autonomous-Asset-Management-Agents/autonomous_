# core/specialist/insight_quality/grader.py
# RPAR Epic #1262, Task T6b (#1271) - PR-1: synthesis-quality grader.
"""Deterministic, pure synthesis-quality scoring for a ``SpecialistReport``.

The grader answers a single question: *how complete and specific is the prose
the synthesizer produced for this report?* It returns a score in ``[0.0, 1.0]``
derived only from the report's own fields - no network, no LLM, no config, no
clock. This is the cheapest, most testable rung of the quality-ratchet: the loop
uses it to decide whether a fresh run is *better* than the prior report before
ever consulting the (expensive, injectable) LLM judge.

Design invariants (HARD):
  * **Pure:** never mutates its input report (callers rely on this - the loop
    grades both the current and the prior report without side effects).
  * **Deterministic:** the same report always yields the same score.
  * **No ``or``-default on numerics** (P0-1): emptiness is decided by explicit
    ``len(...) > 0`` / ``is None`` checks, never ``x or 0``.

The weights below are a refactored, transparent re-expression of the bundle's
completeness heuristic. They are pinned by the unit tests (complete > thin) and
are reconciled against a real bundle snapshot via golden fixtures in PR-2; until
then the package is dormant (``INSIGHT_QUALITY_ENABLED`` default OFF) so these
exact numbers are never on the engine path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only, no runtime import cost
    from core.specialist.report import SpecialistReport

# Relative contribution of each synthesized field to the completeness score.
# Sums to 1.0 so a fully-populated report grades to exactly 1.0. Kept explicit
# (not a loop over getattr) so the contract is greppable and pinned by tests.
_FIELD_WEIGHTS = {
    "company_summary": 0.20,
    "news_summary": 0.15,
    "investment_thesis": 0.25,
    "bull_case": 0.10,
    "bear_case": 0.10,
}
_REASONS_WEIGHT = 0.10
_HEADLINES_WEIGHT = 0.10

# A text field shorter than this is treated as "stub-only" prose, not a real
# synthesis, and earns half credit. Pinned by the complete-vs-thin test.
_MIN_MEANINGFUL_CHARS = 40


def _text_credit(value: str) -> float:
    """Fractional credit for a single prose field. No ``or``-default (P0-1)."""
    if value is None:
        return 0.0
    length = len(value.strip())
    if length == 0:
        return 0.0
    if length < _MIN_MEANINGFUL_CHARS:
        return 0.5
    return 1.0


def grade_synthesis(report: "SpecialistReport") -> float:
    """Score the synthesis completeness of ``report`` in ``[0.0, 1.0]``.

    Pure and deterministic: reads only the report's synthesized fields and never
    mutates the report.
    """
    score = 0.0

    for field_name, weight in _FIELD_WEIGHTS.items():
        score += weight * _text_credit(getattr(report, field_name, ""))

    # List fields: present-and-non-empty earns full weight (explicit len check,
    # never a truthiness ``or``-default on a numeric).
    reasons = getattr(report, "reasons", [])
    if reasons is not None and len(reasons) > 0:
        score += _REASONS_WEIGHT

    headlines = getattr(report, "headlines", [])
    if headlines is not None and len(headlines) > 0:
        score += _HEADLINES_WEIGHT

    # Clamp defensively; the weights sum to 1.0 so this is a guardrail only.
    if score < 0.0:
        return 0.0
    if score > 1.0:
        return 1.0
    return score
