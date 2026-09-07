# core/specialist/report_quality.py
# RPAR-1 (#1262) Abschluss / #1490 — deterministic, bundle-free report-quality score.
"""A deterministic "how good is this report?" score, derived ONLY from the report's own fields —
no LLM, no network, no bundle snapshot. This is the dev-env-native completion of RPAR-1's quality
goal: reuse the pure completeness grader, dock for a degraded data path, map to a Strong/Fair/Thin
label.

Pure + side-effect-free: the same report always yields the same ``(score, label)``; it never
mutates the report and never feeds the trading decision (display-only).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Tuple

from core.specialist.insight_quality.grader import grade_synthesis

if TYPE_CHECKING:  # pragma: no cover - typing only
    from core.specialist.report import SpecialistReport

# A degraded data path (data-integrity guard tripped) caps our confidence in the synthesized prose.
_DEGRADED_FACTOR = 0.6
# Label bands on the 0-100 score (inclusive lower bounds).
_STRONG_MIN = 75
_FAIR_MIN = 50


def quality_label(score: int) -> str:
    """Map a 0-100 score to the Strong / Fair / Thin band."""
    if score >= _STRONG_MIN:
        return "Strong"
    if score >= _FAIR_MIN:
        return "Fair"
    return "Thin"


def compute_report_quality(report: "SpecialistReport") -> Tuple[int, str]:
    """Return ``(score 0-100, label)`` for a report. Deterministic, display-only, bundle-free."""
    grade = grade_synthesis(
        report
    )  # 0.0-1.0 completeness/specificity of the synthesized prose
    if getattr(report, "degraded", False):
        grade *= _DEGRADED_FACTOR
    score = round(grade * 100)
    return score, quality_label(score)
