# core/xai/agent_core.py
# XAI-1 / XAI-T1 (#1330) — Agent-Core skeleton.
#   * Edition-gated DI mirrors boot_engine (core/round_table/runner.py:144).
#   * Flag-gated-dormant: the gate is LATCHED at construction (read once at boot, like
#     boot_engine resolves edition once) so a dormant core can't be toggled live by a
#     later env mutation. Default OFF => byte-identical no-op.
#   * UI-agnostic: request -> intent -> provider -> response. The intent classifier
#     (XAI-T2) is injected (sync OR async); concrete providers (XAI-T3..T6) are
#     registered and fail-closed if missing.
# The chat LLM is NOT bound here — consumers resolve it via core/llm/provider.py
# get_llm_provider() (desktop: Ollama opt-in / Gemini; cloud: always Gemini).
from __future__ import annotations

import enum
import inspect
import logging
import os
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, Union

from core.entitlement import resolve_entitlement
from core.xai.airlock import CommandAirlock
from core.xai.interfaces import IDomainProvider

logger = logging.getLogger(__name__)

# Domain -> data read-seam (interfaces.py): stock_research->ISpecialistReportSource,
# trading_history->ISenateLogReader, strategy->IExplainabilitySource, support->IFaqSource.
DOMAINS = ("stock_research", "trading_history", "strategy", "support")

_TRUTHY = {"1", "true", "yes", "on"}

# Classifier seam (XAI-T2): free text -> a DOMAINS key or None. May be sync OR async.
Classifier = Callable[[str], Union[Optional[str], Awaitable[Optional[str]]]]


class Edition(enum.Enum):
    OSS = "oss"
    ENTERPRISE = "enterprise"


class XaiProviderUnavailable(RuntimeError):
    """Raised (fail-closed) when a routed domain has no provider configured."""


def resolve_edition(license_key: Optional[str]) -> Edition:
    """ENTERPRISE iff a non-blank license key is present; else OSS.

    Mirrors the boot_engine() license gate (core/round_table/runner.py:144). Full license
    *validation* lives outside this seam — this only refuses blank/whitespace keys so an
    empty value can never resolve to ENTERPRISE (entitlement fail-closed).
    """
    return Edition.ENTERPRISE if (license_key and license_key.strip()) else Edition.OSS


def is_agent_core_enabled() -> bool:
    """Flag-gated-dormant: the agent-core is inert unless XAI_AGENT_CORE is truthy.
    Read once at construction (latched) — see XaiAgentCore.__init__."""
    return os.getenv("XAI_AGENT_CORE", "").strip().lower() in _TRUTHY


@dataclass
class XaiRequest:
    text: str
    session_user_id: Optional[str] = None


@dataclass
class XaiResponse:
    """One of three shapes, discriminated by ``dormant`` + ``domain``:

    * ``dormant=True``                  -> core is gated off; no work was done.
    * ``dormant=False, domain=None``    -> intent unresolved; ``text`` is the clarify msg.
    * ``dormant=False, domain=<d>``     -> routed answer; ``payload`` is the provider's
      result. ``payload`` MAY be a legitimately-empty value ("no data") — the provider
      owns that, the router does not coerce empty into an error.
    """

    dormant: bool = False
    domain: Optional[str] = None
    payload: Any = None
    text: Optional[str] = None


