# core/xai/support.py
# XAI-1 / XAI-T5 (#1334) — User-Support (1st-Level FAQ) domain provider.
#
# Answers onboarding / Alpaca-setup / troubleshooting questions STRICTLY from a curated
# FAQ knowledge base — ZERO-HALLUCINATION: the served answer is a FAQ entry's recorded
# text VERBATIM; nothing is invented or paraphrased into a new claim. When no entry is
# relevant enough, it returns an HONEST "not found — here's how to reach a human" and
# flags ``escalate=True``; it NEVER fabricates an answer. No creative LLM touches this path.
#
# OSS read-seam (StaticFaqSource): a bundled static FAQ (a Python constant, optionally
# overridden by an external JSON via XAI_FAQ_PATH). Enterprise injects a vector-DB seam.
# Import-light: stdlib only (no torch, no config import).
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any, Optional

from core.xai.interfaces import IDomainProvider, IFaqSource

logger = logging.getLogger(__name__)

# Tokenizer: lowercase alphanumeric tokens >= 3 chars, minus a small DE+EN question-filler
# stoplist. Deterministic; a miss only narrows the match (a weak match escalates honestly).
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    {
        "wie",
        "was",
        "wo",
        "der",
        "die",
        "das",
        "ein",
        "eine",
        "ich",
        "mein",
        "meine",
        "kann",
        "und",
        "oder",
        "fur",
        "mit",
        "den",
        "dem",
        "ist",
        "sind",
        "nicht",
        "auf",
        "the",
        "what",
        "how",
        "why",
        "where",
        "can",
        "and",
        "for",
        "with",
        "you",
        "your",
        "are",
        "does",
        "did",
        "this",
        "that",
        "from",
        "have",
        "about",
        "tell",
        "please",
    }
)

# Confidence floor: the top FAQ hit must share at least this many meaningful tokens with
# the query to be SERVED. Below it -> honest escalate (the zero-hallucination choice:
# never serve a weak/irrelevant match as if it answered).
_DEFAULT_MIN_SCORE = 2

_ESCALATE_TEXT = (
    "I couldn't find this in the FAQ knowledge base. For community help see the project "
    "README and GitHub Discussions; for errors check the console logs and "
    "oss_audit_logs/. This question is flagged for human follow-up."
)

# Bundled OSS FAQ. `keywords` carry DE+EN morphological variants (there is no stemmer here)
# so a query token matches by equality. Answers are authored once and served VERBATIM.
_DEFAULT_FAQ: list[dict] = [
    {
        "id": "alpaca-connect",
        "question": "Wie verbinde ich mein Alpaca-Konto? / How do I connect my Alpaca account?",
        "answer": (
            "Set ALPACA_API_KEY and ALPACA_SECRET_KEY in your .env (and ALPACA_BASE_URL "
            "for paper vs. live). The OSS desktop runs paper-trading by default "
            "(PAPER_TRADING=true). Restart the app after editing .env."
        ),
        "keywords": [
            "alpaca",
            "konto",
            "account",
            "verbinde",
            "verbinden",
            "verbindung",
            "connect",
            "link",
            "anbinden",
            "broker",
            "apikey",
        ],
    },
    {
        "id": "paper-trading",
        "question": "Handelt der Bot mit echtem Geld? / Does the bot trade real money?",
        "answer": (
            "No. The OSS edition defaults to paper trading (PAPER_TRADING=true) and "
            "SHADOW_MODE intercepts orders in staging. Live trading requires explicit, "
            "deliberate configuration."
        ),
        "keywords": [
            "paper",
            "echtgeld",
            "echtes",
            "geld",
            "money",
            "real",
            "live",
            "trading",
            "handel",
            "handelt",
            "shadow",
            "order",
            "orders",
        ],
    },
    {
        "id": "start-bot",
        "question": "Wie starte ich den Bot? / How do I start the bot?",
        "answer": (
            "Use the bundled desktop launcher (autonomous setup) or run the documented "
            "start command from the README. Watch the console for a startup health line."
        ),
        "keywords": [
            "start",
            "starte",
            "starten",
            "run",
            "launch",
            "begin",
            "bot",
            "app",
        ],
    },
    {
        "id": "logs-where",
        "question": "Wo finde ich die Logs / den Audit-Trail? / Where are the logs?",
        "answer": (
            "The Round-Table audit trail is in oss_audit_logs/audit_log_<date>.jsonl "
            "(hash-chained). Application logs print to the console."
        ),
        "keywords": [
            "logs",
            "log",
            "audit",
            "trail",
            "protokoll",
            "finde",
            "where",
            "decisions",
            "history",
            "historie",
        ],
    },
    {
        "id": "oss-vs-enterprise",
        "question": "Was ist der Unterschied OSS vs. Enterprise? / OSS vs. Enterprise?",
        "answer": (
            "OSS is the single-user local desktop edition (paper trading, local auth, "
            "SQLite). Enterprise adds multi-tenant cloud, BYOC and managed services. "
            "Editions are gated by a license key."
        ),
        "keywords": [
            "oss",
            "enterprise",
            "edition",
            "unterschied",
            "difference",
            "community",
            "license",
            "lizenz",
            "tenant",
            "cloud",
        ],
    },
]


