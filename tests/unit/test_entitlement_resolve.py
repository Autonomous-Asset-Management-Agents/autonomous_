# tests/unit/test_entitlement_resolve.py
# GTM-1 (#1800) — TIER_REGISTRY + resolve_entitlement() single entry point.
#
# TDD Brick-2. Covers the 5 Story Gherkin scenarios:
#   1. no token (LOCAL)          -> BASIC
#   2. valid PRO token (LOCAL)   -> 9 agents + allow_live
#   3. tampered/expired (LOCAL)  -> BASIC (fail-closed)
#   4. non-LOCAL deployment      -> FULL set (cloud/dev/CI unchanged)
#   5. registry maps tier->features
from __future__ import annotations

import json
from dataclasses import fields
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.entitlement import Entitlement, Tier
from core.entitlement import crypto as crypto_mod
from core.entitlement import resolve_entitlement
from core.entitlement.tier import TIER_REGISTRY
from core.round_table.agents import ALL_AGENTS

ALL_9_NAMES = tuple(a.__class__.__name__ for a in ALL_AGENTS)


def _future_iso(days: int = 30) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _past_iso(days: int = 1) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


@pytest.fixture()
def local_env(monkeypatch, tmp_path):
    """DEPLOYMENT_MODE=LOCAL with an isolated AAA_USER_DATA_DIR."""
    monkeypatch.setenv("DEPLOYMENT_MODE", "LOCAL")
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture()
def test_key(monkeypatch):
    priv = Ed25519PrivateKey.generate()
    monkeypatch.setattr(crypto_mod, "TRUSTED_PUBLIC_KEYS", [priv.public_key()])
    return priv


def _write_license(data_dir: Path, private_key, payload: dict) -> None:
    token = crypto_mod._encode_token(private_key, payload)  # noqa: SLF001
    (data_dir / "license.json").write_text(
        json.dumps({"token": token}), encoding="utf-8"
    )


# --------------------------------------------------------------------------- #
# Registry shape (Gherkin #5)
# --------------------------------------------------------------------------- #
def test_registry_covers_all_four_tiers():
    assert set(TIER_REGISTRY.keys()) == set(Tier)


def test_registry_entries_are_frozen_entitlements():
    for tier, ent in TIER_REGISTRY.items():
        assert isinstance(ent, Entitlement)
        assert ent.tier is tier


def test_basic_tier_features():
    # #1877: Junior (BASIC) is content-identical to PRO except allow_live (paper-only).
    ent = TIER_REGISTRY[Tier.BASIC]
    assert ent.allow_live is False  # paper-only; Live is the Senior paywall
    assert ent.backtest_months is None
    assert ent.xai_enabled is False
    assert ent.max_order_value == 10000
    assert set(ent.agent_names) == set(ALL_9_NAMES)


def test_pro_tier_has_all_nine_agents_and_live():
    ent = TIER_REGISTRY[Tier.PRO]
    assert set(ent.agent_names) == set(ALL_9_NAMES)
    assert ent.allow_live is True
    assert ent.backtest_months is None
    assert ent.xai_enabled is False
    assert ent.max_order_value == 10000


def test_professional_tier_enables_xai():
    ent = TIER_REGISTRY[Tier.PROFESSIONAL]
    assert set(ent.agent_names) == set(ALL_9_NAMES)
    assert ent.allow_live is True
    assert ent.xai_enabled is True
    assert ent.max_order_value == 50000


def test_institutional_tier_unlimited_order_value():
    ent = TIER_REGISTRY[Tier.INSTITUTIONAL]
    assert set(ent.agent_names) == set(ALL_9_NAMES)
    assert ent.allow_live is True
    assert ent.xai_enabled is True
    assert ent.max_order_value is None


