# tests/unit/test_invariants_matrix.py
# OSS-3 (#1050 / #1283): CI-enforced regression matrix for the Codebase Invariants
# (docs/prompts/CODEBASE_INVARIANTS_REPORT.md). Pure SOURCE-level assertions — no
# engine import, no network, no DB — so it runs fast in CI and FAILS the PR the
# moment a desktop-critical invariant regresses. Each check cites its INV id.
#
# Scope: the source-checkable contracts + the report's grep-gates + a guard-existence
# net (the existing per-INV behavioural tests must not be silently deleted). The
# behavioural depth for each INV stays in its own test (referenced below); this file
# is the single aggregating gate the epic asked for ("Invariants matrix green on
# every G-PR", Epic #1050 §3).
from __future__ import annotations

import re
from pathlib import Path

import pytest

# This file lives at <repo>/ai_trading_bot/tests/unit/test_invariants_matrix.py
AI = Path(__file__).resolve().parents[2]  # ai_trading_bot/


def _read(rel: str) -> str:
    return (AI / rel).read_text(encoding="utf-8")


# ── Positive source contracts: (label, relative path, regex that MUST match) ──
_CONTRACTS = [
    (
        "INV-03 engine host default = loopback (127.0.0.1, not 0.0.0.0)",
        "core/engine/__main__.py",
        r"""os\.environ\.get\(\s*["']ENGINE_HOST["']\s*,\s*["']127\.0\.0\.1["']""",
    ),
    (
        "INV-05 keychain SERVICE_NAME == 'aaagents'",
        "core/keychain.py",
        r"""SERVICE_NAME\s*=\s*["']aaagents["']""",
    ),
    (
        "INV-12 SHADOW_MODE fail-safe (getattr(config,'SHADOW_MODE',False))",
        "core/engine/order_executor.py",
        r"""getattr\(\s*config\s*,\s*["']SHADOW_MODE["']\s*,\s*False\s*\)""",
    ),
    (
        "INV-07 AUTO_START_STRATEGY off by default (getattr(...,False))",
        "core/engine/base.py",
        r"""getattr\(\s*config\s*,\s*["']AUTO_START_STRATEGY["']\s*,\s*False\s*\)""",
    ),
    (
        "INV-26 SQLite WAL journal mode pragma",
        "core/database/session.py",
        r"PRAGMA\s+journal_mode\s*=\s*WAL",
    ),
    (
        "INV-26 SQLite foreign_keys pragma",
        "core/database/session.py",
        r"PRAGMA\s+foreign_keys\s*=\s*ON",
    ),
    (
        "INV-27 JSON_TYPE dual-dialect (JSON().with_variant(JSONB,'postgresql'))",
        "core/database/models.py",
        r"with_variant\(\s*JSONB",
    ),
]


@pytest.mark.parametrize(
    "label,rel,pattern", _CONTRACTS, ids=[c[0].split()[0] for c in _CONTRACTS]
)
def test_source_contract_present(label, rel, pattern):
    assert re.search(
        pattern, _read(rel)
    ), f"{label}: invariant regressed in {rel} — pattern not found ({pattern!r})"


# ── INV-17: Iron Dome is a DISTRIBUTED architecture — all 4 components present ──
@pytest.mark.parametrize(
    "rel", ["core/compliance.py", "core/risk_manager.py", "core/kill_switch.py"]
)
def test_iron_dome_component_present(rel):
    assert (AI / rel).is_file(), f"INV-17: Iron Dome component missing: {rel}"


def test_iron_dome_gatekeeper_present():
    assert list(
        AI.glob("core/**/gatekeeper.py")
    ), "INV-17: ComplianceGatekeeper (gatekeeper.py) not found anywhere under core/"


# ── Negative grep-gates (verbatim from the report's Regressions-Test-Matrix) ──
def test_inv03_no_bare_0_0_0_0_host_default():
    src = _read("core/engine/__main__.py")
    assert not re.search(
        r"""get\(\s*["']ENGINE_HOST["']\s*,\s*["']0\.0\.0\.0["']""", src
    ), "INV-03: engine host default reverted to 0.0.0.0 (binds all LAN interfaces)"


def test_no_bare_postgres_type_as_column():
    # INV-27: postgres-only types (JSONB) must only reach the schema via the
    # dual-dialect JSON_TYPE alias (JSON().with_variant(JSONB, ...)). A bare
    # Column(JSONB) would break the SQLite desktop path. The import of JSONB for
    # the alias is legitimate, so we gate on USAGE as a column type, not the import.
    bare = re.compile(r"\b(?:Column|mapped_column)\s*\([^)]*\bJSONB\b")
    for p in (AI / "core").rglob("*.py"):
        m = bare.search(p.read_text(encoding="utf-8"))
        if m:
            pytest.fail(
                f"INV-27: bare postgres type used as column in {p.relative_to(AI)}: "
                f"{m.group(0)} — use JSON_TYPE (with_variant) instead"
            )


def test_cloud_app_paths_are_desktop_guarded():
    # INV-02/desktop: a container default like "/app/data" is acceptable ONLY as a
    # fallback that is explicitly overridden on Windows (os.name == "nt"). An
    # unguarded "/app/..." default would leak the cloud filesystem layout onto the
    # desktop. We require every core file that mentions "/app/" to also carry the
    # Windows override in the same module.
    for p in (AI / "core").rglob("*.py"):
        txt = p.read_text(encoding="utf-8")
        if "/app/" in txt and 'os.name == "nt"' not in txt:
            pytest.fail(
                f'Unguarded container path "/app/..." in {p.relative_to(AI)} '
                '(no os.name == "nt" desktop override in this module)'
            )


def test_no_docker_hostnames_in_core():
    pat = re.compile(r"http://(backend|db|redis|postgres):")
    for p in (AI / "core").rglob("*.py"):
        if pat.search(p.read_text(encoding="utf-8")):
            pytest.fail(f"Docker-internal hostname in {p.relative_to(AI)}")


# ── Guard-existence net: the per-INV behavioural tests must not be silently removed ──
@pytest.mark.parametrize(
    "guard",
    [
        "test_keychain.py",  # INV-01 / INV-05
        "test_user_data_dir.py",  # INV-02
        "test_engine_host_binding.py",  # INV-03
        "test_g0a_db_bootstrap_wiring.py",  # INV-24 / INV-26
        "test_g2_desktop_env_contract.py",  # INV-03/07/12/32 desktop env contract
        "test_shadow_boot_oss.py",  # INV-12 shadow boot
    ],
)
def test_inv_guard_test_present(guard):
    assert (AI / "tests" / "unit" / guard).is_file(), (
        f"Invariant guard test removed: tests/unit/{guard} — a desktop invariant lost "
        "its dedicated regression test"
    )
