# tests/unit/test_xai_support.py
# XAI-1 / XAI-T5 (#1334) — User-Support (1st-Level FAQ) domain provider.
# Pins: deterministic token-overlap search, ZERO-HALLUCINATION (verbatim FAQ answer or an
# honest escalate — never an invented answer), conservative confidence floor (a weak match
# escalates), robust external-FAQ loading (missing / malformed / non-list -> bundled
# default, never raises), injected-source trust, provider wiring, import-light.
import json
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import allure
import pytest

from core.xai.agent_core import XaiRequest
from core.xai.interfaces import IDomainProvider, IFaqSource
from core.xai.support import (
    _DEFAULT_FAQ,
    _ESCALATE_TEXT,
    StaticFaqSource,
    SupportProvider,
    render_answer,
    search_faq,
)

_GHERKIN_Q = "wie verbinde ich mein Alpaca-Konto?"
_ALPACA_ANSWER = _DEFAULT_FAQ[0]["answer"]


@allure.feature("XAI-1 Transparency Window")
@allure.story("User-Support 1st-Level FAQ (XAI-T5)")
class TestSearchFaq:
    def test_relevant_query_ranks_right_entry_first(self):
        hits = search_faq(_DEFAULT_FAQ, _GHERKIN_Q)
        assert hits, "expected a FAQ hit for the Alpaca-connect question"
        assert hits[0]["id"] == "alpaca-connect"
        assert hits[0]["score"] >= 2

    def test_no_match_returns_empty(self):
        assert search_faq(_DEFAULT_FAQ, "tell me a joke about cats") == []

    def test_deterministic_order_score_then_id(self):
        faq = [
            {"id": "b", "question": "x", "answer": "B", "keywords": ["zeta"]},
            {"id": "a", "question": "x", "answer": "A", "keywords": ["zeta"]},
        ]
        # equal score -> stable id tie-break (a before b)
        assert [h["id"] for h in search_faq(faq, "zeta")] == ["a", "b"]

    def test_top_k_limits(self):
        q = "trading account logs enterprise alpaca"
        assert len(search_faq(_DEFAULT_FAQ, q, top_k=2)) == 2

    def test_skips_non_dict_entries(self):
        assert search_faq(
            ["junk", {"id": "k", "answer": "A", "keywords": ["zeta"]}], "zeta"
        )


