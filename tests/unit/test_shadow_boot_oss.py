"""Lock-in test for the OSS-specific shadow_boot variant.

The OSS snapshot script (`scripts/oss_make_snapshot.sh:256-258`) renames
`scripts/shadow_boot.oss.py` to `scripts/shadow_boot.py` on publish to
`autonomous_`. A regression in 2026-04 shipped the OSS variant with bare
`API_KEY`, `API_SECRET`, `BASE_URL` free-variable references (NameError at
runtime) that `core/engine/__main__.py:34` swallowed via `sys.exit(0)`,
causing every fresh OSS install to exit clean before placing a single trade.

The Enterprise-edition `shadow_boot.py` correctly uses `config.API_KEY` etc.
(see line ~41-44 of that file), and the existing unit tests at
`tests/test_shadow_boot.py` patch `_check_alpaca` away so they never exercise
this code path. These tests fill that gap by asserting the OSS-only variant
does not regress to bare-name references.
"""

from __future__ import annotations

import ast
from pathlib import Path

import allure

# The OSS variant lives under the same scripts/ tree as the Enterprise file.
# Resolve relative to this test file so the assertion does not depend on
# pytest's working directory.
_OSS_VARIANT = Path(__file__).resolve().parents[2] / "scripts" / "shadow_boot.oss.py"


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
def test_oss_shadow_boot_variant_exists():
    assert _OSS_VARIANT.is_file(), (
        f"OSS variant missing at {_OSS_VARIANT}. Snapshot rename "
        "(scripts/oss_make_snapshot.sh) depends on this file."
    )


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
def test_oss_shadow_boot_parses_as_python():
    """Sanity gate before structural assertions."""
    ast.parse(_OSS_VARIANT.read_text(encoding="utf-8"))


@allure.feature("VC-0 Platform Infrastructure")
@allure.story("Core Utilities")
def test_oss_shadow_boot_uses_config_prefix_for_alpaca_credentials():
    """Bare `API_KEY` / `API_SECRET` / `BASE_URL` reads must not appear.

    The module imports `config` (line 11) but does NOT do
    `from config import API_KEY`, so the bare name has no module-scope
    binding. A bare-name read in Load context = NameError at runtime.

    Acceptable: `config.API_KEY`, `config.API_SECRET`, `config.BASE_URL`
    (these resolve via attribute access on the imported module).
    """
    tree = ast.parse(_OSS_VARIANT.read_text(encoding="utf-8"))
    forbidden = {"API_KEY", "API_SECRET", "BASE_URL"}
    bare_reads: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in forbidden:
            if isinstance(node.ctx, ast.Load):
                bare_reads.append((node.id, node.lineno))
    assert not bare_reads, (
        "shadow_boot.oss.py must reference Alpaca config via `config.<NAME>`, "
        f"never bare names. Bare reads found: {bare_reads}"
    )
