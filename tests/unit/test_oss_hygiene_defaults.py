"""
Lock-in tests for OSS Tier A hygiene defaults (BORA audit, 2026-04-24).

A7 — `aaagents.devvv` typo domain swept out of source files
A8 — Postgres healthcheck + service_healthy gating for backend startup

These tests scan the working-tree source files (not built dist artifacts),
matching the scope of the corresponding sweep in PR 3/3.
"""

from __future__ import annotations

import os
import typing
from pathlib import Path

import pytest


def _find_repo_root() -> Path:
    """
    Walk up from this file until we find the repo root.
    Checks .git/ (authoritative) and docker-compose.oss.yml (OSS snapshot marker).
    """
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


# Allow BORA_REPO_ROOT env var override (set by oss-ci.yml to ${{ github.workspace }}).
_root_override = os.environ.get("BORA_REPO_ROOT")
REPO_ROOT = Path(_root_override) if _root_override else _find_repo_root()

COMPOSE_OSS = REPO_ROOT / "docker-compose.oss.yml"

# Minified Vite output is regenerated on `npm run build`; not part of the sweep.
# __pycache__ contains compiled bytecode of THIS test file (which mentions the
# typo string in error messages) — also excluded.
SWEEP_EXCLUDE_DIRS = {
    ".git",
    "node_modules",
    ".worktrees",
    "dist",
    "build",
    "__pycache__",
    ".venv",
    "venv",
}
SWEEP_EXCLUDE_PATH_FRAGMENTS = ("static/assets/",)
SWEEP_EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".so", ".pyd", ".dll", ".whl"}
TEST_FILE_NAME = "test_oss_hygiene_defaults.py"


def _iter_source_files() -> typing.Iterator[Path]:
    for root, dirs, files in os.walk(REPO_ROOT):
        # Prune excluded directories in-place so os.walk skips them entirely
        dirs[:] = [
            d for d in dirs if d not in SWEEP_EXCLUDE_DIRS and not d.startswith(".")
        ]

        for file in files:
            p = Path(root) / file
            rel = p.relative_to(REPO_ROOT)

            if any(frag in rel.as_posix() for frag in SWEEP_EXCLUDE_PATH_FRAGMENTS):
                continue
            if p.suffix.lower() in SWEEP_EXCLUDE_SUFFIXES:
                continue
            if p.name == TEST_FILE_NAME:
                continue
            try:
                if p.stat().st_size > 2_000_000:
                    continue
            except OSError:
                continue

            yield p


@pytest.fixture(scope="module")
def compose_text() -> str:
    return COMPOSE_OSS.read_text(encoding="utf-8")


# ── A7: typo sweep ─────────────────────────────────────────────────────────


def test_a7_no_devvv_typo_in_source() -> None:
    """No source file may contain `aaagents.devvv` — that's a typo for `aaagents.de`.

    Why this matters: the triple-v domain is unregistered; anyone could buy it
    for ~10 €/yr and start intercepting disclosure reports, partner inquiries,
    and CI alert emails. Even non-email instances (User-Agent strings, doc
    examples) signal sloppiness to fintech reviewers.
    """
    offenders: list[str] = []
    for f in _iter_source_files():
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except (OSError, UnicodeDecodeError):
            continue
        if "aaagents.devvv" in text:
            offenders.append(str(f.relative_to(REPO_ROOT)))
    assert not offenders, (
        "Found `aaagents.devvv` typo in source files (should be `aaagents.de`):\n  "
        + "\n  ".join(offenders)
    )


# ── A8: Postgres healthcheck + gated startup ───────────────────────────────


def test_a8_postgres_has_healthcheck(compose_text: str) -> None:
    assert "pg_isready" in compose_text, (
        "Postgres service must declare a healthcheck using pg_isready so backend "
        "can wait for `service_healthy` before running alembic. Without it, the "
        'first-run race documented in TROUBLESHOOTING.md ("wait 10s and rerun") '
        "is the standard out-of-the-box experience."
    )


def test_a8_backend_waits_for_postgres_healthy(compose_text: str) -> None:
    # Backend's depends_on must be in map form with condition: service_healthy on postgres
    assert "condition: service_healthy" in compose_text, (
        "backend.depends_on.postgres must use `condition: service_healthy` (map form), "
        "not the bare list form, so alembic doesn't run before Postgres is ready."
    )
    # Sanity: backend block references postgres with the condition
    backend_idx = compose_text.find("aaa-backend-oss")
    assert backend_idx != -1, "backend service block missing"
    backend_block = compose_text[backend_idx : backend_idx + 1500]
    assert (
        "postgres:" in backend_block and "condition: service_healthy" in backend_block
    ), "backend service must depend on postgres with service_healthy condition."


def test_a8_no_legacy_list_form_for_backend_depends_on(compose_text: str) -> None:
    # Make sure we didn't accidentally leave the old list form somewhere
    backend_idx = compose_text.find("aaa-backend-oss")
    backend_block = compose_text[backend_idx : backend_idx + 1500]
    assert "- postgres" not in backend_block, (
        "backend.depends_on still uses the legacy list form. Switch to map form "
        "with explicit `condition:` per service so the healthcheck is honored."
    )
