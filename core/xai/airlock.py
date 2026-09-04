# core/xai/airlock.py
# XAI-1 / XAI-T7 (#1336) — Command-Airlock. Read-only enforcement for actionable intents.
#
# Two layers, both fail-safe:
#   1. XAI_REQUIRE_PLT3_AUTH (fail-closed, default ON) — while PLT-3 (multi-tenant capital
#      isolation) is unverified, ALL actionable commands are blocked at the routing level
#      (Phase-1 read-only), independent of the agent-core dormancy flag. Read per call (NOT
#      latched like dormancy) ON PURPOSE: a fail-closed gate must be re-closable at runtime,
#      and a stale latched-OPEN value would be the dangerous direction.
#   2. Command-Airlock — even once PLT-3 is verified, an actionable command is NEVER executed
#      directly; it becomes a frozen Pending_Transaction draft requiring explicit MFA.
#
# Detection precision (is_actionable) is BEST-EFFORT and explicitly NOT a safety boundary:
# nothing in XAI executes, so a missed command merely routes to a READ provider; the "never
# execute" guarantee holds via the gate + the absence of any execution path. Import-light.
from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

_DISABLE = {"0", "false", "no", "off"}
_POLITE = {"please", "pls", "kindly", "now", "just"}
_MODALS = {"can", "could", "would", "will"}
_GO_DIRECTIONS = {"short", "long"}
_STRIP = "\"'`.,!?:;()[]{}<>-_*#~ "

# Imperative, state-changing verbs (as the command head). A read that merely mentions one
# ("why did we sell AAPL?") is not a command.
_ACTION_VERBS = frozenset(
    {
        "sell",
        "buy",
        "purchase",
        "close",
        "liquidate",
        "cancel",
        "place",
        "execute",
        "exit",
        "rebalance",
        "withdraw",
        "deposit",
        "transfer",
        "short",
        "cover",
        "dump",
        "offload",
        "unwind",
        "flatten",
        "dispose",
        "reduce",
        "long",
    }
)
_QUESTION_WORDS = frozenset(
    {
        "why",
        "what",
        "whats",
        "when",
        "how",
        "who",
        "whom",
        "which",
        "where",
        "is",
        "are",
        "was",
        "were",
        "did",
        "do",
        "does",
        "can",
        "could",
        "should",
        "would",
        "will",
        "show",
        "list",
        "explain",
        "tell",
        "describe",
        "summarize",
        "give",
        "display",
    }
)


def is_plt3_required() -> bool:
    """Fail-closed PLT-3 gate: actionable commands stay disabled UNLESS explicitly turned off
    (XAI_REQUIRE_PLT3_AUTH in 0/false/no/off — set only after PLT-3 is verified). Any other
    value (incl. unset / typo / empty / whitespace) keeps it required."""
    return os.getenv("XAI_REQUIRE_PLT3_AUTH", "1").strip().lower() not in _DISABLE


def _normalize(text: str) -> str:
    """NFKC + drop zero-width/format chars + lower + strip (defeats unicode evasion)."""
    norm = unicodedata.normalize("NFKC", text or "")
    norm = "".join(ch for ch in norm if unicodedata.category(ch) != "Cf")
    return norm.strip().lower()


def is_actionable(text: str) -> bool:
    """Best-effort: True iff ``text`` reads as an imperative state-changing command (not a
    question / read). NOT a safety boundary — see module header."""
    t = _normalize(text)
    if not t:
        return False
    words = [w for w in (token.strip(_STRIP) for token in t.split()) if w]
    if not words:
        return False
    head = words[0]
    if words[:3] == ["get", "rid", "of"]:  # "get rid of my TSLA"
        return True
    if (
        head == "go" and len(words) > 1 and words[1] in _GO_DIRECTIONS
    ):  # "go short NVDA"
        return True
    # Polite-modal command ("can/could/would/will you <verb> ...") — a command despite the
    # modal/question form.
    if head in _MODALS and len(words) > 1 and words[1] in {"you", "u"}:
        return any(w in _ACTION_VERBS for w in words[2:5])
    if t.endswith("?") or head in _QUESTION_WORDS:
        return False
    idx = 1 if head in _POLITE and len(words) > 1 else 0
    return idx < len(words) and words[idx] in _ACTION_VERBS


@dataclass(frozen=True)
class PendingTransaction:
    """A drafted, UNEXECUTED order awaiting MFA confirmation.

    ``frozen`` + ``init=False`` make the safety invariant TYPE-ENFORCED, not just documented:
    ``executed`` is always False and ``requires_mfa`` always True — neither can be set at
    construction nor mutated afterwards. Execution (post-PLT-3, post-MFA) lives elsewhere.
    """

    raw_request: str
    status: str = "pending_confirmation"
    requires_mfa: bool = field(default=True, init=False)
    executed: bool = field(default=False, init=False)


@dataclass
class AirlockDecision:
    kind: str  # "allow" | "blocked" | "pending_confirmation"
    message: str = ""
    draft: Optional[PendingTransaction] = None


_BLOCKED_MSG = (
    "This is a read-only assistant. Actionable commands are disabled until multi-tenant "
    "capital isolation (PLT-3) is verified — nothing was executed."
)
_DRAFT_MSG = (
    "Drafted a pending transaction. It requires explicit MFA confirmation and has NOT "
    "been executed."
)


class CommandAirlock:
    """Screens a request BEFORE routing. Reads pass through; actionable commands never
    execute — blocked while PLT-3 is required, drafted for MFA once it is verified."""

    def screen(self, text: str) -> AirlockDecision:
        if not is_actionable(text):
            return AirlockDecision(kind="allow")
        if is_plt3_required():
            return AirlockDecision(kind="blocked", message=_BLOCKED_MSG)
        return AirlockDecision(
            kind="pending_confirmation",
            message=_DRAFT_MSG,
            draft=PendingTransaction(raw_request=text),
        )