# --------------------------------------------------------------------------- #
# Simulation feature gate — product decision: the desktop Simulation/backtest page
# is disabled for EVERY tier (incl. cloud/Enterprise, which resolves to
# PROFESSIONAL) until the backtest runtime is hardened (upfront data load has no
# network timeout -> can hang; the page has no progress/cancel affordance). One
# central switch in TIER_REGISTRY drives desktop AND cloud.
# --------------------------------------------------------------------------- #
def test_every_tier_disables_simulation():
    for tier, ent in TIER_REGISTRY.items():
        assert (
            ent.simulation_enabled is False
        ), f"{tier} must ship with the Simulation page disabled"


def test_resolved_entitlement_simulation_off_non_local(monkeypatch):
    # Non-LOCAL (cloud/dev/CI/Enterprise) resolves to PROFESSIONAL -> simulation off too.
    monkeypatch.setenv("DEPLOYMENT_MODE", "CLOUD")
    assert resolve_entitlement().simulation_enabled is False


# --------------------------------------------------------------------------- #
# #1877 — BASIC-trade-deadlock regression guard.
# The ComplianceGatekeeper (core/round_table/runner.py:438-454) hard-vetoes any
# consensus that lacks a valid LSTMSignalAgent AND RLConfidenceAgent vote
# ("Missing core ML votes"). Every tier runs the Round Table, so every tier MUST
# carry both agents -- otherwise that tier can never produce a trade. BASIC used to
# exclude them, so the default desktop (fail-closed BASIC, no token) never traded.
# --------------------------------------------------------------------------- #
_GATEKEEPER_REQUIRED_CORE_ML = frozenset({"LSTMSignalAgent", "RLConfidenceAgent"})


def test_every_tier_carries_gatekeeper_required_core_ml_agents():
    for tier, ent in TIER_REGISTRY.items():
        missing = _GATEKEEPER_REQUIRED_CORE_ML - set(ent.agent_names)
        assert not missing, (
            f"{tier.name} excludes {sorted(missing)} -> the ComplianceGatekeeper "
            f"vetoes every consensus ('Missing core ML votes'); this tier can never "
            f"trade. Regression of #1877."
        )


def test_basic_junior_content_equals_pro_senior():
    """#1877 FROZEN TIER LINE (product decision Georg, 2026-07-08):

    Junior (BASIC/Free) and Senior (PRO) are content-identical in EVERY
    Entitlement field EXCEPT ``allow_live`` — Live (real-capital) trading stays
    the Senior paywall (BASIC=False, PRO=True). The proposed allow_live flip
    for BASIC was REJECTED; the Grant register (#1914) is the free-live path.
    The allow_live axis and the Ed25519 register mechanics (#1800) stay in
    place unchanged, so any future change to this line is a deliberate registry
    edit that must consciously touch this test.

    Field-complete by construction: iterates dataclasses.fields(Entitlement),
    so any NEW Entitlement field automatically falls under the invariant.
    """
    basic = TIER_REGISTRY[Tier.BASIC]
    pro = TIER_REGISTRY[Tier.PRO]
    assert basic.allow_live is False  # paper-only; Live is the Senior paywall
    assert pro.allow_live is True
    for f in fields(Entitlement):
        if f.name in ("tier", "allow_live"):
            continue  # tier label differs by definition; allow_live IS the paywall axis
        assert getattr(basic, f.name) == getattr(pro, f.name), (
            f"#1877 frozen tier line violated: BASIC.{f.name}="
            f"{getattr(basic, f.name)!r} != PRO.{f.name}={getattr(pro, f.name)!r} "
            f"— Junior must equal Senior in every field except allow_live."
        )


# --------------------------------------------------------------------------- #
# resolve_entitlement — non-LOCAL (Gherkin #4)
# --------------------------------------------------------------------------- #
def test_non_local_returns_full_entitlement(monkeypatch):
    monkeypatch.delenv("DEPLOYMENT_MODE", raising=False)
    ent = resolve_entitlement()
    assert set(ent.agent_names) == set(ALL_9_NAMES)
    assert ent.allow_live is True


