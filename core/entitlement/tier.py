# core/entitlement/tier.py
# GTM-1 (#1800) — the central signed Tier-Entitlement table.
#
# A Tier maps to an immutable Entitlement (the feature bundle a licensee is allowed).
# resolve_entitlement() (core/entitlement/__init__.py) is the single runtime entry point;
# this module only defines the Tier enum, the frozen Entitlement dataclass, and the
# central Tier -> features registry.
#
# SCOPE: gating is desktop-only (DEPLOYMENT_MODE=LOCAL). Cloud/Dev/CI are unchanged.
# NEVER gate Iron Dome / risk / kill-switch here — those are unconditional.
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Tier(Enum):
    """Product tiers, ascending capability. Values are the canonical token strings."""

    BASIC = "BASIC"
    PRO = "PRO"
    PROFESSIONAL = "PROFESSIONAL"
    INSTITUTIONAL = "INSTITUTIONAL"


@dataclass(frozen=True)
class Entitlement:
    """The immutable feature bundle a resolved tier grants.

    Attributes:
        tier: the resolved Tier.
        agent_names: Round-Table agent CLASS names this tier may run (filter by NAME).
        allow_live: may this tier arm live (real-capital) trading? (BASIC = paper only).
        backtest_months: max backtest look-back in months; None = unlimited.
        xai_enabled: is the XAI explainability agent-core allowed?
        simulation_enabled: is the desktop Simulation/backtest page exposed? Currently
            False for EVERY tier (incl. cloud/Enterprise, which resolves to PROFESSIONAL)
            while the backtest runtime is hardened (upfront data load has no network
            timeout -> can hang; no progress/cancel affordance). Flip per tier to re-ship.
        max_order_value: per-order EUR cap; None = no entitlement-imposed cap.
        expires_at: ISO-8601 expiry carried through for observability; None = n/a.
    """

    tier: Tier
    agent_names: tuple[str, ...]
    allow_live: bool
    backtest_months: Optional[int]
    xai_enabled: bool
    max_order_value: Optional[float]
    # Fail-closed default: any constructor that omits this ships the Simulation page
    # OFF. The registry sets it explicitly per tier for self-documentation.
    simulation_enabled: bool = False
    expires_at: Optional[str] = None


# The exact 9 Round-Table agent class names (source of truth: core/round_table/agents.py
# ALL_AGENTS, ~L862). Hard-coded here to keep the registry a pure, import-cheap data table
# and to avoid instantiating the (heavy) agent objects just to read their names.
_ALL_9_AGENTS: tuple[str, ...] = (
    "DrawdownGuardAgent",
    "SpecialistAlphaAgent",
    "RegimeDetectionAgent",
    "MomentumAgent",
    "VIXAwareRiskAgent",
    "LSTMSignalAgent",
    "RLConfidenceAgent",
    "NewsSentimentAgent",
    "PatternRecognitionAgent",
)

# ---------------------------------------------------------------------------
# VORSCHLAG — Compliance/Produkt zu bestaetigen (GTM-1 #1800)
# ---------------------------------------------------------------------------
# The Tier -> features table below is a PROPOSAL. Concrete agent bundles, live-trading
# permission, backtest windows, XAI access, and per-order caps MUST be confirmed by
# Compliance/Product before this ships. Values mirror the Archon-approved v2 design.
# ---------------------------------------------------------------------------
TIER_REGISTRY: dict[Tier, Entitlement] = {
    # Junior (Free), #1877: content-identical to PRO/Senior EXCEPT allow_live. It runs
    # the same Round Table, so it must carry the gatekeeper-required LSTM+RL agents,
    # else "Missing core ML votes" vetoes every consensus (the default desktop, which
    # fail-closes to BASIC without a token, could never trade).
    Tier.BASIC: Entitlement(
        tier=Tier.BASIC,
        agent_names=_ALL_9_AGENTS,
        allow_live=False,  # paper-only; Live is the PRO/Senior paywall
        backtest_months=None,
        xai_enabled=False,
        simulation_enabled=False,  # Simulation page shipped disabled (see Entitlement docstring)
        # ADR-C01: 10,000 EUR max order value (ESMA MiFID II Art. 57 default).
        max_order_value=10000.0,
    ),
    Tier.PRO: Entitlement(
        tier=Tier.PRO,
        agent_names=_ALL_9_AGENTS,
        allow_live=True,
        backtest_months=None,
        xai_enabled=False,
        simulation_enabled=False,
        max_order_value=10000.0,
    ),
    Tier.PROFESSIONAL: Entitlement(
        tier=Tier.PROFESSIONAL,
        agent_names=_ALL_9_AGENTS,
        allow_live=True,
        backtest_months=None,
        xai_enabled=True,
        simulation_enabled=False,
        max_order_value=50000.0,
    ),
    Tier.INSTITUTIONAL: Entitlement(
        tier=Tier.INSTITUTIONAL,
        agent_names=_ALL_9_AGENTS,
        allow_live=True,
        backtest_months=None,
        xai_enabled=True,
        simulation_enabled=False,
        max_order_value=None,
    ),
}
