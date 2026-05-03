"""
Lock-in tests for OSS Tier A security defaults (BORA audit, 2026-04-24).

These tests pin the safe defaults of the OSS container so accidental regressions
(e.g. someone re-adding `ALLOW_UNTRUSTED_PLUGINS: "true"`, or re-exposing Postgres
on 0.0.0.0) get caught in CI rather than at launch.

Audit references:
  A1 — Plugin loading deny-by-default in compose
  A2 — Postgres bound to loopback + password required via env
  A3 — Auth-safe nginx config baked into the frontend image

Path strategy:
  Use _find_repo_root() instead of hard-coded parents[N] so these tests work
  identically in the Dev-Env working tree AND in the flat OSS snapshot
  (~/aaagents-public/), where the directory depth differs.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def _find_repo_root() -> Path:
    """
    Walk up from this file until we find a directory that looks like the repo root.

    Markers checked (in order):
      1. .git/        — authoritative for Git repos (Dev-Env and cloned OSS repos)
      2. docker-compose.oss.yml — present in both Dev-Env and OSS snapshot root

    Using both markers avoids false matches in Monorepo setups where an ancestor
    might contain docker-compose.oss.yml without being our repo root.
    """
    candidate = Path(__file__).resolve().parent
    for _ in range(8):  # guard against infinite loop on unexpected layouts
        if (candidate / ".git").exists() or (
            candidate / "docker-compose.oss.yml"
        ).exists():
            return candidate
        candidate = candidate.parent
    raise RuntimeError(
        f"Could not locate repo root from {Path(__file__)}. "
        "Expected to find .git/ or docker-compose.oss.yml in an ancestor directory."
    )


# Allow BORA_REPO_ROOT env var override (set by oss-ci.yml to ${{ github.workspace }}).
_root_override = os.environ.get("BORA_REPO_ROOT")
REPO_ROOT = Path(_root_override) if _root_override else _find_repo_root()

COMPOSE_OSS = REPO_ROOT / "docker-compose.oss.yml"
DOCKERFILE_FRONTEND = REPO_ROOT / "Dockerfile.frontend"
ENV_OSS_EXAMPLE = REPO_ROOT / ".env.oss.example"


@pytest.fixture(scope="module")
def compose_text() -> str:
    return COMPOSE_OSS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def dockerfile_text() -> str:
    if not DOCKERFILE_FRONTEND.exists():
        pytest.fail(
            f"Dockerfile.frontend missing from snapshot root ({DOCKERFILE_FRONTEND}). "
            'Add to oss_make_snapshot.sh Step 2: cp "$REPO_ROOT/Dockerfile.frontend" "$PUBLIC_DIR_NEW/". '
            "This is a Security Gate (A3) — skipping is not acceptable."
        )
    return DOCKERFILE_FRONTEND.read_text(encoding="utf-8")


# ── A1: Plugin loading must not be force-enabled in compose ────────────────


def test_a1_allow_untrusted_plugins_not_hardcoded_true(compose_text: str) -> None:
    assert 'ALLOW_UNTRUSTED_PLUGINS: "true"' not in compose_text, (
        "Compose must not ship with ALLOW_UNTRUSTED_PLUGINS hard-set to true. "
        "Use ${ALLOW_UNTRUSTED_PLUGINS:-false} so plugin loading is opt-in via .env.oss."
    )


# ── A2: Postgres bound to loopback + password required ─────────────────────


def test_a2_postgres_bound_to_loopback(compose_text: str) -> None:
    assert '"127.0.0.1:5432:5432"' in compose_text, (
        "Postgres must bind to 127.0.0.1, not 0.0.0.0. Exposing 5432 publicly "
        "with hardcoded oss/oss credentials is the BORA audit B2 finding."
    )
    assert '"5432:5432"' not in compose_text.replace(
        '"127.0.0.1:5432:5432"', ""
    ), "Found a non-loopback 5432 binding."


def test_a2_postgres_password_required(compose_text: str) -> None:
    # Refuse-to-start syntax: ${VAR:?message}
    assert "POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?" in compose_text, (
        "POSTGRES_PASSWORD must use the ${VAR:?...} syntax so compose refuses "
        "to start without an explicit password. Hardcoded passwords ship oss/oss "
        "credentials to the public internet."
    )
    assert (
        "POSTGRES_PASSWORD: oss" not in compose_text
    ), "Hardcoded POSTGRES_PASSWORD: oss must not be present."


def test_a2_env_oss_example_exists_and_has_required_marker() -> None:
    assert (
        ENV_OSS_EXAMPLE.is_file()
    ), ".env.oss.example must exist as the canonical template"
    text = ENV_OSS_EXAMPLE.read_text(encoding="utf-8")
    assert (
        "POSTGRES_PASSWORD=" in text
    ), ".env.oss.example must declare POSTGRES_PASSWORD"
    assert "REQUIRED" in text.upper(), ".env.oss.example must mark required values"


# ── A3: Auth-safe nginx baked into frontend image, no override mount ───────


def test_a3_dockerfile_bakes_oss_nginx(dockerfile_text: str) -> None:
    assert "nginx.oss.conf" in dockerfile_text, (
        "Dockerfile.frontend must COPY nginx.oss.conf as default config. "
        "Baking the auth-safe config closes the volume-mount-failure auth-bypass path."
    )
    assert "COPY --chown=nginx:nginx nginx.conf " not in dockerfile_text, (
        "Dockerfile.frontend must not bake the private nginx.conf — it routes /api/ "
        "direct to backend:8001, bypassing the public-api auth layer."
    )


def test_a3_oss_compose_has_no_nginx_override_mount(compose_text: str) -> None:
    # nginx.oss.conf ist im Image gebacken — kein Runtime-Mount erlaubt.
    assert "nginx.oss.conf:/etc/nginx/conf.d/default.conf" not in compose_text, (
        "OSS compose must not mount nginx.oss.conf — baked into the image. "
        "A runtime mount re-introduces the auth-bypass risk."
    )
