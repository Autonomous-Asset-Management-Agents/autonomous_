# core/xai/strategy.py
# XAI-1 / XAI-T4 (#1333) — Trading-Strategies (Explainability/SHAP) domain provider.
#
# Explains WHY the Round Table / a specific agent decided as it did — STRICTLY from the
# recorded decision, ZERO-HALLUCINATION: every rendered weight/score/reasoning is copied
# from the audit entry; nothing is computed or invented. OSS serves a DEGRADED, clearly
# marked local explanation derived from the recorded vote weights (NOT SHAP); Enterprise
# injects a Vertex-AI SHAP IExplainabilitySource. No creative LLM touches this path.
# Import-light (reuses the T3 senate reader + lossless number formatter).
from __future__ import annotations

import re
from typing import Any, Optional

from core.xai.interfaces import IDomainProvider, IExplainabilitySource, ISenateLogReader
from core.xai.trading_history import JsonlSenateLogReader, _fmt_num, extract_symbol

# A CamelCase agent token, e.g. "DrawdownGuard", "RiskManager". A miss just means we render
# the whole ranking instead of focusing one agent (data stays real either way).
_AGENT_RE = re.compile(r"\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b")

_DEGRADED_NOTE = "(Degraded local explanation from recorded vote weights — not SHAP.)"
_NO_DECISION = "No recorded Round-Table decision found to explain."


def extract_agent(text: str) -> Optional[str]:
    """First CamelCase agent-name token in the text (e.g. 'DrawdownGuard'), else None."""
    m = _AGENT_RE.search(text or "")
    return m.group(0) if m else None


def _neg_abs_weight(feature: dict) -> float:
    w = feature.get("weight")
    if isinstance(w, bool) or not isinstance(w, (int, float)):
        return 0.0
    return -abs(w)


def shape_features(entry: dict) -> list:
    """Degraded feature-importances from a decision's RECORDED votes: each agent is a
    'feature', its importance = the recorded weight. Sorted by |weight| desc (stable by
    agent name). Zero-hallucination: every value is copied verbatim from the audit entry.
    """
    feats: list = []
    for v in entry.get("votes") or []:
        if not isinstance(v, dict):
            continue
        feats.append(
            {
                "agent": v.get("agent_name") or v.get("name") or "?",
                "weight": v.get("weight"),
                "score": v.get("score"),
                "signal": v.get("signal"),
                "vetoed": bool(v.get("vetoed")),
                "reasoning": (v.get("reasoning") or "").strip(),
            }
        )
    feats.sort(key=lambda f: (_neg_abs_weight(f), str(f.get("agent"))))
    return feats


def _render_feature(f: dict) -> str:
    s = str(f.get("agent") or "?")
    bits = []
    w = _fmt_num(f.get("weight"))
    sc = _fmt_num(f.get("score"))
    if w is not None:
        bits.append(f"weight {w}")
    if sc is not None:
        bits.append(f"score {sc}")
    if f.get("signal"):
        bits.append(f"signal {f['signal']}")
    if bits:
        s += " (" + ", ".join(bits) + ")"
    if f.get("vetoed"):
        s += " [VETOED]"
    reasoning = (f.get("reasoning") or "").strip()
    if reasoning:
        s += f' — "{reasoning}"'
    return s


def render_explanation(
    features: list, *, agent: Optional[str] = None, degraded: bool = True
) -> str:
    """Deterministic, zero-hallucination rendering. If ``agent`` is named AND present in the
    recorded votes, focus on it; if named but ABSENT, say so honestly (never fabricate a
    vote). A veto is decisive -> always shown (truncation only drops lower-weight non-vetoes,
    and any omission is disclosed truthfully). Empty -> honest no-data."""
    if not features:
        return _NO_DECISION
    note = [_DEGRADED_NOTE] if degraded else []

    if agent:
        match = next(
            (f for f in features if agent.lower() in str(f.get("agent") or "").lower()),
            None,
        )
        if match:
            return "\n".join(
                [f"Why {match.get('agent')} voted as it did:", _render_feature(match)]
                + note
            )
        head = (
            f"No recorded vote by '{agent}' in this decision. "
            "Recorded factors (by weight):"
        )
    else:
        head = "Recorded decision factors (by weight):"

    vetoed = [f for f in features if f.get("vetoed")]
    others = [f for f in features if not f.get("vetoed")]
    shown = vetoed + others[: max(0, 5 - len(vetoed))]
    omitted = len(features) - len(shown)
    body = "\n".join(_render_feature(f) for f in shown)
    if omitted > 0:
        body += f"\n(+{omitted} more factor(s) not shown)"
    return "\n".join([head, body] + note)


class LocalExplainabilitySource(IExplainabilitySource):
    """OSS degraded IExplainabilitySource: shapes the RECORDED vote weights of a decision
    (looked up by session_id) into feature-importances. NOT SHAP. Reads the audit trail via
    the (injected) senate reader; returns None if the decision id is not found."""

    def __init__(self, *, reader: Optional[ISenateLogReader] = None) -> None:
        self._reader = reader or JsonlSenateLogReader()

    async def get_feature_importance(self, decision_id: str) -> Optional[dict]:
        decisions = await self._reader.read_decisions(limit=200)
        entry = next(
            (d for d in decisions if str(d.get("session_id")) == str(decision_id)), None
        )
        if entry is None:
            return None
        return {
            "decision_id": decision_id,
            "features": shape_features(entry),
            "degraded": True,
            "method": "recorded vote weights (degraded; not SHAP)",
        }


class StrategyProvider(IDomainProvider):
    """Explainability domain handler: find the relevant decision and explain it.

    OSS (no injected explainer): shape a DEGRADED explanation directly from the recorded
    vote weights. Enterprise: an injected IExplainabilitySource (Vertex SHAP) supplies the
    richer importances; if it returns nothing, fall back to the degraded recorded view (never
    nothing). Returns ``{text, explanation, degraded, decision_id, agent}``."""

    def __init__(
        self,
        *,
        reader: Optional[ISenateLogReader] = None,
        explainer: Optional[IExplainabilitySource] = None,
    ) -> None:
        self._reader = reader or JsonlSenateLogReader()
        self._explainer = explainer  # None => OSS degraded-from-record path

    async def answer(self, request: Any) -> dict:
        text = getattr(request, "text", "") or ""
        agent = extract_agent(text)
        symbol = extract_symbol(text)
        decisions = await self._reader.read_decisions(symbol=symbol, limit=1)
        if not decisions:
            return {
                "text": _NO_DECISION,
                "explanation": [],
                "degraded": True,
                "decision_id": None,
                "agent": agent,
            }
        entry = decisions[0]
        decision_id = str(entry.get("session_id") or "") or None
        features = shape_features(entry)
        degraded = True
        if self._explainer is not None and decision_id:
            shap = await self._explainer.get_feature_importance(decision_id)
            feats = shap.get("features") if isinstance(shap, dict) else None
            # Trust an injected (Enterprise) source ONLY if it returns a non-empty list of
            # dicts — a malformed `features` must fall back to the recorded degraded view,
            # never crash. And treat the result as DEGRADED unless the source EXPLICITLY
            # declares full fidelity (degraded is False): a missing/None/odd value must never
            # silently drop the "(degraded — not SHAP)" disclosure.
            if (
                isinstance(feats, list)
                and feats
                and all(isinstance(f, dict) for f in feats)
            ):
                features = feats
                degraded = shap.get("degraded") is not False
        return {
            "text": render_explanation(features, agent=agent, degraded=degraded),
            "explanation": features,
            "degraded": degraded,
            "decision_id": decision_id,
            "agent": agent,
        }
