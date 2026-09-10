# tests/unit/test_xai_strategy.py
# XAI-1 / XAI-T4 (#1333) — Trading-Strategies (Explainability) domain provider.
# Pins: CamelCase agent extraction, ZERO-HALLUCINATION shaping (every value copied from the
# audit entry; a named-but-absent agent is reported honestly, never fabricated), veto never
# hidden by truncation, degraded clearly marked, OSS-degraded vs Enterprise-SHAP path,
# no-decision honesty, provider wiring, import-light.
import os
import subprocess
import sys
from unittest.mock import AsyncMock

import allure
import pytest

from core.xai.agent_core import XaiRequest
from core.xai.interfaces import IDomainProvider, IExplainabilitySource, ISenateLogReader
from core.xai.strategy import (
    _DEGRADED_NOTE,
    _NO_DECISION,
    LocalExplainabilitySource,
    StrategyProvider,
    extract_agent,
    render_explanation,
    shape_features,
)


def _vote(agent, weight, *, score=0.5, signal="BUY", vetoed=False, reasoning=""):
    return {
        "agent_name": agent,
        "weight": weight,
        "score": score,
        "signal": signal,
        "vetoed": vetoed,
        "reasoning": reasoning,
    }


def _decision(session_id="s1", symbol="AAPL", votes=None):
    return {
        "session_id": session_id,
        "symbol": symbol,
        "signal_action": "SELL",
        "consensus_score": 0.32,
        "votes": (
            votes
            if votes is not None
            else [
                _vote(
                    "DrawdownGuard",
                    0.5,
                    score=0.2,
                    signal="SELL",
                    reasoning="drawdown risk high",
                ),
                _vote("MomentumAgent", 0.3, score=0.7, signal="BUY"),
            ]
        ),
    }


@allure.feature("XAI-1 Transparency Window")
@allure.story("Trading-Strategies Explainability (XAI-T4)")
class TestPureFns:
    def test_extract_agent(self):
        assert (
            extract_agent("warum hat DrawdownGuard so abgestimmt?") == "DrawdownGuard"
        )
        assert extract_agent("why did RiskManager veto") == "RiskManager"
        assert extract_agent("why the sell decision") is None

    def test_shape_features_sorted_by_abs_weight(self):
        feats = shape_features(_decision())
        assert [f["agent"] for f in feats] == ["DrawdownGuard", "MomentumAgent"]
        assert (
            feats[0]["weight"] == 0.5 and feats[0]["reasoning"] == "drawdown risk high"
        )

    def test_shape_features_skips_non_dict_and_empty(self):
        assert shape_features({"votes": ["junk", None]}) == []
        assert shape_features({}) == []


@allure.feature("XAI-1 Transparency Window")
@allure.story("Trading-Strategies Explainability (XAI-T4)")
class TestRender:
    def test_focuses_named_agent_verbatim(self):
        out = render_explanation(shape_features(_decision()), agent="DrawdownGuard")
        assert "Why DrawdownGuard voted as it did:" in out
        assert "weight 0.5" in out and '"drawdown risk high"' in out
        assert _DEGRADED_NOTE in out

    def test_named_but_absent_agent_is_honest(self):
        out = render_explanation(shape_features(_decision()), agent="GhostAgent")
        assert "No recorded vote by 'GhostAgent'" in out
        assert "GhostAgent (" not in out  # never fabricates a vote line for it

    def test_veto_never_hidden_by_truncation(self):
        votes = [_vote(f"A{i}", 0.9 - i * 0.01) for i in range(8)]
        votes.append(_vote("Vetoer", 0.01, vetoed=True, reasoning="hard block"))
        out = render_explanation(shape_features({"votes": votes}))
        assert "Vetoer" in out and "[VETOED]" in out  # shown despite lowest weight
        assert "more factor(s) not shown" in out

    def test_not_degraded_has_no_note(self):
        out = render_explanation(shape_features(_decision()), degraded=False)
        assert _DEGRADED_NOTE not in out

    def test_empty_is_no_decision(self):
        assert render_explanation([]) == _NO_DECISION


@allure.feature("XAI-1 Transparency Window")
@allure.story("Trading-Strategies Explainability (XAI-T4)")
class TestLocalExplainabilitySource:
    @pytest.mark.anyio
    async def test_finds_by_decision_id(self):
        reader = AsyncMock(spec=ISenateLogReader)
        reader.read_decisions.return_value = [_decision(session_id="s9")]
        src = LocalExplainabilitySource(reader=reader)
        out = await src.get_feature_importance("s9")
        assert (
            out["degraded"] is True and out["features"][0]["agent"] == "DrawdownGuard"
        )

    @pytest.mark.anyio
    async def test_unknown_id_returns_none(self):
        reader = AsyncMock(spec=ISenateLogReader)
        reader.read_decisions.return_value = [_decision(session_id="s1")]
        assert (
            await LocalExplainabilitySource(reader=reader).get_feature_importance("zzz")
            is None
        )