@allure.feature("XAI-1 Transparency Window")
@allure.story("User-Support 1st-Level FAQ (XAI-T5)")
class TestStaticFaqSource:
    @pytest.mark.anyio
    async def test_default_bundle_answers_gherkin(self):
        hits = await StaticFaqSource().search(_GHERKIN_Q)
        assert hits and hits[0]["id"] == "alpaca-connect"

    @pytest.mark.anyio
    async def test_external_path_override(self, tmp_path, monkeypatch):
        f = tmp_path / "faq.json"
        f.write_text(
            json.dumps(
                [{"id": "z", "question": "zonk", "answer": "ZA", "keywords": ["zonk"]}]
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("XAI_FAQ_PATH", str(f))
        hits = await StaticFaqSource().search("zonk")
        assert hits and hits[0]["id"] == "z" and hits[0]["answer"] == "ZA"

    @pytest.mark.anyio
    async def test_malformed_external_falls_back(self, tmp_path, monkeypatch):
        f = tmp_path / "bad.json"
        f.write_text("{ not valid json", encoding="utf-8")
        monkeypatch.setenv("XAI_FAQ_PATH", str(f))
        hits = await StaticFaqSource().search(_GHERKIN_Q)  # must NOT raise
        assert hits and hits[0]["id"] == "alpaca-connect"  # fell back to bundle

    @pytest.mark.anyio
    async def test_missing_external_falls_back(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XAI_FAQ_PATH", str(tmp_path / "nope.json"))
        assert await StaticFaqSource().search(_GHERKIN_Q)  # must NOT raise

    @pytest.mark.anyio
    async def test_non_list_external_falls_back(self, tmp_path, monkeypatch):
        f = tmp_path / "obj.json"
        f.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
        monkeypatch.setenv("XAI_FAQ_PATH", str(f))
        assert await StaticFaqSource().search(_GHERKIN_Q)


@allure.feature("XAI-1 Transparency Window")
@allure.story("User-Support 1st-Level FAQ (XAI-T5)")
class TestRenderAnswer:
    def test_escalated_returns_honest_text(self):
        assert render_answer({"answer": "X"}, escalated=True) == _ESCALATE_TEXT

    def test_no_top_returns_honest_text(self):
        assert render_answer(None, escalated=False) == _ESCALATE_TEXT

    def test_non_dict_top_returns_honest_text(self):
        assert render_answer("junk", escalated=False) == _ESCALATE_TEXT

    def test_served_answer_is_verbatim_stripped(self):
        assert render_answer({"answer": "  hello  "}, escalated=False) == "hello"

    def test_empty_answer_escalates(self):
        assert render_answer({"answer": "   "}, escalated=False) == _ESCALATE_TEXT


@allure.feature("XAI-1 Transparency Window")
@allure.story("User-Support 1st-Level FAQ (XAI-T5)")
class TestSupportProvider:
    @pytest.mark.anyio
    async def test_gherkin_answers_from_kb_no_escalation(self):
        # Given the FAQ KB; When the Alpaca-connect question; Then answered VERBATIM from
        # the KB, no human escalation.
        res = await SupportProvider().answer(XaiRequest(text=_GHERKIN_Q))
        assert res["escalate"] is False
        assert res["faq_id"] == "alpaca-connect"
        assert res["text"] == _ALPACA_ANSWER  # verbatim — not paraphrased/invented

    @pytest.mark.anyio
    async def test_unknown_query_escalates_without_fabrication(self):
        res = await SupportProvider().answer(
            XaiRequest(text="what is the airspeed of a swallow")
        )
        assert res["escalate"] is True
        assert res["faq_id"] is None
        assert res["text"] == _ESCALATE_TEXT  # honest no-answer, nothing invented

    @pytest.mark.anyio
    async def test_weak_single_token_match_escalates(self):
        # 'cloud' overlaps exactly one keyword (oss-vs-enterprise) -> below floor -> escalate.
        res = await SupportProvider().answer(XaiRequest(text="cloud"))
        assert res["escalate"] is True

    @pytest.mark.anyio
    async def test_lower_floor_can_serve_single_token(self):
        res = await SupportProvider(min_score=1).answer(XaiRequest(text="cloud"))
        assert res["escalate"] is False and res["faq_id"] == "oss-vs-enterprise"

    @pytest.mark.anyio
    async def test_injected_scored_source_is_served(self):
        src = AsyncMock(spec=IFaqSource)
        src.search.return_value = [
            {"id": "x", "question": "q", "answer": "ANSWER", "score": 9}
        ]
        res = await SupportProvider(faq_source=src).answer(XaiRequest(text="q"))
        assert res["escalate"] is False and res["text"] == "ANSWER"

    @pytest.mark.anyio
    async def test_injected_unscored_source_is_trusted(self):
        # A source that returns hits WITHOUT scores (did its own filtering) is trusted —
        # we must not manufacture an escalation.
        src = AsyncMock(spec=IFaqSource)
        src.search.return_value = [{"id": "x", "answer": "B"}]
        res = await SupportProvider(faq_source=src).answer(XaiRequest(text="q"))
        assert res["escalate"] is False and res["text"] == "B"

    @pytest.mark.anyio
    async def test_scored_but_empty_answer_escalates(self):
        # A scored hit with no usable answer must NOT be reported as answered-from-KB
        # (else the UI shows "source X, answered" while the user got the no-answer message).
        src = AsyncMock(spec=IFaqSource)
        src.search.return_value = [{"id": "x", "score": 9, "answer": "   "}]
        res = await SupportProvider(faq_source=src).answer(XaiRequest(text="q"))
        assert res["escalate"] is True
        assert res["faq_id"] is None
        assert res["text"] == _ESCALATE_TEXT

    @pytest.mark.anyio
    async def test_non_dict_hit_does_not_crash(self):
        # Never-crash guarantee: a non-dict hit from an injected seam degrades to escalate.
        src = AsyncMock(spec=IFaqSource)
        src.search.return_value = ["junk"]
        res = await SupportProvider(faq_source=src).answer(XaiRequest(text="q"))
        assert res["escalate"] is True and res["faq_id"] is None
        assert res["count"] == 1  # raw hits are still surfaced

    @pytest.mark.anyio
    async def test_none_text_request_does_not_crash(self):
        res = await SupportProvider().answer(SimpleNamespace(text=None))
        assert res["escalate"] is True  # no hit on empty query, no crash on text=None

    @pytest.mark.anyio
    async def test_payload_shape(self):
        res = await SupportProvider().answer(XaiRequest(text=_GHERKIN_Q))
        assert set(res) == {"text", "hits", "count", "escalate", "faq_id"}
        assert res["count"] == len(res["hits"])

    def test_is_domain_provider(self):
        assert isinstance(SupportProvider(), IDomainProvider)


@allure.feature("XAI-1 Transparency Window")
@allure.story("User-Support 1st-Level FAQ (XAI-T5)")
class TestImportLight:
    def test_no_torch_pulled(self):
        root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        code = (
            "import sys\n"
            "import core.xai.support\n"
            "bad = sorted(m for m in sys.modules if m == 'torch' or m.startswith('torch.'))\n"
            "assert not bad, bad\n"
        )
        r = subprocess.run(
            [sys.executable, "-c", code], cwd=root, capture_output=True, text=True
        )
        assert r.returncode == 0, (r.stdout, r.stderr)
