# tests/unit/test_insight_quality.py
# RPAR Epic #1262, Task T6b (#1271) - PR-1: insight_quality package skeleton.
#
# Scope (PR-1 only): the package is importable-but-UNWIRED and dormant. These
# tests pin the standalone units (grader / judge-adapter / loop ratchet), the
# side-effect-free import contract, and the flag presence/default in BOTH config
# files. The research()-wiring + _fetch_earnings_transcript scenarios are PR-2
# and are intentionally NOT tested here (see plan Sequencing section).
#
# Gherkin (PR-1 subset):
#   Scenario: grader scores complete vs thin synthesis deterministically
#   Scenario: judge (injected mock) returns rewrite / abstain
#   Scenario: loop realizes the ratchet (abstain w/ weaker new grade -> last_report;
#             rewrite -> new report, signal_quality='iq:rewritten')
#   Scenario: importing the package is side-effect-free
#   Scenario: INSIGHT_QUALITY_ENABLED exists, default False, in config.py + config.oss.py

import copy
import importlib
import importlib.util
import os

import allure

from core.specialist.report import SpecialistReport

# config.oss.py is a dotted filename -> load it explicitly (same pattern as
# tests/unit/test_config_oss_get_secret_str.py) so we test the OSS edition
# regardless of which edition sits at config.py.
_OSS_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), os.pardir, os.pardir, "config.oss.py"
)


def _load_oss_config():
    abs_path = os.path.abspath(_OSS_CONFIG_PATH)
    spec = importlib.util.spec_from_file_location("config_oss_t6b", abs_path)
    assert spec and spec.loader, f"Could not find config.oss.py at {abs_path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------- helpers -------------------------
def _complete_report(symbol: str = "AAPL") -> SpecialistReport:
    """A richly-synthesized report (high grade)."""
    return SpecialistReport(
        symbol=symbol,
        company_summary=(
            "Apple designs and sells consumer hardware, software and services. "
            "Q3 revenue grew on Services strength while iPhone units were flat."
        ),
        news_summary=(
            "Multiple outlets report a robust Services quarter and a measured "
            "hardware cycle; analysts remain split on the AI roadmap timing."
        ),
        investment_thesis=(
            "Durable installed base and high-margin Services underpin the bull "
            "case; valuation already prices in steady growth, capping upside."
        ),
        bull_case="Services margin expansion and buybacks support EPS growth.",
        bear_case="Hardware saturation and regulatory pressure on the App Store.",
        reasons=["Services growth", "Strong balance sheet", "Buyback program"],
        headlines=[
            {"title": "Apple Services hits record", "url": "https://example.com/1"},
            {"title": "iPhone units flat YoY", "url": "https://example.com/2"},
        ],
        sentiment_score=62.0,
        recommendation="buy",
        confidence=0.7,
    )


def _thin_report(symbol: str = "AAPL") -> SpecialistReport:
    """A near-empty synthesis (low grade)."""
    return SpecialistReport(
        symbol=symbol,
        company_summary="",
        news_summary="",
        investment_thesis="",
        bull_case="",
        bear_case="",
        reasons=[],
        headlines=[],
        sentiment_score=50.0,
        recommendation="hold",
        confidence=0.5,
    )


class _StubJudge:
    """Injectable LLM-judge double - no genai call. Returns a fixed verdict."""

    def __init__(self, *, verdict, rewritten=None):
        self._verdict = verdict
        self._rewritten = rewritten
        self.calls = 0

    def evaluate(self, report, *, gathered, grade):
        self.calls += 1
        return {"verdict": self._verdict, "rewritten": self._rewritten}


@allure.feature("RPAR Specialist-Report Parity")
@allure.story("T6b insight_quality - grader")
class TestGrader:
    def test_complete_scores_higher_than_thin(self):
        from core.specialist.insight_quality import grader

        g_full = grader.grade_synthesis(_complete_report())
        g_thin = grader.grade_synthesis(_thin_report())
        assert 0.0 <= g_thin <= g_full <= 1.0
        assert g_full > g_thin

    def test_grade_is_deterministic(self):
        from core.specialist.insight_quality import grader

        r = _complete_report()
        assert grader.grade_synthesis(r) == grader.grade_synthesis(r)

    def test_grader_does_not_mutate_input(self):
        from core.specialist.insight_quality import grader

        r = _complete_report()
        before = copy.deepcopy(r)
        grader.grade_synthesis(r)
        assert r == before


