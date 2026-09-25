# tests/unit/test_entitlement_agent_gate.py
# GTM-1 (#1800) — Brick-3: boot_engine() filters the active agent set by the resolved
# tier's entitlement ONLY when DEPLOYMENT_MODE=LOCAL. Cloud/Dev/CI keep the full set.
#
# The filter is by CLASS NAME (never a slice) so the agent order/count of a tier is data-
# driven and the DrawdownGuard invariant can be asserted independent of list position.
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.entitlement import crypto as crypto_mod
from core.round_table.agents import ALL_AGENTS
from core.round_table.runner import boot_engine


def _future_iso(days: int = 30) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _active_names():
    import core.round_table.runner as runner

    return [a.__class__.__name__ for a in runner._active_agents]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("DEPLOYMENT_MODE", raising=False)
    monkeypatch.delenv("ENTERPRISE_LICENSE_KEY", raising=False)
    monkeypatch.delenv("AAA_USER_DATA_DIR", raising=False)
    yield


@pytest.fixture()
def test_key(monkeypatch):
    priv = Ed25519PrivateKey.generate()
    monkeypatch.setattr(crypto_mod, "TRUSTED_PUBLIC_KEYS", [priv.public_key()])
    return priv


def _write_license(data_dir, private_key, tier: str):
    token = crypto_mod._encode_token(
        private_key,
        {
            "tier": tier,
            "expires_at": _future_iso(),
            "issued_to": "unit-test",
            "nonce": "n",
        },
    )
    (data_dir / "license.json").write_text(
        json.dumps({"token": token}), encoding="utf-8"
    )


# --------------------------------------------------------------------------- #
# Regression: CI / cloud mode is byte-identical (the required Brick-3 test)
# --------------------------------------------------------------------------- #
def test_boot_engine_ci_mode_unchanged():
    """DEPLOYMENT_MODE unset AND ENTERPRISE_LICENSE_KEY absent -> full ALL_AGENTS set."""
    import core.round_table.runner as runner

    boot_engine(None)
    assert runner._active_agents == ALL_AGENTS


# --------------------------------------------------------------------------- #
# LOCAL gating
# --------------------------------------------------------------------------- #
def test_local_basic_yields_every_round_table_module(monkeypatch, tmp_path):
    """LOCAL with no license -> BASIC. #1877: BASIC carries the FULL module set
    (incl. LSTMSignalAgent + RLConfidenceAgent) so the default desktop can reach
    consensus and paper-trade. The old 3-agent BASIC set excluded LSTM/RL, which made
    the gatekeeper veto every consensus ('Missing core ML votes') -> trade deadlock.

    Asserted as a MIRROR of ALL_AGENTS rather than a magic count: the runner filters
    the active modules by the entitlement list, so a module added to the gremium but
    missing from the tier would be silently mute instead of failing loudly."""
    monkeypatch.setenv("DEPLOYMENT_MODE", "LOCAL")
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))

    boot_engine(None)
    names = set(_active_names())
    assert {"LSTMSignalAgent", "RLConfidenceAgent"} <= names
    assert names == {a.__class__.__name__ for a in ALL_AGENTS}


def test_local_basic_always_contains_drawdown_guard(monkeypatch, tmp_path):
    """Invariant: the DrawdownGuard is NEVER gated away — present in the BASIC set."""
    monkeypatch.setenv("DEPLOYMENT_MODE", "LOCAL")
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))

    boot_engine(None)
    assert "DrawdownGuardAgent" in _active_names()


def test_local_pro_token_activates_every_round_table_module(
    monkeypatch, tmp_path, test_key
):
    """LOCAL with a valid PRO token -> every Round-Table module is active."""
    monkeypatch.setenv("DEPLOYMENT_MODE", "LOCAL")
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))
    _write_license(tmp_path, test_key, "PRO")

    boot_engine(None)
    all_names = {a.__class__.__name__ for a in ALL_AGENTS}
    assert set(_active_names()) == all_names
    assert len(_active_names()) == len(ALL_AGENTS)
