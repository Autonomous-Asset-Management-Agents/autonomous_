# core/specialist/insight_quality/judge.py
# RPAR Epic #1262, Task T6b (#1271) - PR-1: LLM-judge adapter (injectable).
"""LLM-judge adapter for the insight-quality ratchet.

The judge decides what to do with a freshly-graded synthesis: keep it
(``"pass"``), ask for a rewrite (``"rewrite"``), or abstain (``"abstain"``)
because the new prose is not trustworthy. Crucially the *adapter* is designed to
be **injected and mocked**: the loop accepts any object exposing
``evaluate(report, *, gathered, grade) -> dict``. No genai/Gemini client is
constructed at import time, and the unit tests inject a stub instead of calling
a real model (CODING_POLICY §5.2 - no live LLM in unit tests).

PR-1 scope: only the adapter *shape* and a NO-OP default judge are landed. The
real Gemini-backed verdict prompt is wired in PR-2 (research()-wiring), still
behind ``INSIGHT_QUALITY_ENABLED``. Until then the package is dormant.

Verdict contract (dict):
    {"verdict": "pass" | "rewrite" | "abstain", "rewritten": SpecialistReport | None}
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:  # pragma: no cover - typing only
    from core.specialist.report import SpecialistReport

# The three terminal verdicts the loop understands.
VERDICT_PASS = "pass"
VERDICT_REWRITE = "rewrite"
VERDICT_ABSTAIN = "abstain"


def make_verdict(
    verdict: str, *, rewritten: Optional["SpecialistReport"] = None
) -> Dict[str, Any]:
    """Build a well-formed verdict dict (single source of the contract shape)."""
    return {"verdict": verdict, "rewritten": rewritten}


class LLMJudge:
    """Default, side-effect-free judge adapter.

    Importing this module and constructing ``LLMJudge()`` must NOT create a
    genai client or hit the network - that keeps the package importable-but-
    dormant. The default ``evaluate`` is a conservative NO-OP that always passes
    the current synthesis through; the real model call is injected (or wired in
    PR-2). Real callers pass a Gemini-backed adapter with the same ``evaluate``
    signature; unit tests pass a stub.
    """

    def __init__(self, *, client: Optional[Any] = None) -> None:
        # Injected, optional. None = no model available -> conservative pass.
        # No client is constructed here (no import-time genai dependency).
        self._client = client

    def evaluate(
        self,
        report: "SpecialistReport",
        *,
        gathered: Dict[str, Any],
        grade: float,
    ) -> Dict[str, Any]:
        """Return a verdict dict. Default (no client) = conservative pass.

        The real LLM-backed verdict is PR-2. With no injected client this is a
        pure, deterministic NO-OP so the dormant package never needs a model.
        """
        return make_verdict(VERDICT_PASS)
