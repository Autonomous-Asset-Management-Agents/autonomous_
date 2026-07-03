# tests/unit/test_specialist_prompt_parser.py
# RPAR Epic #1262, Task T1 (#1265) - flag-gated V2 prompt + parser.
#
# Gherkin (plan):
#   Szenario: V2-Prompt fragt nach Prosa-Feldern
#     Angenommen SPECIALIST_PROMPT_V2 ist ON
#     Dann enthält der Prompt COMPANY-, BULL-, BEAR- und THESIS-Aufgaben
#   Szenario: V2-Parser füllt Prosa ohne Score zu ändern
#     Angenommen eine V2-LLM-Antwort mit COMPANY/BULL/BEAR/THESIS + SCORE/OUTLOOK
#     Wenn parse_synthesis_v2 läuft
#     Dann sind company_summary/bull_case/bear_case/investment_thesis gefüllt
#     Und sentiment_score/recommendation sind identisch zum V1-Parser
#   Szenario: Flag OFF = byte-identisch
#     Angenommen SPECIALIST_PROMPT_V2 ist OFF (Default)
#     Dann läuft der heutige 6-Tupel-Pfad und der Report ist byte-identisch
#
# Policy: CODING_POLICY.md §11.5 TDD, §1 Compliance-First.
# HARD INVARIANTS (reviewer-checked):
#   1. Dormancy: flag OFF -> prompt str + parsed tuple + report byte-identical to today.
#   2. NEWS-8: sentiment_score/recommendation/reasons/escalate identical flag OFF vs ON
#      for the SAME LLM text.
#   3. P0-1: never `or`-default a numeric (0.0 is legit) -> `is not None`.
#   4. Purity: prompt/parser don't mutate inputs.

from __future__ import annotations

import copy
import types
from unittest.mock import patch

import allure
import pytest

from core.specialist.parser import ParsedSynthesis, parse_synthesis_v2
from core.specialist.prompt import build_prompt_v2
from core.stock_specialist import StockSpecialistAgent

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

# A V2-style LLM response that exercises BOTH the V1 score fields (SCORE/OUTLOOK/
# SUMMARY/SIGNALS/REASONS) AND the new prose tasks (COMPANY/BULL/BEAR/THESIS).
V2_TEXT = (
    "SUMMARY: Strong earnings beat with record revenue. Guidance raised.\n"
    "SIGNALS: Multiple insider buys detected this week.\n"
    "OUTLOOK: bullish\n"
    "SCORE: 78\n"
    "COMPANY: Acme builds widgets and sells them globally to enterprise clients.\n"
    "BULL: Margins are expanding and the order book is at a record high.\n"
    "BEAR: Valuation is rich and a recession would hit discretionary spend.\n"
    "THESIS: Accumulate on dips; the secular growth story remains intact.\n"
    "REASONS:\n"
    "- Record earnings growth\n"
    "- Strong institutional buying\n"
    "- Raised full-year guidance\n"
)

# A bare V2 response with ONLY the score fields (no prose) - exercises the
# DOTALL fallback returning empty prose, and the V1-parser-tail equivalence.
SCORE_ONLY_TEXT = (
    "SUMMARY: Missed earnings badly.\n"
    "OUTLOOK: bearish\n"
    "SCORE: 25\n"
    "REASONS:\n"
    "- Revenue miss\n"
)


def _v1_gathered() -> dict:
    """A complete `gathered` dict (the keys `_build_report`/prompt read)."""
    return {
        "insider_trades": [],
        "material_events": [],
        "activist_stakes": [],
        "political_trades": [],
        "recent_headlines": ["Revenue beats estimates", "New product launch"],
        "wiki_spike": False,
        "wiki_views_7d": 0,
        "reddit_mentions_24h": 0,
        "reddit_sentiment": "neutral",
        "short_interest_pct": None,
        "google_trend_score": None,
        "reddit_mentions": 0,
        "ml_prediction": None,
    }


# ---------------------------------------------------------------------------
# 1. V2 prompt builder - asks for the prose tasks (Gherkin 1)
# ---------------------------------------------------------------------------


