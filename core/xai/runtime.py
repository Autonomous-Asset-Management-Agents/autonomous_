# core/xai/runtime.py
# XAI-1 / XAI-T9a (#1401) — OSS agent-core composition.
#
# Wires the four concrete OSS read-seams (built by XAI-T3..T6) behind their domain providers,
# builds the deterministic-first IntentRouter (XAI-T2) as the classifier, and returns a
# flag-gated XaiAgentCore (XAI-T1). This is the seam that lets the engine's /chat answer via
# the 4-domain glass box instead of the generic single-prompt chat.
#
# Scope: this composes the OSS (single-tenant desktop) read-seams. Enterprise source-swap
# (Vertex-SHAP explainer, SenateProtocol DB reader + RLS) is a follow-up; the edition is
# still resolved/tracked via boot_xai() so fail-closed messages and a later swap stay correct.
#
# Import-light by construction: no torch, no network, no config read at import time (mirrors
# the agent-core's import-lightness guarantee). The IntentRouter's LLM fallback resolves the
# provider lazily (core/llm/provider.py) only for genuinely ambiguous text.
from __future__ import annotations

from typing import Any, Optional

from core.xai.agent_core import (
    XaiAgentCore,
    XaiProviders,
    XaiRequest,
    XaiResponse,
    boot_xai,
)
from core.xai.intent_router import IntentRouter, LlmProviderFactory
from core.xai.stock_research import (
    RegistrySpecialistReportSource,
    StockResearchProvider,
)
from core.xai.strategy import StrategyProvider
from core.xai.support import SupportProvider
from core.xai.trading_history import JsonlSenateLogReader, TradingHistoryProvider


def build_oss_providers(
    *,
    license_key: Optional[str] = None,
    specialist_registry: Any = None,
    senate_log_dir: Optional[str] = None,
) -> XaiProviders:
    """Register the four OSS domain providers on an edition-aware registry.

    Each provider is wired to its concrete OSS read-seam:
      * ``trading_history`` -> ``JsonlSenateLogReader`` (local senate JSONL audit log)
      * ``strategy``        -> degraded-from-record explanation over the same reader
      * ``stock_research``  -> ``RegistrySpecialistReportSource`` over the engine registry
      * ``support``         -> ``StaticFaqSource`` (FAQ baked in; works with no prior run)

    All four seams fail-safe to a degraded dict on missing/empty data — none crash the chat.

    Args:
        license_key: edition gate (mirrors ``boot_engine``); blank -> OSS.
        specialist_registry: duck-typed ``get_report(symbol)`` registry from the running
            engine. ``None`` -> stock_research degrades to a "no report" message.
        senate_log_dir: override for the senate-log directory; ``None`` lets the reader use
            its own default (``SENATE_LOG_DIR`` env / standard path).
    """
    providers = boot_xai(license_key)
    reader = JsonlSenateLogReader(log_dir=senate_log_dir)
    providers.register("trading_history", TradingHistoryProvider(reader=reader))
    providers.register("strategy", StrategyProvider(reader=reader))
    providers.register(
        "stock_research",
        StockResearchProvider(
            source=RegistrySpecialistReportSource(registry=specialist_registry)
        ),
    )
    providers.register("support", SupportProvider())
    return providers


def boot_xai_runtime(
    *,
    license_key: Optional[str] = None,
    specialist_registry: Any = None,
    senate_log_dir: Optional[str] = None,
    llm_factory: Optional[LlmProviderFactory] = None,
) -> XaiAgentCore:
    """Compose a routable, flag-gated ``XaiAgentCore`` for the engine's /chat path.

    Dormancy is latched at construction (``XAI_AGENT_CORE`` is read once by ``XaiAgentCore``);
    with the flag off (default) the core is inert and the caller falls back to the legacy
    chat. The ``IntentRouter`` is deterministic-first — its LLM fallback resolves lazily via
    the sanctioned ``core/llm/provider.py`` seam (desktop: Ollama opt-in) only for text that
    no high-precision rule matches.
    """
    providers = build_oss_providers(
        license_key=license_key,
        specialist_registry=specialist_registry,
        senate_log_dir=senate_log_dir,
    )
    router = (
        IntentRouter() if llm_factory is None else IntentRouter(llm_factory=llm_factory)
    )
    return XaiAgentCore(providers=providers, classifier=router.classify)


def render_response(resp: XaiResponse) -> str:
    """Render an ``XaiResponse`` into the user-facing reply string.

    Maps the three response shapes (see ``XaiResponse``): a *clarify* (``domain is None``) and
    an *airlock-block* (``domain == "command"``) carry their message in ``.text``; a routed
    *domain answer* carries the provider's dict in ``.payload`` whose ``text`` field is the
    rendered, on-record answer. Never surfaces a raw payload object to the user, and always
    returns a non-empty string (the chat's never-blank guarantee).
    """
    if resp.domain in (None, "command"):
        return resp.text or "Could not process that — please rephrase."
    payload = resp.payload
    if isinstance(payload, dict):
        text = payload.get("text")
        if isinstance(text, str) and text.strip():
            return text
    return "I couldn't find anything for that."


async def answer_via_xai(message: str, *, core: XaiAgentCore) -> Optional[str]:
    """Route a chat message through the XAI glass-box ``core``.

    Returns the rendered reply, or ``None`` when the core is dormant (``XAI_AGENT_CORE`` off)
    so the caller falls back to the legacy chat — with the flag off the core does no work and
    the /chat default path stays byte-identical.
    """
    resp = await core.handle(XaiRequest(text=message))
    if resp.dormant:
        return None
    return render_response(resp)
