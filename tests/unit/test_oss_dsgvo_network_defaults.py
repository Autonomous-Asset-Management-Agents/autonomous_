"""
Lock-in tests for OSS Tier A DSGVO + network defaults (BORA audit, 2026-04-24).

Pin the Firebase telemetry gate and the loopback frontend binding so accidental
regressions get caught in CI rather than at OSS launch.

Audit references:
  A4 — Firebase telemetry gated behind VITE_ENABLE_FIREBASE
  A9 — Frontend bound to 127.0.0.1 + Caddy/TLS guidance in docs
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def _find_repo_root() -> Path:
    candidate = Path(__file__).resolve().parent
    for _ in range(8):
        if (candidate / ".git").exists() or (
            candidate / "docker-compose.oss.yml"
        ).exists():
            return candidate
        candidate = candidate.parent
    raise RuntimeError(
        f"Could not locate repo root from {Path(__file__)}. "
        "Expected to find .git/ or docker-compose.oss.yml in an ancestor directory."
    )


_root_override = os.environ.get("BORA_REPO_ROOT")
REPO_ROOT = Path(_root_override) if _root_override else _find_repo_root()
FIREBASE_TS = REPO_ROOT / "src" / "lib" / "firebase.ts"
COMPOSE_OSS = REPO_ROOT / "docker-compose.oss.yml"
TROUBLESHOOTING = REPO_ROOT / "docs" / "oss" / "TROUBLESHOOTING.md"
VITE_ENV_DTS = REPO_ROOT / "src" / "vite-env.d.ts"


@pytest.fixture(scope="module")
def firebase_text() -> str:
    return FIREBASE_TS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def compose_text() -> str:
    return COMPOSE_OSS.read_text(encoding="utf-8")


# ── A4: Firebase telemetry gated ───────────────────────────────────────────


def test_a4_firebase_enabled_flag_defined(firebase_text: str) -> None:
    assert (
        "FIREBASE_ENABLED" in firebase_text
    ), "firebase.ts must define a FIREBASE_ENABLED gate driven by VITE_ENABLE_FIREBASE."
    assert (
        "VITE_ENABLE_FIREBASE" in firebase_text
    ), "VITE_ENABLE_FIREBASE env var must be referenced"
    assert (
        '=== "true"' in firebase_text
    ), "Gate must check for the literal string 'true' so undefined → disabled."


def test_a4_app_check_initialization_is_gated(firebase_text: str) -> None:
    # initializeAppCheck must only run inside an `if (FIREBASE_ENABLED)` block
    init_call = firebase_text.find("initializeAppCheck(")
    assert init_call != -1, "initializeAppCheck call missing"
    # The first FIREBASE_ENABLED gate must appear before the call
    gate = firebase_text.find("if (FIREBASE_ENABLED)")
    assert 0 <= gate < init_call, (
        "initializeAppCheck() must sit inside an `if (FIREBASE_ENABLED)` block — it is "
        "the reCAPTCHA-fetching call that leaks page-load network traffic."
    )


def test_a4_remote_config_fetch_is_gated(firebase_text: str) -> None:
    fetch = firebase_text.find("fetchAndActivate(remoteConfig)")
    assert fetch != -1, "fetchAndActivate(remoteConfig) call missing"
    # Walk backward to find the nearest `if (FIREBASE_ENABLED)`
    preceding = firebase_text[:fetch]
    assert (
        "if (FIREBASE_ENABLED)" in preceding
    ), "fetchAndActivate(remoteConfig) must run only when telemetry is opted-in."


def test_a4_analytics_initialization_is_gated(firebase_text: str) -> None:
    ga = firebase_text.find("getAnalytics(app)")
    assert ga != -1, "getAnalytics(app) call missing"
    preceding = firebase_text[:ga]
    assert "if (FIREBASE_ENABLED)" in preceding, (
        "getAnalytics(app) must run only when telemetry is opted-in — it sets up "
        "GA4 beacons and cookies on every page load."
    )


def test_a4_vite_env_dts_documents_flag() -> None:
    text = VITE_ENV_DTS.read_text(encoding="utf-8")
    assert "VITE_ENABLE_FIREBASE" in text, (
        "src/vite-env.d.ts must declare VITE_ENABLE_FIREBASE on ImportMetaEnv "
        "so consumers get type checking for the gate."
    )


# ── A9: Frontend bound to loopback + Caddy/TLS docs ────────────────────────


def test_a9_frontend_bound_to_loopback(compose_text: str) -> None:
    assert (
        '"127.0.0.1:80:8080"' in compose_text or "'127.0.0.1:80:8080'" in compose_text
    ), "Frontend port must bind to 127.0.0.1, not 0.0.0.0"
    # Zusätzlich: kein unscoped bind
    public_binds = [
        line
        for line in compose_text.splitlines()
        if '"80:8080"' in line and "127.0.0.1" not in line
    ]
    assert (
        not public_binds
    ), f"Found unscoped 80:8080 bind in OSS compose: {public_binds}"


def test_a9_troubleshooting_has_tls_guidance() -> None:
    if not TROUBLESHOOTING.exists():
        pytest.fail(f"TROUBLESHOOTING.md not found at {TROUBLESHOOTING}")
    text = TROUBLESHOOTING.read_text(encoding="utf-8")
    assert (
        "Caddy" in text
    ), "TROUBLESHOOTING.md must include a Caddy reverse-proxy example for TLS"
    assert (
        "reverse_proxy 127.0.0.1:80" in text
    ), "TROUBLESHOOTING.md Caddy snippet must proxy from public 443 → loopback 80."
    assert "Let's Encrypt" in text or "letsencrypt" in text.lower(), (
        "TROUBLESHOOTING.md should mention automatic cert provisioning so users don't "
        "default to plain HTTP."
    )
