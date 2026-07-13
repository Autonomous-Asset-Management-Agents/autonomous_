# tests/unit/test_entitlement_xai_gate.py
# GTM-1 (#1800) — Brick-6: gate the XAI agent-core on the tier's xai_enabled, in ADDITION
# to the existing edition license. Fail-closed OFF when the tier disallows XAI (LOCAL).
# Cloud/Dev/CI resolve to the full bundle (xai_enabled=True) → unchanged behaviour.
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from core.entitlement.tier import Entitlement, Tier
from core.xai.agent_core import Edition, XaiProviderUnavailable, boot_xai
from core.xai.interfaces import IDomainProvider


class _StubProvider(IDomainProvider):
    async def answer(self, request):  # pragma: no cover - trivial stub
        return {"ok": True}


def _ent(xai_enabled):
    return Entitlement(
        tier=Tier.BASIC if not xai_enabled else Tier.PROFESSIONAL,
        agent_names=("DrawdownGuardAgent",),
        allow_live=False,
        backtest_months=12,
        xai_enabled=xai_enabled,
        max_order_value=1000.0,
    )


def test_non_local_unaffected(monkeypatch):
    """Cloud/Dev/CI: boot_xai is unchanged — a registered provider is retrievable."""
    monkeypatch.delenv("DEPLOYMENT_MODE", raising=False)
    providers = boot_xai("ent-key")
    assert providers.edition is Edition.ENTERPRISE
    providers.register("support", _StubProvider())
    assert providers.require("support") is not None


def test_local_xai_disabled_fails_closed(monkeypatch):
    """LOCAL + tier xai_enabled=False → the registry is inert: register is a no-op and
    require fails closed, so no XAI provider is ever reachable."""
    monkeypatch.setenv("DEPLOYMENT_MODE", "LOCAL")
    with patch("core.xai.agent_core.resolve_entitlement", return_value=_ent(False)):
        providers = boot_xai(None)
        providers.register("support", _StubProvider())  # silently ignored
        with pytest.raises(XaiProviderUnavailable):
            providers.require("support")


def test_local_xai_enabled_allows_providers(monkeypatch):
    """LOCAL + tier xai_enabled=True → registry behaves normally."""
    monkeypatch.setenv("DEPLOYMENT_MODE", "LOCAL")
    with patch("core.xai.agent_core.resolve_entitlement", return_value=_ent(True)):
        providers = boot_xai("ent-key")
        providers.register("support", _StubProvider())
        assert providers.require("support") is not None