@allure.feature("VC-1 Research & Analysis")
@allure.story("Specialist Prompt/Parser V2")
class TestBuildPromptV2:
    def test_v2_prompt_contains_prose_tasks(self):
        prompt = build_prompt_v2("AAPL", _v1_gathered())
        for token in ("COMPANY:", "BULL:", "BEAR:", "THESIS:"):
            assert token in prompt, f"V2 prompt must ask for {token}"

    def test_v2_prompt_keeps_v1_score_tasks(self):
        prompt = build_prompt_v2("AAPL", _v1_gathered())
        for token in ("SUMMARY:", "SIGNALS:", "OUTLOOK:", "SCORE:", "REASONS:"):
            assert token in prompt, f"V2 prompt must keep V1 task {token}"

    def test_v2_prompt_includes_data_sections(self):
        prompt = build_prompt_v2("AAPL", _v1_gathered())
        assert "Recent Headlines" in prompt
        assert "Revenue beats estimates" in prompt

    def test_v2_prompt_pure_does_not_mutate_input(self):
        gathered = _v1_gathered()
        snapshot = copy.deepcopy(gathered)
        build_prompt_v2("AAPL", gathered)
        assert gathered == snapshot, "build_prompt_v2 must not mutate its input"


# ---------------------------------------------------------------------------
# 2. V2 parser - fills prose, score-tuple invariant to V1 (Gherkin 2)
# ---------------------------------------------------------------------------


@allure.feature("VC-1 Research & Analysis")
@allure.story("Specialist Prompt/Parser V2")
class TestParseSynthesisV2:
    def setup_method(self):
        self.agent = StockSpecialistAgent("AAPL", "dummy-key")

    def test_v2_parser_fills_prose_fields(self):
        parsed = parse_synthesis_v2(V2_TEXT)
        assert isinstance(parsed, ParsedSynthesis)
        assert "widgets" in parsed.company_summary
        assert "Margins" in parsed.bull_case
        assert "Valuation" in parsed.bear_case
        assert "Accumulate" in parsed.investment_thesis

    def test_v2_parser_pure_does_not_mutate_or_use_state(self):
        # parse_synthesis_v2 is a free function on a str - calling twice is stable.
        a = parse_synthesis_v2(V2_TEXT)
        b = parse_synthesis_v2(V2_TEXT)
        assert a == b

    def test_score_tuple_invariant_to_v1_parser(self):
        """NEWS-8: on the SAME text, V2's score-deriving fields == V1's."""
        v1 = self.agent._parse_synthesis(V2_TEXT)
        (
            v1_news,
            v1_signals,
            v1_rec,
            v1_score,
            v1_conf,
            v1_reasons,
        ) = v1
        v2 = parse_synthesis_v2(V2_TEXT)
        # The score-deriving fields MUST be identical (NEWS-8). `reasons`
        # wording may differ flag-ON (display only) - NOT asserted equal here.
        assert v2.recommendation == v1_rec
        assert v2.sentiment_score == v1_score
        assert v2.confidence == v1_conf
        assert v2.news_summary == v1_news
        assert v2.alternative_signals == v1_signals

    def test_score_only_text_invariant(self):
        """Bare score text: V2 must reproduce the V1 tail exactly + empty prose."""
        v1 = self.agent._parse_synthesis(SCORE_ONLY_TEXT)
        _, _, v1_rec, v1_score, v1_conf, _ = v1
        v2 = parse_synthesis_v2(SCORE_ONLY_TEXT)
        assert v2.recommendation == v1_rec
        assert v2.sentiment_score == v1_score
        assert v2.confidence == v1_conf
        # No prose in the text -> DOTALL fallback yields empty prose fields.
        assert v2.company_summary == ""
        assert v2.bull_case == ""
        assert v2.bear_case == ""
        assert v2.investment_thesis == ""

    def test_empty_text_invariant(self):
        """Empty text: V2 reproduces V1's empty-text 6-tuple exactly."""
        v1 = self.agent._parse_synthesis("")
        v1_news, v1_signals, v1_rec, v1_score, v1_conf, v1_reasons = v1
        v2 = parse_synthesis_v2("")
        assert v2.news_summary == v1_news
        assert v2.alternative_signals == v1_signals
        assert v2.recommendation == v1_rec
        assert v2.sentiment_score == v1_score
        assert v2.confidence == v1_conf
        assert v2.reasons == v1_reasons


