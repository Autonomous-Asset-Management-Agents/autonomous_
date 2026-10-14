# core/specialist/insight_quality/fleet.py
# RPAR Epic #1262, Task T6b (#1271) - PR-1: multi-pass orchestration (skeleton).
"""Multi-pass orchestration over the grader/judge for the insight-quality path.

The ``fleet`` coordinates one-or-more grade->judge passes. PR-1 lands a thin,
synchronous single-pass orchestrator so the module is importable and unit-shaped;
the real multi-pass fan-out (and any async work) is PR-2, still behind
``INSIGHT_QUALITY_ENABLED``. No network / LLM at import or in this single pass
beyond what the injected judge does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

from core.specialist.insight_quality import grader as _grader

if TYPE_CHECKING:  # pragma: no cover - typing only
    from core.specialist.report import SpecialistReport


def single_pass(
    report: "SpecialistReport", *, gathered: Dict[str, Any], judge: Any
) -> Dict[str, Any]:
    """Grade once and ask the injected judge for a verdict. Pure orchestration.

    Returns the judge's verdict dict augmented with the computed ``grade``. The
    grader does not mutate ``report``.
    """
    grade = _grader.grade_synthesis(report)
    verdict = judge.evaluate(report, gathered=gathered, grade=grade)
    return {"grade": grade, **verdict}