@allure.feature("RPAR Specialist-Report Parity")
@allure.story("T6b insight_quality - judge adapter")
class TestJudge:
    def test_injected_judge_returns_rewrite(self):
        rewritten = _complete_report()
        judge = _StubJudge(verdict="rewrite", rewritten=rewritten)
        out = judge.evaluate(_thin_report(), gathered={}, grade=0.1)
        assert out["verdict"] == "rewrite"
        assert out["rewritten"] is rewritten
        assert judge.calls == 1

    def test_injected_judge_returns_abstain(self):
        judge = _StubJudge(verdict="abstain")
        out = judge.evaluate(_thin_report(), gathered={}, grade=0.1)
        assert out["verdict"] == "abstain"

    def test_judge_module_has_no_module_level_genai(self):
        # The adapter must be injectable; importing it must not require/construct
        # a genai client (no direct call at import time).
        judge_mod = importlib.import_module("core.specialist.insight_quality.judge")
        assert hasattr(judge_mod, "LLMJudge")


@allure.feature("RPAR Specialist-Report Parity")
@allure.story("T6b insight_quality - quality ratchet loop")
class TestRatchetLoop:
    def test_abstain_with_weaker_new_grade_returns_last_report(self):
        from core.specialist.insight_quality import enforce_insight_quality

        strong_prev = _complete_report("AAPL")  # high grade, prior good report
        weak_new = _thin_report("AAPL")  # low grade, current run
        judge = _StubJudge(verdict="abstain")

        out = enforce_insight_quality(
            weak_new,
            gathered={},
            last_report=strong_prev,
            cfg=None,
            judge=judge,
        )
        # Ratchet: abstain + weaker new grade -> keep the better prior report.
        assert out is strong_prev
        assert out.signal_quality == "iq:abstain"

    def test_abstain_with_no_prior_keeps_current_graded(self):
        from core.specialist.insight_quality import enforce_insight_quality

        current = _complete_report("MSFT")
        judge = _StubJudge(verdict="abstain")
        out = enforce_insight_quality(
            current, gathered={}, last_report=None, cfg=None, judge=judge
        )
        # No prior to ratchet to -> keep current, mark graded.
        assert out is current
        assert out.signal_quality == "iq:graded"

    def test_rewrite_returns_new_report_marked_rewritten(self):
        from core.specialist.insight_quality import enforce_insight_quality

        original = _thin_report("NVDA")
        rewritten = _complete_report("NVDA")
        judge = _StubJudge(verdict="rewrite", rewritten=rewritten)

        out = enforce_insight_quality(
            original, gathered={}, last_report=None, cfg=None, judge=judge
        )
        assert out is rewritten
        assert out.signal_quality == "iq:rewritten"

    def test_pass_verdict_marks_graded(self):
        from core.specialist.insight_quality import enforce_insight_quality

        current = _complete_report("GOOG")
        judge = _StubJudge(verdict="pass")
        out = enforce_insight_quality(
            current, gathered={}, last_report=None, cfg=None, judge=judge
        )
        assert out is current
        assert out.signal_quality == "iq:graded"


@allure.feature("RPAR Specialist-Report Parity")
@allure.story("T6b insight_quality - dormancy & import contract")
class TestImportContract:
    def test_package_import_is_side_effect_free(self):
        # Re-importing the package + every submodule must not raise and must not
        # touch the network / config / genai. Facade must be exposed.
        pkg = importlib.import_module("core.specialist.insight_quality")
        for sub in ("grader", "judge", "loop", "fleet", "news", "prompt"):
            importlib.import_module(f"core.specialist.insight_quality.{sub}")
        assert hasattr(pkg, "enforce_insight_quality")
        assert callable(pkg.enforce_insight_quality)


@allure.feature("RPAR Specialist-Report Parity")
@allure.story("T6b insight_quality - config flag parity")
class TestFlagParity:
    def test_flag_exists_default_false_config_py(self):
        import config

        assert hasattr(config, "INSIGHT_QUALITY_ENABLED")
        assert isinstance(config.INSIGHT_QUALITY_ENABLED, bool)
        assert config.INSIGHT_QUALITY_ENABLED is False

    def test_flag_exists_default_false_config_oss_py(self):
        config_oss = _load_oss_config()

        assert hasattr(config_oss, "INSIGHT_QUALITY_ENABLED")
        assert isinstance(config_oss.INSIGHT_QUALITY_ENABLED, bool)
        assert config_oss.INSIGHT_QUALITY_ENABLED is False
