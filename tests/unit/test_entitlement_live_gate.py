# tests/unit/test_entitlement_live_gate.py
# GTM-1 (#1800) — Brick-4: the signed-tier LIVE gate (Archon §1 CRITICAL fix).
#
# assert_live_trading_config() must FAIL-CLOSED when the resolved tier does not allow
# live trading. Placement matters:
#   * It must run BEFORE the existing DEPLOYMENT_MODE==LOCAL early-return, otherwise the
#     LOCAL branch would skip the entitlement check and a BASIC desktop could go live.
#   * It must NOT fire in paper mode — "BASIC = paper only" means paper trading MUST keep
#     working. So the tier check sits just AFTER the PAPER_TRADING no-op return.
#     (DEVIATION from the literal "before the PAPER_TRADING return" wording in the brief:
#      putting it before PAPER_TRADING would crash the desktop at boot in paper mode,
#      since assert_live_trading_config() is called unconditionally in __main__.py.)
#
# We never mutate config.PAPER_TRADING (bound at import in live_trading_guard.py:26) —
# tests patch the module attribute, exactly like test_live_trading_guard_edition.py.
from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

from core.entitlement.tier import Entitlement, Tier


def _guard():
    from core.engine import live_trading_guard

    return live_trading_guard


_BASIC = Entitlement(
    tier=Tier.BASIC,
    agent_names=("DrawdownGuardAgent",),
    allow_live=False,
    backtest_months=12,
    xai_enabled=False,
    max_order_value=1000.0,
)
_PRO = Entitlement(
    tier=Tier.PRO,
    agent_names=tuple("A" for _ in range(9)),
    allow_live=True,
    backtest_months=None,
    xai_enabled=False,
    max_order_value=10000.0,
)


def test_local_live_basic_tier_downgrades_to_paper():
    """LOCAL + live + BASIC (allow_live=False) → graceful PAPER downgrade, NOT a raise (#1918).

    A hard raise here killed the engine BEFORE uvicorn.run when a live entitlement lapsed after
    the operator had armed live, leaving open real-money positions unmanaged in a boot-loop. The
    tier gate now fail-closes by forcing paper (BASIC still can NEVER trade live — it is degraded
    to paper, which is even safer) so the engine boots and positions stay managed. The
    fail-CLOSED invariant ("BASIC = paper only") is preserved; only the mechanism changed from
    crash to graceful downgrade. Detailed downgrade behaviour: test_live_entitlement_downgrade.py.
    """
    g = _guard()
    with patch.object(g, "PAPER_TRADING", False), patch.object(
        g, "ALPACA_DATA_FEED", "iex"
    ), patch.dict(os.environ, {"DEPLOYMENT_MODE": "LOCAL"}), patch(
        "core.entitlement.resolve_entitlement", return_value=_BASIC
    ), patch(
        "config.force_paper_trading"
    ) as force_paper, patch(
        "core.hitl_gate.log_live_enablement_event", AsyncMock()
    ):
        g.assert_live_trading_config()  # must NOT raise — degrade instead of crash
    force_paper.assert_called_once()


def test_local_live_pro_tier_allowed():
    """LOCAL + live + PRO (allow_live=True) → no raise (LOCAL still skips SIP after the
    tier gate passes)."""
    g = _guard()
    with patch.object(g, "PAPER_TRADING", False), patch.object(
        g, "ALPACA_DATA_FEED", "iex"
    ), patch.dict(os.environ, {"DEPLOYMENT_MODE": "LOCAL"}), patch(
        "core.entitlement.resolve_entitlement", return_value=_PRO
    ):
        g.assert_live_trading_config()  # must NOT raise


def test_paper_mode_never_blocked_by_tier():
    """Paper trading MUST keep working on a BASIC desktop — the tier gate is a no-op in
    paper mode (BASIC = paper only)."""
    g = _guard()
    with patch.object(g, "PAPER_TRADING", True), patch.dict(
        os.environ, {"DEPLOYMENT_MODE": "LOCAL"}
    ), patch("core.entitlement.resolve_entitlement", return_value=_BASIC):
        g.assert_live_trading_config()  # must NOT raise


