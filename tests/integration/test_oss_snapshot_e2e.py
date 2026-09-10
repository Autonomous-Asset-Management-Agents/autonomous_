"""
P-F: OSS snapshot-time integration test (regression-lock).

The OSS strip pipeline (`scripts/oss_make_snapshot.sh:160-172`) renames
`*.oss.py` stub files over their private counterparts at snapshot time:

    config.oss.py                       → config.py
    core/secret_manager_utils.oss.py    → core/secret_manager_utils.py
    scripts/shadow_boot.oss.py          → scripts/shadow_boot.py
    requirements.oss.txt                → requirements.txt

This is a structural trap: a developer who fixes a bug in `shadow_boot.py`
(the *private* file) without applying the same fix to `shadow_boot.oss.py`
(the actual OSS payload) silently leaves the bug in the OSS edition.

PR #840 hit this exact trap. The `GEMINI_API_KEY` shadow-boot fail-closed
fix was applied to the wrong file. The OSS stack continued to crash on
first boot for users without a Gemini key, even though the private fix
"looked correct" in code review. Sprint-1 caught it manually after a
senior engineering review of the diff. This test catches that class of
regression automatically: it runs the snapshot script and asserts the
load-bearing fix tokens (`PAPER_TRADING`, `Degraded Sentiment Mode`)
reach the post-rename OSS payload.

Without this test, every future refactor of any of the four stub-renamed
files can silently un-fix the OSS edition.

Cost: ~30 s per run (the snapshot script copies the full source tree).
Skips on platforms without `bash` available (Windows-without-WSL); runs
in CI ubuntu-latest where the snapshot pipeline lives.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

# Repo root: this file is at ai_trading_bot/tests/integration/test_oss_snapshot_e2e.py
# parents: [integration, tests, ai_trading_bot, REPO_ROOT]
REPO_ROOT = Path(__file__).resolve().parents[3]
SNAPSHOT_SCRIPT = REPO_ROOT / "scripts" / "oss_make_snapshot.sh"


_BASH_MISSING_REASON = "bash interpreter not available on this platform"
_SCRIPT_MISSING_REASON = (
    f"snapshot script not at expected path {SNAPSHOT_SCRIPT.relative_to(REPO_ROOT)}"
)


def _has_bash() -> bool:
    return shutil.which("bash") is not None


def _has_snapshot_script() -> bool:
    return SNAPSHOT_SCRIPT.is_file()


pytestmark = [
    pytest.mark.skipif(not _has_bash(), reason=_BASH_MISSING_REASON),
    pytest.mark.skipif(not _has_snapshot_script(), reason=_SCRIPT_MISSING_REASON),
]


@pytest.fixture(scope="module")
def snapshot_dir() -> Path:
    """
    Run `scripts/oss_make_snapshot.sh` into a fresh temp dir and yield
    the resulting OSS-payload root. Module-scoped so all tests in this
    file share one snapshot run (~30 s amortised).

    Honours `OSS_SNAPSHOT_DIR` if it points at an already-built snapshot
    (CI optimisation: oss-ci.yml may have already produced one). Falls
    back to building our own.
    """
    pre_built = os.environ.get("OSS_SNAPSHOT_DIR")
    if pre_built:
        candidate = Path(pre_built)
        # Trust the env var only if the snapshot actually contains the
        # post-rename payload — otherwise build our own.
        if candidate.is_dir() and (candidate / "scripts" / "shadow_boot.py").is_file():
            yield candidate
            return

    with tempfile.TemporaryDirectory(prefix="oss-snapshot-test-") as tmp:
        env = {**os.environ, "OSS_SNAPSHOT_DIR": tmp}
        result = subprocess.run(
            ["bash", str(SNAPSHOT_SCRIPT)],
            env=env,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            pytest.fail(
                "scripts/oss_make_snapshot.sh exited with non-zero code "
                f"{result.returncode}.\n"
                f"--- stdout ---\n{result.stdout[-4000:]}\n"
                f"--- stderr ---\n{result.stderr[-4000:]}"
            )
        yield Path(tmp)


# ── Stub-rename invariants ──────────────────────────────────────────────────


def test_snapshot_renames_shadow_boot_oss(snapshot_dir: Path) -> None:
    """`scripts/shadow_boot.oss.py` must be renamed to `scripts/shadow_boot.py`."""
    target = snapshot_dir / "scripts" / "shadow_boot.py"
    stub = snapshot_dir / "scripts" / "shadow_boot.oss.py"
    assert target.is_file(), (
        f"Post-rename {target.relative_to(snapshot_dir)} missing. "
        f"Did the `_require_stub` call in scripts/oss_make_snapshot.sh:168 fail?"
    )
    assert not stub.exists(), (
        f"OSS stub {stub.relative_to(snapshot_dir)} should be gone after rename, "
        f"but is still present."
    )


def test_snapshot_renames_config_oss(snapshot_dir: Path) -> None:
    """`config.oss.py` must be renamed to `config.py`."""
    target = snapshot_dir / "config.py"
    stub = snapshot_dir / "config.oss.py"
    assert target.is_file(), f"Post-rename config.py missing"
    assert not stub.exists(), f"config.oss.py stub should be gone after rename"


def test_snapshot_renames_secret_manager_utils_oss(snapshot_dir: Path) -> None:
    """`core/secret_manager_utils.oss.py` must be renamed."""
    target = snapshot_dir / "core" / "secret_manager_utils.py"
    stub = snapshot_dir / "core" / "secret_manager_utils.oss.py"
    assert target.is_file(), f"Post-rename core/secret_manager_utils.py missing"
    assert (
        not stub.exists()
    ), f"core/secret_manager_utils.oss.py stub should be gone after rename"


def test_snapshot_renames_requirements_oss(snapshot_dir: Path) -> None:
    """`requirements.oss.txt` must be renamed to `requirements.txt`."""
    target = snapshot_dir / "requirements.txt"
    stub = snapshot_dir / "requirements.oss.txt"
    assert target.is_file(), f"Post-rename requirements.txt missing"
    assert not stub.exists(), f"requirements.oss.txt stub should be gone after rename"


# ── Fix-token invariants (the load-bearing reason this test exists) ────────


def test_shadow_boot_paper_trading_fallback_token(snapshot_dir: Path) -> None:
    """
    The post-rename `shadow_boot.py` must contain the `PAPER_TRADING` env
    check that lets OSS users boot in Degraded Sentiment Mode without a
    `GEMINI_API_KEY`.

    PR #840 Sprint-1: this fix was originally applied to
    `ai_trading_bot/scripts/shadow_boot.py` (the *private* file) instead
    of `shadow_boot.oss.py` (the OSS payload). The OSS edition continued
    to crash on first boot until the wrong-file mistake was caught
    manually. This assertion locks the correct payload reaching OSS.
    """
    payload = (snapshot_dir / "scripts" / "shadow_boot.py").read_text(encoding="utf-8")
    assert "PAPER_TRADING" in payload, (
        "Post-rename `shadow_boot.py` is missing the `PAPER_TRADING` gate.\n"
        "Likely cause: the fix-closed-on-missing-Gemini-key fallback was "
        "applied to `ai_trading_bot/scripts/shadow_boot.py` (the private "
        "file), but the OSS payload is `scripts/shadow_boot.oss.py` "
        "(renamed to `shadow_boot.py` at snapshot time).\n"
        "Fix: edit `ai_trading_bot/scripts/shadow_boot.oss.py`, not the "
        "private `shadow_boot.py`."
    )
    assert "Degraded Sentiment Mode" in payload, (
        "Post-rename `shadow_boot.py` is missing the 'Degraded Sentiment Mode' "
        "log message that `docs/oss/TROUBLESHOOTING.md` instructs users to look "
        "for. The fix tokens did not reach the OSS payload."
    )


# ── Strip-pipeline leak guard ──────────────────────────────────────────────


def test_snapshot_excludes_private_audit_files(snapshot_dir: Path) -> None:
    """
    Files explicitly listed in `scripts/oss_exclude.txt` must NOT appear
    in the snapshot. Catches strip-pipeline regressions like the one in
    PR #840 (`oss_review_analysis.md` leaking into the OSS payload, which
    in turn broke `tests/unit/test_oss_hygiene_defaults.py::test_a7_*`).
    """
    forbidden_files = [
        "oss_review_analysis.md",
        "OSS_AUDIT_REPORT.md",
        "IMPROVEMENTS_PROFITABILITY_AND_TESTING.md",
        "git_status.txt",
        "docker-compose.migrate.yml",
        "crd.yaml.bak",
    ]
    leaks = [f for f in forbidden_files if (snapshot_dir / f).exists()]
    assert not leaks, (
        f"Strip-pipeline leak: forbidden files appear in OSS snapshot: {leaks}.\n"
        f"Fix: add the missing entries to `scripts/oss_exclude.txt` or verify "
        f"the existing entries match the actual filenames."
    )


def test_snapshot_flattens_docker_compose_paths(snapshot_dir: Path) -> None:
    """`docker-compose.oss.yml` must reference `Dockerfile.public-api` at the root."""
    compose_file = snapshot_dir / "docker-compose.oss.yml"
    assert compose_file.is_file(), "docker-compose.oss.yml missing from snapshot"

    content = compose_file.read_text(encoding="utf-8")
    assert "dockerfile: Dockerfile.public-api" in content, (
        "docker-compose.oss.yml still references the nested public-api Dockerfile path.\n"
        "Expected: 'dockerfile: Dockerfile.public-api'"
    )