class XaiProviders:
    """Edition-aware registry of domain providers. ``require`` fails closed.

    GTM-1 (#1800) Brick-6: ``enabled`` gates the whole registry on the tier's
    ``xai_enabled``. When disabled (a LOCAL tier without XAI), ``register`` is a no-op and
    ``require`` fails closed — no XAI provider is ever reachable (fail-closed OFF).
    """

    def __init__(self, edition: Edition, enabled: bool = True) -> None:
        self.edition = edition
        self.enabled = enabled
        self._by_domain: dict[str, IDomainProvider] = {}

    def register(self, domain: str, provider: IDomainProvider) -> None:
        if domain not in DOMAINS:
            raise ValueError(f"unknown XAI domain: {domain!r}")
        if not isinstance(provider, IDomainProvider):
            raise TypeError(
                f"provider for {domain!r} must implement IDomainProvider, "
                f"got {type(provider).__name__}"
            )
        if not self.enabled:
            # Tier disallows XAI — refuse to register so nothing becomes reachable.
            logger.info(
                "[Entitlement] XAI disabled by tier — ignoring provider for %r.", domain
            )
            return
        self._by_domain[domain] = provider

    def get(self, domain: str) -> Optional[IDomainProvider]:
        """Nullable accessor (returns None if unset). Use ``require`` to fail closed."""
        return self._by_domain.get(domain)

    def require(self, domain: str) -> IDomainProvider:
        provider = self._by_domain.get(domain)
        if provider is None:
            raise XaiProviderUnavailable(
                f"XAI domain {domain!r} has no provider configured "
                f"(edition={self.edition.value}, enabled={self.enabled}); fail-closed."
            )
        return provider


def boot_xai(license_key: Optional[str] = None) -> XaiProviders:
    """DI factory (mirrors boot_engine): build the edition-aware provider registry.
    Concrete domain providers are registered by XAI-T3..T6; here it starts empty.

    GTM-1 (#1800) Brick-6: on the LOCAL desktop the registry is additionally gated on the
    tier's ``xai_enabled`` (fail-closed OFF if the tier disallows XAI). Non-LOCAL
    deployments resolve to the full bundle (xai_enabled=True) → unchanged behaviour.
    """
    xai_enabled = True
    if os.getenv("DEPLOYMENT_MODE", "").upper() == "LOCAL":
        xai_enabled = resolve_entitlement().xai_enabled
    return XaiProviders(resolve_edition(license_key), enabled=xai_enabled)


class XaiAgentCore:
    """UI-agnostic agent core: request -> intent -> provider -> response.

    Dormancy is **latched at construction** (XAI_AGENT_CORE read once at boot) so a
    constructed dormant core cannot be woken live by a later env mutation — re-boot to
    change the gate. The intent classifier (XAI-T2) may be sync or async.
    """

    def __init__(
        self,
        *,
        providers: XaiProviders,
        classifier: Classifier,
        airlock: Optional[CommandAirlock] = None,
    ) -> None:
        self._providers = providers
        self._classify = classifier
        self._airlock = airlock or CommandAirlock()
        self._enabled = is_agent_core_enabled()  # latched once, at boot

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def handle(self, request: XaiRequest) -> XaiResponse:
        if not self._enabled:
            # Flag-gated-dormant: neither classify nor route.
            return XaiResponse(dormant=True)

        # Command-Airlock (XAI-T7): screen for actionable intents BEFORE routing. A command
        # never reaches a read provider — it is blocked (PLT-3 fail-closed) or drafted for
        # MFA, but NEVER executed.
        decision = self._airlock.screen(request.text)
        if decision.kind != "allow":
            return XaiResponse(
                dormant=False,
                domain="command",
                text=decision.message,
                payload=decision,
            )

        raw = self._classify(request.text)
        if inspect.isawaitable(raw):
            raw = await raw
        domain = raw

        if domain not in DOMAINS:
            # Fail-safe: an unknown/None intent never crashes; ask the user to clarify.
            # WARNING (not silent) so a misbehaving classifier is observable.
            logger.warning(
                "XAI intent unresolved (classifier returned %r) — returning clarify.",
                domain,
            )
            return XaiResponse(
                dormant=False,
                domain=None,
                text="Could not determine intent — please rephrase.",
            )

        provider = self._providers.require(domain)  # fail-closed if unconfigured
        # Pure pass-through: the provider owns its content, including a legitimately
        # empty result ("no decisions found"). The router does NOT coerce empty -> error.
        payload = await provider.answer(request)
        return XaiResponse(dormant=False, domain=domain, payload=payload)