def _tokens(text: str) -> set[str]:
    return {
        t
        for t in _TOKEN_RE.findall((text or "").lower())
        if len(t) >= 3 and t not in _STOP
    }


def _score_entry(query_tokens: set[str], entry: dict) -> int:
    """Meaningful-token overlap of the query with an entry's question + keywords."""
    searchable = _tokens(entry.get("question") or "")
    searchable |= {
        k.lower() for k in (entry.get("keywords") or []) if isinstance(k, str)
    }
    return len(query_tokens & searchable)


def search_faq(faq: list[dict], query: str, *, top_k: int = 3) -> list[dict]:
    """Deterministic token-overlap search. Returns up to ``top_k`` entries with a positive
    score as ``{id, question, answer, score}``, highest score first (id as a stable
    tie-break). No LLM, no fuzzy magic — a faithful, explainable ranking."""
    qt = _tokens(query)
    scored: list[dict] = []
    for e in faq:
        if not isinstance(e, dict):
            continue
        s = _score_entry(qt, e)
        if s > 0:
            scored.append(
                {
                    "id": e.get("id"),
                    "question": e.get("question"),
                    "answer": e.get("answer"),
                    "score": s,
                }
            )
    scored.sort(key=lambda h: (-h["score"], str(h.get("id") or "")))
    return scored[: max(0, top_k)]


def render_answer(top: Optional[dict], *, escalated: bool) -> str:
    """Zero-hallucination rendering: the served answer is the top FAQ entry's text
    VERBATIM; on escalation — OR a missing/non-dict top, OR an empty answer — it is the
    honest no-answer message, never an invented answer.
    """
    if escalated or not isinstance(top, dict):
        return _ESCALATE_TEXT
    answer = (top.get("answer") or "").strip()
    return answer or _ESCALATE_TEXT


class StaticFaqSource(IFaqSource):
    """OSS read-seam: a bundled static FAQ (a Python constant), optionally overridden by an
    external JSON file via ``XAI_FAQ_PATH``. Robust: a missing/malformed override degrades
    to the bundled default, never an exception (a support read must not crash the chat).
    """

    def __init__(self, *, faq: Optional[list[dict]] = None) -> None:
        self._faq = faq if faq is not None else self._load_default()

    @staticmethod
    def _load_default() -> list[dict]:
        # ADR-014 env-boundary seam: XAI_FAQ_PATH is read directly here (NOT via
        # get_config()) — keeping the OSS FAQ path in one place, like SENATE_LOG_DIR.
        path = os.getenv("XAI_FAQ_PATH", "").strip()
        if not path:
            return list(_DEFAULT_FAQ)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            logger.warning(
                "XAI support: could not load XAI_FAQ_PATH %s; using bundled FAQ.",
                path,
                exc_info=True,
            )
            return list(_DEFAULT_FAQ)
        if isinstance(data, list):
            entries = [e for e in data if isinstance(e, dict)]
            if not entries:
                logger.warning(
                    "XAI support: XAI_FAQ_PATH %s yielded 0 usable FAQ entries; serving "
                    "an empty KB (all queries will escalate).",
                    path,
                )
            return entries
        logger.warning(
            "XAI support: XAI_FAQ_PATH %s is not a JSON list; using bundled FAQ.", path
        )
        return list(_DEFAULT_FAQ)

    async def search(self, query: str, *, top_k: int = 3) -> list[dict]:
        return await asyncio.to_thread(search_faq, self._faq, query, top_k=top_k)


class SupportProvider(IDomainProvider):
    """1st-Level support handler: search the FAQ KB, serve the best entry VERBATIM, or
    honestly escalate. Returns ``{text, hits, count, escalate, faq_id}`` so the UI can
    surface the source entry and route un-answered queries to a human."""

    def __init__(
        self,
        *,
        faq_source: Optional[IFaqSource] = None,
        min_score: int = _DEFAULT_MIN_SCORE,
    ) -> None:
        self._faq = faq_source or StaticFaqSource()
        self._min_score = min_score

    async def answer(self, request: Any) -> dict:
        query = getattr(request, "text", "") or ""
        hits = await self._faq.search(query, top_k=3)
        # Guard the injected seam: IFaqSource is external (Enterprise / mock / future), so a
        # non-dict top hit must NEVER crash the chat (the never-crash guarantee) — treat it
        # as "no usable hit".
        top = hits[0] if hits and isinstance(hits[0], dict) else None
        top_score = top.get("score") if top else None
        # Escalate when there is no usable hit, OR a numeric score is present but below the
        # floor. A source returning hits WITHOUT scores (an injected vector DB that did its
        # own filtering) is trusted — we don't manufacture an escalation.
        below_floor = (
            isinstance(top_score, (int, float))
            and not isinstance(top_score, bool)
            and top_score < self._min_score
        )
        text = render_answer(top, escalated=(top is None) or below_floor)
        # Single source of truth: the flags MUST agree with the served text. If rendering
        # fell back to the escalate message (no hit / below floor / empty answer on the
        # entry), report escalate — never tell the UI "answered from KB, source X" while
        # the user actually received the no-answer message.
        escalate = text == _ESCALATE_TEXT
        return {
            "text": text,
            "hits": hits,
            "count": len(hits),
            "escalate": escalate,
            "faq_id": (None if escalate else top.get("id")),
        }
