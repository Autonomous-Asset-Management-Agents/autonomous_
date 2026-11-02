# core/specialist/insight_quality/loop.py
# RPAR Epic #1262, Task T6b (#1271) - PR-1: the quality-ratchet loop.
"""The insight-quality *ratchet*: grade -> judge -> rewrite / abstain / pass.

This is the decision-relevant core of T6b. Given the freshly-built report, the
prior report (``last_report``), and an injected judge, it returns the report the
specialist should actually surface - and tags it with a ``signal_quality`` of
``'iq:graded'`` / ``'iq:rewritten'`` / ``'iq:abstain'`` so the DTO reflects what
the ratchet did.

Ratchet semantics (pinned by the unit tests):
  * **rewrite** - the judge supplied a better-written report -> return THAT object,
    tag ``'iq:rewritten'``.
  * **abstain** - the judge does not trust the fresh synthesis. If a prior report
    exists AND grades *higher* than the fresh one, return the PRIOR report
    (the ratchet refuses to regress) tagged ``'iq:abstain'``. If there is no
    usable prior (or the fresh one is no worse), keep the current report tagged
    ``'iq:graded'``.
  * **pass** (or any non-rewrite/non-abstain verdict) - keep the current report,
    tag ``'iq:graded'``.

(!) Decision relevance: because abstain can return the *prior* report object, the
``sentiment_score`` / ``recommendation`` / ``escalate`` the round-table reads can
differ from a no-ratchet run. That is exactly why T6b is gated and dormant
(``INSIGHT_QUALITY_ENABLED`` default OFF) until a shadow-validated default flip.

This module is PURE w.r.t. the grader (which never mutates); the only mutation
here is setting ``signal_quality`` on the report object that is *returned*.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

from core.specialist.insight_quality import grader as _grader
from core.specialist.insight_quality import judge as _judge

if TYPE_CHECKING:  # pragma: no cover - typing only
    from core.specialist.report import SpecialistReport

SIGNAL_GRADED = "iq:graded"
SIGNAL_REWRITTEN = "iq:rewritten"
SIGNAL_ABSTAIN = "iq:abstain"


def run_ratchet(
    report: "SpecialistReport",
    *,
    gathered: Dict[str, Any],
    last_report: Optional["SpecialistReport"],
    judge: Any,
) -> "SpecialistReport":
    """Run the quality-ratchet and return the report to surface.

    ``judge`` is injected (any object with ``evaluate(report, *, gathered,
    grade) -> {"verdict", "rewritten"}``). The grader is pure; this function only
    sets ``signal_quality`` on the object it returns.
    """
    grade = _grader.grade_synthesis(report)
    verdict = judge.evaluate(report, gathered=gathered, grade=grade)
    decision = verdict.get("verdict", _judge.VERDICT_PASS)

    if decision == _judge.VERDICT_REWRITE:
        rewritten = verdict.get("rewritten")
        if rewritten is not None:
            rewritten.signal_quality = SIGNAL_REWRITTEN
            return rewritten
        # Judge asked for a rewrite but supplied none -> fall through to graded.

    if decision == _judge.VERDICT_ABSTAIN:
        if last_report is not None:
            prior_grade = _grader.grade_synthesis(last_report)
            if prior_grade > grade:
                # Ratchet refuses to regress -> keep the better prior report.
                last_report.signal_quality = SIGNAL_ABSTAIN
                return last_report
        # No usable prior (or fresh is no worse) -> keep current, graded.
        report.signal_quality = SIGNAL_GRADED
        return report

    # pass / unknown verdict -> keep current synthesis, graded.
    report.signal_quality = SIGNAL_GRADED
    return report