@allure.feature("XAI-1 Transparency Window")
@allure.story("Trading-Strategies Explainability (XAI-T4)")
class TestStrategyProvider:
    @pytest.mark.anyio
    async def test_gherkin_oss_degraded(self):
        # Given a decision with computed weights; When "warum hat DrawdownGuard so
        # abgestimmt?"; Then OSS serves a degraded, clearly-marked explanation.
        reader = AsyncMock(spec=ISenateLogReader)
        reader.read_decisions.return_value = [_decision()]
        res = await StrategyProvider(reader=reader).answer(
            XaiRequest(text="warum hat DrawdownGuard so abgestimmt?")
        )
        assert res["degraded"] is True
        assert res["agent"] == "DrawdownGuard"
        assert "DrawdownGuard" in res["text"] and _DEGRADED_NOTE in res["text"]
        assert "drawdown risk high" in res["text"]  # verbatim from the record

    @pytest.mark.anyio
    async def test_no_decision_is_honest(self):
        reader = AsyncMock(spec=ISenateLogReader)
        reader.read_decisions.return_value = []
        res = await StrategyProvider(reader=reader).answer(
            XaiRequest(text="why did X vote")
        )
        assert res["text"] == _NO_DECISION
        assert res["degraded"] is True and res["decision_id"] is None

    @pytest.mark.anyio
    async def test_enterprise_shap_path(self):
        reader = AsyncMock(spec=ISenateLogReader)
        reader.read_decisions.return_value = [_decision(session_id="s1")]
        explainer = AsyncMock(spec=IExplainabilitySource)
        explainer.get_feature_importance.return_value = {
            "features": [{"agent": "SHAP_f1", "weight": 9}],
            "degraded": False,
        }
        res = await StrategyProvider(reader=reader, explainer=explainer).answer(
            XaiRequest(text="explain the decision")
        )
        assert res["degraded"] is False
        assert res["explanation"][0]["agent"] == "SHAP_f1"

    @pytest.mark.anyio
    async def test_enterprise_empty_falls_back_to_degraded(self):
        reader = AsyncMock(spec=ISenateLogReader)
        reader.read_decisions.return_value = [_decision()]
        explainer = AsyncMock(spec=IExplainabilitySource)
        explainer.get_feature_importance.return_value = None  # SHAP unavailable
        res = await StrategyProvider(reader=reader, explainer=explainer).answer(
            XaiRequest(text="explain")
        )
        assert res["degraded"] is True  # fell back to the recorded degraded view
        assert res["explanation"][0]["agent"] == "DrawdownGuard"

    @pytest.mark.anyio
    async def test_shap_missing_degraded_key_defaults_to_degraded(self):
        # P0: a SHAP source omitting `degraded` must NOT silently drop the disclosure.
        reader = AsyncMock(spec=ISenateLogReader)
        reader.read_decisions.return_value = [_decision()]
        explainer = AsyncMock(spec=IExplainabilitySource)
        explainer.get_feature_importance.return_value = {
            "features": [{"agent": "f1", "weight": 1}]  # no `degraded` key
        }
        res = await StrategyProvider(reader=reader, explainer=explainer).answer(
            XaiRequest(text="explain")
        )
        assert res["degraded"] is True and _DEGRADED_NOTE in res["text"]

    @pytest.mark.anyio
    async def test_shap_explicit_full_fidelity_drops_note(self):
        reader = AsyncMock(spec=ISenateLogReader)
        reader.read_decisions.return_value = [_decision()]
        explainer = AsyncMock(spec=IExplainabilitySource)
        explainer.get_feature_importance.return_value = {
            "features": [{"agent": "f1", "weight": 1}],
            "degraded": False,  # explicit full fidelity
        }
        res = await StrategyProvider(reader=reader, explainer=explainer).answer(
            XaiRequest(text="explain")
        )
        assert res["degraded"] is False and _DEGRADED_NOTE not in res["text"]

    @pytest.mark.anyio
    @pytest.mark.parametrize("feats", ["notalist", ["junk"], {}, [], {"f": 1}])
    async def test_shap_malformed_features_falls_back_no_crash(self, feats):
        # P0: malformed SHAP features must fall back to the recorded degraded view, never crash.
        reader = AsyncMock(spec=ISenateLogReader)
        reader.read_decisions.return_value = [_decision()]
        explainer = AsyncMock(spec=IExplainabilitySource)
        explainer.get_feature_importance.return_value = {
            "features": feats,
            "degraded": False,
        }
        res = await StrategyProvider(reader=reader, explainer=explainer).answer(
            XaiRequest(text="explain")
        )
        assert res["degraded"] is True
        assert res["explanation"][0]["agent"] == "DrawdownGuard"
        assert _DEGRADED_NOTE in res["text"]

    @pytest.mark.anyio
    async def test_degraded_payload_matches_rendered_note(self):
        # invariant: res["degraded"] <=> the degraded note is in the text.
        reader = AsyncMock(spec=ISenateLogReader)
        reader.read_decisions.return_value = [_decision()]
        res = await StrategyProvider(reader=reader).answer(XaiRequest(text="explain"))
        assert res["degraded"] is (_DEGRADED_NOTE in res["text"])

    @pytest.mark.anyio
    async def test_payload_shape(self):
        reader = AsyncMock(spec=ISenateLogReader)
        reader.read_decisions.return_value = [_decision()]
        res = await StrategyProvider(reader=reader).answer(XaiRequest(text="explain"))
        assert set(res) == {"text", "explanation", "degraded", "decision_id", "agent"}

    def test_is_domain_provider(self):
        assert isinstance(StrategyProvider(), IDomainProvider)


@allure.feature("XAI-1 Transparency Window")
@allure.story("Trading-Strategies Explainability (XAI-T4)")
class TestImportLight:
    def test_no_torch_pulled(self):
        root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        code = (
            "import sys\n"
            "import core.xai.strategy\n"
            "bad = sorted(m for m in sys.modules if m == 'torch' or m.startswith('torch.'))\n"
            "assert not bad, bad\n"
        )
        r = subprocess.run(
            [sys.executable, "-c", code], cwd=root, capture_output=True, text=True
        )
        assert r.returncode == 0, (r.stdout, r.stderr)