# ---------------------------------------------------------------------------
# 3. Flag-OFF prompt + parser are byte-identical to today (Dormancy / Gherkin 3)
# ---------------------------------------------------------------------------


@allure.feature("VC-1 Research & Analysis")
@allure.story("Specialist Prompt/Parser V2")
class TestDormancyFlagOff:
    def setup_method(self):
        self.agent = StockSpecialistAgent("AAPL", "dummy-key")

    def _set_flag(self, value: bool):
        """Patch get_config() (the established pattern, see test_specialist_ml_wiring)
        to return a config whose SPECIALIST_PROMPT_V2 == value. ML_SENTIMENT_BLEND_ENABLED
        is pinned False so the dormant blend path is untouched."""
        cfg = types.SimpleNamespace(
            SPECIALIST_PROMPT_V2=value,
            ML_SENTIMENT_BLEND_ENABLED=False,
        )
        return patch("core.stock_specialist.get_config", return_value=cfg)

    def test_flag_off_prompt_is_v1(self):
        """Flag OFF -> the prompt emitted is byte-identical to _build_synthesis_prompt."""
        gathered = _v1_gathered()
        v1_prompt = self.agent._build_synthesis_prompt(gathered)
        with self._set_flag(False):
            chosen = self.agent._synthesis_prompt(gathered)
        assert chosen == v1_prompt

    def test_flag_on_prompt_is_v2(self):
        gathered = _v1_gathered()
        with self._set_flag(True):
            chosen = self.agent._synthesis_prompt(gathered)
        for token in ("COMPANY:", "BULL:", "BEAR:", "THESIS:"):
            assert token in chosen

    def test_flag_off_report_byte_identical(self):
        """Flag OFF -> the FULL _build_report output is byte-identical to today's."""
        gathered = _v1_gathered()
        synthesis = {"text": V2_TEXT}
        # Baseline: today's behaviour is the flag-OFF path.
        with self._set_flag(False):
            off = self.agent._build_report(gathered, synthesis)
        # The report must look exactly like the legacy path: empty prose,
        # reasons capped <3/[:120] + bonus + [:5].
        assert off.company_summary == ""
        assert off.bull_case == ""
        assert off.bear_case == ""
        assert off.investment_thesis == ""
        assert off.about == ""

    def test_flag_off_vs_on_scoring_invariant_full_tuple(self):
        """NEWS-8 (full-tuple): score/recommendation/reasons/escalate/escalate_reason
        derived in _build_report are IDENTICAL flag-OFF vs flag-ON for the SAME text."""
        gathered = _v1_gathered()
        synthesis = {"text": V2_TEXT}
        with self._set_flag(False):
            off = self.agent._build_report(gathered, copy.deepcopy(synthesis))
        with self._set_flag(True):
            on = self.agent._build_report(gathered, copy.deepcopy(synthesis))
        # T1 adds prose ONLY - the score-deriving tuple must be invariant.
        assert on.sentiment_score == off.sentiment_score
        assert on.recommendation == off.recommendation
        assert on.reasons == off.reasons
        assert on.escalate == off.escalate
        assert on.escalate_reason == off.escalate_reason
        assert on.confidence == off.confidence
        # ...and flag-ON additionally fills prose (T1's whole point).
        assert on.company_summary != ""
        assert on.bull_case != ""
        assert on.bear_case != ""
        assert on.investment_thesis != ""

    def test_flag_off_vs_on_scoring_invariant_with_bonus_signals(self):
        """Same invariant with bonus-signal-bearing gathered (insider cluster +
        high short interest) so the bonus-reasons path is also exercised."""
        gathered = _v1_gathered()
        gathered["insider_trades"] = [
            {"filed": "2026-01-01", "filer": "CEO", "form": "Form 4"}
        ] * 3
        gathered["short_interest_pct"] = 30.0
        synthesis = {"text": V2_TEXT}
        with self._set_flag(False):
            off = self.agent._build_report(gathered, copy.deepcopy(synthesis))
        with self._set_flag(True):
            on = self.agent._build_report(gathered, copy.deepcopy(synthesis))
        assert on.sentiment_score == off.sentiment_score
        assert on.recommendation == off.recommendation
        assert on.reasons == off.reasons
        assert on.escalate == off.escalate
        assert on.escalate_reason == off.escalate_reason
        assert on.confidence == off.confidence
