# core/xai/intent_router.py
# XAI-1 / XAI-T2 (#1331) — 4-Way Intent Router. Fills XaiAgentCore's classifier seam.
#
# Two-stage, deterministic-first and FAIL-SAFE (never confidently mis-route):
#   1. a tiny, high-precision keyword fast-path of UNAMBIGUOUS, system-specific phrases
#      (word-boundary matched; a query hitting >1 domain is treated as ambiguous), then
#   2. an LLM fallback for everything else, via the sanctioned seam
#      core/llm/provider.py get_llm_provider() (desktop: Ollama opt-in / Gemini; cloud:
#      always Gemini). The LLM is asked for EXACTLY one label; the reply is parsed STRICTLY.
# Any uncertainty / conflict / missing provider / LLM error / unclean reply -> None, so the
# agent-core asks the user to clarify rather than guessing a domain (Zero-Hallucination).
from __future__ import annotations

import logging
import re
from typing import Callable, Optional

from core.xai.agent_core import DOMAINS

logger = logging.getLogger(__name__)

# UNAMBIGUOUS, system-specific phrases ONLY. A false positive here silently bypasses the
# LLM, so the bar is high: generic question-forms ("why did", "how do i") are deliberately
# NOT here — they go to the LLM. Word-boundary matched (optional trailing plural 's').
_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "trading_history",
        (
            "the senate",
            "senate vote",
            "senate decision",
            "round table",
            "who voted",
            "decision log",
            "glass box",
        ),
    ),
    (
        "strategy",
        (
            "iron dome",
            "drawdownguard",
            "shap",
            "feature importance",
            "regime detection",
            "agent weight",
        ),
    ),
    (
        "support",
        (
            "connect alpaca",
            "alpaca api",
            "api key",
            "onboarding",
            "reset my password",
            "account setup",
        ),
    ),
    (
        "stock_research",
        (
            "fundamentals",
            "sector analysis",
            "insider trade",
            "news sentiment",
            "p/e ratio",
            "analyst rating",
            "valuation",
        ),
    ),
)

# One word-boundary regex per domain. ``(?<!\w) ... s?(?!\w)`` prevents substring traps
# (e.g. 'regime' must not match inside 'regimen') while allowing a simple trailing plural.
_RULE_RES: tuple[tuple[str, "re.Pattern[str]"], ...] = tuple(
    (
        domain,
        re.compile(
            r"(?<!\w)(?:" + "|".join(re.escape(n) for n in needles) + r")s?(?!\w)"
        ),
    )
    for domain, needles in _RULES
)

LlmProviderFactory = Callable[[], Optional[object]]

_LABELS = frozenset(DOMAINS)
_TOKEN_RE = re.compile(r"[a-z_]+")
_CATEGORY_PREFIX_RE = re.compile(r"^category\s*:?\s*")
_MAX_LABEL_TOKENS = 16


def _default_llm_factory() -> Optional[object]:
    # Lazy import keeps this module import-light and avoids an import cycle.
    from core.llm.provider import get_llm_provider

    return get_llm_provider()


def _build_prompt(text: str) -> str:
    labels = " | ".join(DOMAINS)
    return (
        "You are an intent classifier. Classify the user's message into EXACTLY ONE "
        f"of these categories: {labels}. If none clearly fits, answer 'unknown'. "
        "Reply with ONLY the single category word, nothing else.\n\n"
        f"Message: {text}\nCategory:"
    )


def parse_label(raw: object) -> Optional[str]:
    """Strictly map an LLM reply to ONE domain label, else None (fail-safe).

    Rejects anything that isn't a clean single label: empty, non-str, 'unknown', partials
    ('trading'), chatter ('trading_history is the answer'), negation ('not strategy ...'),
    and multi-label replies ('support\\nstock_research'). Guessing a domain we aren't sure
    of would violate the Zero-Hallucination discipline.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    distinct = {t for t in _TOKEN_RE.findall(raw.lower()) if t in _LABELS}
    if len(distinct) != 1:
        return None  # zero, or ambiguous multiple -> fail-safe
    label = next(iter(distinct))
    cleaned = _CATEGORY_PREFIX_RE.sub("", raw.strip().lower()).splitlines()[0]
    cleaned = cleaned.strip().strip(".,!?:;\"'`() ")
    return label if cleaned == label else None


class IntentRouter:
    """4-Way intent router. Use the (async) ``classify`` as XaiAgentCore's classifier."""

    def __init__(
        self, *, llm_factory: LlmProviderFactory = _default_llm_factory
    ) -> None:
        self._llm_factory = llm_factory

    def rule_classify(self, text: str) -> Optional[str]:
        """Deterministic fast-path. Returns a domain only if EXACTLY ONE unambiguous
        domain matches; zero or multiple (ambiguous) -> None (defer to the LLM)."""
        t = (text or "").lower()
        hits = {domain for domain, rx in _RULE_RES if rx.search(t)}
        return next(iter(hits)) if len(hits) == 1 else None

    async def classify(self, text: str) -> Optional[str]:
        # 1) deterministic fast-path
        domain = self.rule_classify(text)
        if domain is not None:
            return domain

        # 2) LLM fallback via the sanctioned seam. Fail-safe at every step.
        provider = self._llm_factory()
        if provider is None:
            logger.warning("XAI intent-router: no LLM provider available — unresolved.")
            return None
        try:
            raw = await provider.generate_content_async(
                _build_prompt(text), max_output_tokens=_MAX_LABEL_TOKENS
            )
        except Exception:  # noqa: BLE001 — provider should return "", never trust it
            # logger.exception preserves the stack trace (OTel policy 7) so the real
            # cause (timeout / auth / rate-limit) is diagnosable; still fail-safe.
            logger.exception("XAI intent-router: LLM classify failed — unresolved.")
            return None
        return parse_label(raw)