def test_cloud_deployment_mode_returns_full_entitlement(monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_MODE", "CLOUD")
    ent = resolve_entitlement()
    assert set(ent.agent_names) == set(ALL_9_NAMES)
    assert ent.allow_live is True


# --------------------------------------------------------------------------- #
# resolve_entitlement — LOCAL (Gherkin #1, #2, #3)
# --------------------------------------------------------------------------- #
def test_local_no_license_returns_basic(local_env):
    # No license.json present at all — fail-closed to BASIC (paper-only).
    ent = resolve_entitlement()
    assert ent.tier is Tier.BASIC
    assert ent.allow_live is False


def test_no_token_desktop_can_produce_trade_signal(local_env):
    """#1877 acceptance — Gherkin:
    Given a LOCAL desktop without any license token (fail-closed BASIC)
    When the trading cycle resolves its entitlement
    Then a (paper) trade signal is possible: the entitlement carries the
         gatekeeper-required LSTM+RL core ML agents, so the ComplianceGatekeeper
         (core/round_table/runner.py:438-454) cannot veto every consensus with
         'Missing core ML votes' — the BASIC trade deadlock fixed in 81231ee
         stays fixed — while Live stays behind the Senior paywall.
    """
    ent = resolve_entitlement()
    assert ent.tier is Tier.BASIC
    assert _GATEKEEPER_REQUIRED_CORE_ML <= set(ent.agent_names)
    assert set(ent.agent_names) == set(ALL_9_NAMES)
    assert ent.allow_live is False  # signal path is PAPER; Live = Senior paywall


def test_local_valid_pro_token_returns_pro(local_env, test_key):
    _write_license(
        local_env,
        test_key,
        {
            "tier": "PRO",
            "expires_at": _future_iso(),
            "issued_to": "unit-test",
            "nonce": "n",
        },
    )
    ent = resolve_entitlement()
    assert ent.tier is Tier.PRO
    assert ent.allow_live is True
    assert set(ent.agent_names) == set(ALL_9_NAMES)


def test_local_expired_token_returns_basic(local_env, test_key):
    _write_license(
        local_env,
        test_key,
        {
            "tier": "PRO",
            "expires_at": _past_iso(),
            "issued_to": "unit-test",
            "nonce": "n",
        },
    )
    ent = resolve_entitlement()
    assert ent.tier is Tier.BASIC


def test_local_tampered_license_returns_basic(local_env, test_key):
    # Write a syntactically valid but garbage token.
    (local_env / "license.json").write_text(
        json.dumps({"token": "dGFtcGVyZWQ="}), encoding="utf-8"
    )
    ent = resolve_entitlement()
    assert ent.tier is Tier.BASIC


def test_local_malformed_license_file_returns_basic(local_env):
    (local_env / "license.json").write_text("{not json", encoding="utf-8")
    ent = resolve_entitlement()
    assert ent.tier is Tier.BASIC


def test_shipped_beta_license_resolves_to_pro(local_env):
    """Ensure that the actual shipped `desktop/resources/beta_senior_license.json`
    verifies against the fallback `TRUSTED_PUBLIC_KEYS` and successfully
    resolves to PRO.
    """
    test_file_dir = Path(__file__).parent
    repo_root = test_file_dir.parents[2]
    shipped_license_path = (
        repo_root / "desktop" / "resources" / "beta_senior_license.json"
    )

    assert (
        shipped_license_path.exists()
    ), f"Shipped beta license file does not exist at {shipped_license_path}"

    dest_license = local_env / "license.json"
    dest_license.write_text(
        shipped_license_path.read_text(encoding="utf-8"), encoding="utf-8"
    )

    ent = resolve_entitlement()
    assert ent.tier is Tier.PRO
    assert ent.allow_live is True
    assert len(ent.agent_names) == 9