def test_cloud_live_never_blocked_by_tier():
    """Non-LOCAL deployments resolve to the full bundle (allow_live=True); the tier gate
    never blocks cloud. The existing SIP check still governs cloud live."""
    g = _guard()
    with patch.object(g, "PAPER_TRADING", False), patch.object(
        g, "ALPACA_DATA_FEED", "sip"
    ), patch.dict(os.environ, {"DEPLOYMENT_MODE": "CLOUD_RUN"}):
        g.assert_live_trading_config()  # must NOT raise (real resolve → PROFESSIONAL)


# --------------------------------------------------------------------------- #
# Defense-in-depth: the /api/live/enable endpoint also honours the tier gate.
# --------------------------------------------------------------------------- #
_ENGINE_KEY = "test-engine-key-entitlement"


def _client():
    from fastapi.testclient import TestClient

    import core.engine.api_routes as api_routes

    return TestClient(api_routes.app, raise_server_exceptions=False)


def test_api_live_enable_blocked_for_basic_tier():
    """Defense-in-depth (#1800): /api/live/enable must refuse to arm live for a tier that
    disallows it, independent of the startup guard. Fail-closed → 4xx, no WORM write."""
    with patch.dict(os.environ, {"ENGINE_API_KEY": _ENGINE_KEY}), patch(
        "core.entitlement.resolve_entitlement", return_value=_BASIC
    ):
        client = _client()
        r = client.post(
            "/api/live/enable",
            headers={"X-Engine-Key": _ENGINE_KEY},
            json={"acknowledgment": "x", "nonce": "n-basic"},
        )
        assert r.status_code == 403


def test_api_live_enable_403_for_real_basic_registry_entry():
    """#1877 frozen tier line at the HTTP layer (product decision Georg, 2026-07-08):

    The REAL ``TIER_REGISTRY[Tier.BASIC]`` entry — not the synthetic ``_BASIC``
    fixture above — must be refused by POST /api/live/enable via the tier gate
    (core/engine/api_routes.py:3273): 403, fail-closed, before any WORM write.
    This is the exact paywall path a default no-token desktop (fail-closed
    BASIC) hits; it was previously only covered with a synthetic entitlement.
    If someone flips BASIC.allow_live in the registry, this test goes red at
    the HTTP boundary too, not just in the registry unit tests.
    """
    from core.entitlement.tier import TIER_REGISTRY

    real_basic = TIER_REGISTRY[Tier.BASIC]
    assert real_basic.allow_live is False  # precondition: the frozen Senior paywall
    with patch.dict(os.environ, {"ENGINE_API_KEY": _ENGINE_KEY}), patch(
        "core.entitlement.resolve_entitlement", return_value=real_basic
    ):
        client = _client()
        r = client.post(
            "/api/live/enable",
            headers={"X-Engine-Key": _ENGINE_KEY},
            json={"acknowledgment": "x", "nonce": "n-real-basic"},
        )
        assert r.status_code == 403
        assert "does not allow live" in r.json()["detail"]


def test_api_live_enable_allowed_for_pro_tier(tmp_path):
    """A live-allowing tier passes the entitlement gate (endpoint proceeds to the WORM
    write and returns 201)."""
    with patch.dict(
        os.environ,
        {"ENGINE_API_KEY": _ENGINE_KEY, "SENATE_LOG_DIR": str(tmp_path)},
    ), patch("core.entitlement.resolve_entitlement", return_value=_PRO):
        import core.hitl_gate as hg
        import core.round_table.runner as runner

        old_senate = runner._senate
        runner._senate = None
        hg._fallback_audit_logger = None
        try:
            client = _client()
            r = client.post(
                "/api/live/enable",
                headers={"X-Engine-Key": _ENGINE_KEY},
                json={"acknowledgment": "x", "nonce": "n-pro"},
            )
            assert r.status_code == 201
        finally:
            runner._senate = old_senate
            hg._fallback_audit_logger = None
