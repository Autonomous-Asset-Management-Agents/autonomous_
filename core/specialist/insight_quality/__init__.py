# core/specialist/insight_quality/__init__.py
# RPAR Epic #1262, Task T6b (#1271) - PR-1: thin facade for the IQ ratchet.
"""``core.specialist.insight_quality`` - the insight-quality ratchet package.

Phase T6b of the Specialist-Report-Parity epic (#1262). This package is the
quality-ratchet that grades the synthesizer's prose, optionally asks an injected
LLM judge to rewrite or abstain, and ratchets the result against the prior
report so a fresh run never silently regresses.

PR-1 (this PR) lands the package **skeleton + grader/judge/loop + the
``INSIGHT_QUALITY_ENABLED`` flag** - importable-but-UNWIRED and dormant. Nothing
in ``stock_specialist.research()`` / ``_build_report`` calls into this package
yet; that wiring (plus ``_fetch_earnings_transcript``) is the PR-2 follow-up. The
flag is read NOWHERE on the engine path in PR-1, so with the flag OFF (default)
the engine + DTO are byte-identical to today.

Importing this package (and every submodule) is side-effect-free: no network, no
genai client, no config read at import time.

Facade
------
``enforce_insight_quality(report, *, gathered, last_report, cfg, judge=...) ->
SpecialistReport`` runs the ratchet and returns the report to surface, tagged
with ``signal_quality`` in {``'iq:graded'``, ``'iq:rewritten'``, ``'iq:abstain'``}.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

from core.specialist.insight_quality.judge import LLMJudge
from core.specialist.insight_quality.loop import run_ratchet

if TYPE_CHECKING:  # pragma: no cover - typing only
    from core.specialist.report import SpecialistReport

__all__ = ["enforce_insight_quality", "LLMJudge"]


def enforce_insight_quality(
    report: "SpecialistReport",
    *,
    gathered: Dict[str, Any],
    last_report: Optional["SpecialistReport"] = None,
    cfg: Any = None,
    judge: Any = None,
) -> "SpecialistReport":
    """Run the insight-quality ratchet over ``report``.

    Args:
        report: the freshly-built ``SpecialistReport`` to evaluate.
        gathered: the raw gather dict (grounding context for the judge).
        last_report: the prior report to ratchet against (or ``None``).
        cfg: the runtime config object. Accepted for facade parity; PR-1 does
            NOT read ``INSIGHT_QUALITY_ENABLED`` here - gating happens at the
            (PR-2) ``research()`` call site so the package stays dormant.
        judge: an injected judge adapter exposing
            ``evaluate(report, *, gathered, grade)``. Defaults to the
            conservative no-op ``LLMJudge`` (always passes through).

    Returns:
        The report to surface, with ``signal_quality`` set to one of
        ``'iq:graded'`` / ``'iq:rewritten'`` / ``'iq:abstain'``.
    """
    active_judge = judge if judge is not None else LLMJudge()
    return run_ratchet(
        report,
        gathered=gathered,
        last_report=last_report,
        judge=active_judge,
    )
