# tests/unit/test_insight_quality_wiring.py
# RPAR Epic #1262, Task T6b (#1271) - PR-2: the research()-wiring surface + the
# `_fetch_earnings_transcript` graceful stub. The standalone grader/judge/loop are
# pinned by PR-1's test_insight_quality.py; here we cover the PR-2-new pieces with a
# Fake-Judge, WITHOUT constructing the full agent (no network / LLM).
from core.specialist.insight_quality import LLMJudge, enforce_insight_quality
from core.specialist.insight_quality.judge import VERDICT_ABSTAIN, make_verdict
from core.specialist.insight_quality.prompt import cap_transcript
from core.specialist.report import SpecialistReport
from core.stock_specialist import StockSpecialistAgent


def _report(symbol: str = "AAPL", **prose) -> SpecialistReport:
    return SpecialistReport(symbol=symbol, **prose)


def test_fetch_earnings_transcript_is_graceful_stub():
    # Option A: returns "" until the data source lands; uses no `self`, never crashes; capped to "".
    assert StockSpecialistAgent._fetch_earnings_transcript(object()) == ""
    assert cap_transcript("") == ""


def test_facade_default_judge_passes_through_tagged_graded():
    # The wired LLMJudge() is a conservative PASS no-op -> fresh report kept, tagged iq:graded.
    fresh = _report(
        company_summary="x" * 50, news_summary="y" * 40, investment_thesis="z" * 60
    )
    out = enforce_insight_quality(
        fresh, gathered={}, last_report=None, cfg=None, judge=LLMJudge()
    )
    assert out.signal_quality == "iq:graded"


class _AbstainJudge:
    """Fake-Judge that always abstains (does not trust the fresh synthesis)."""

    def evaluate(self, report, *, gathered, grade):
        return make_verdict(VERDICT_ABSTAIN)


def test_facade_abstain_returns_higher_grading_prior():
    # Fresh is thin (empty prose -> low grade), prior is rich -> on abstain the ratchet returns the
    # PRIOR object so a fresh run never silently regresses (loop docstring's decision-relevance note).
    thin = _report()
    rich = _report(
        company_summary="x" * 80,
        news_summary="y" * 60,
        investment_thesis="z" * 90,
        bull_case="b" * 50,
        bear_case="c" * 50,
    )
    out = enforce_insight_quality(
        thin, gathered={}, last_report=rich, cfg=None, judge=_AbstainJudge()
    )
    assert out is rich
    assert out.signal_quality == "iq:abstain"
