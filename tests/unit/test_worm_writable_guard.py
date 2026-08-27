"""test_worm_writable_guard.py — PR-1 WORM Fail-Closed Boot Guard.

Verifies that ``assert_worm_writable()`` blocks live trading when the WORM audit
directory is not writable, and passes silently when it is.  Uses an active
temp-file probe (not ``os.W_OK``) to guarantee correctness on Windows NTFS ACLs.

Scenarios (from the implementation plan Gherkin):
  1. Writable dir   → no exception
  2. Read-only dir  → RuntimeError ("not writable")
  3. Non-existent   → auto-created, no exception
  4. Paper trading  → guard never invoked (tested via assert_live_trading_config)
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from unittest import mock

import pytest

# ── Scenario 1: writable dir → pass ──────────────────────────────────────────


def test_worm_writable_passes_on_writable_dir(tmp_path, monkeypatch):
    """assert_worm_writable() must NOT raise when the WORM dir is writable."""
    monkeypatch.setenv("SENATE_LOG_DIR", str(tmp_path))
    # Force no existing logger so it picks up the patched env.
    monkeypatch.setattr("core.hitl_gate._resolve_audit_logger", lambda: lambda: None)

    from core.hitl_gate import assert_worm_writable

    # Should complete without exception.
    assert_worm_writable()


# ── Scenario 2: read-only dir → RuntimeError ─────────────────────────────────


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="os.chmod read-only on directories is unreliable on Windows",
)
@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root bypasses read-only directory permissions",
)
def test_worm_writable_raises_on_readonly_dir(tmp_path, monkeypatch):
    """assert_worm_writable() must raise RuntimeError when the WORM dir is read-only."""
    readonly_dir = tmp_path / "readonly_worm"
    readonly_dir.mkdir()
    monkeypatch.setenv("SENATE_LOG_DIR", str(readonly_dir))
    monkeypatch.setattr("core.hitl_gate._resolve_audit_logger", lambda: lambda: None)

    # Make dir read-only (remove write + execute for owner on POSIX).
    readonly_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)

    from core.hitl_gate import assert_worm_writable

    try:
        with pytest.raises(RuntimeError, match="not writable"):
            assert_worm_writable()
    finally:
        # Restore permissions so pytest can clean up tmp_path.
        readonly_dir.chmod(stat.S_IRWXU)


def test_worm_writable_raises_when_probe_write_fails(tmp_path, monkeypatch):
    """assert_worm_writable() must raise RuntimeError when the write probe fails.

    Platform-independent: mocks Path.write_bytes to raise OSError, simulating a
    permission denial on any OS (including Windows NTFS ACL denial).
    """
    monkeypatch.setenv("SENATE_LOG_DIR", str(tmp_path))
    monkeypatch.setattr("core.hitl_gate._resolve_audit_logger", lambda: lambda: None)

    from core.hitl_gate import assert_worm_writable

    with mock.patch.object(Path, "write_bytes", side_effect=OSError("mocked ACL deny")):
        with pytest.raises(RuntimeError, match="not writable"):
            assert_worm_writable()


# ── Scenario 3: non-existent dir → auto-created ─────────────────────────────


def test_worm_writable_creates_missing_dir(tmp_path, monkeypatch):
    """assert_worm_writable() must auto-create a non-existent WORM dir."""
    new_dir = tmp_path / "deep" / "nested" / "worm_logs"
    assert not new_dir.exists()

    monkeypatch.setenv("SENATE_LOG_DIR", str(new_dir))
    monkeypatch.setattr("core.hitl_gate._resolve_audit_logger", lambda: lambda: None)

    from core.hitl_gate import assert_worm_writable

    assert_worm_writable()
    assert new_dir.is_dir()


def test_worm_writable_raises_when_mkdir_fails(tmp_path, monkeypatch):
    """assert_worm_writable() must raise RuntimeError when the dir cannot be created."""
    impossible_dir = tmp_path / "impossible"

    # Provide a pre-built mock logger with _log_dir so _resolve_audit_logger() returns it
    fake_logger = mock.MagicMock()
    fake_logger._log_dir = impossible_dir
    monkeypatch.setattr(
        "core.hitl_gate._resolve_audit_logger", lambda: lambda: fake_logger
    )

    from core.hitl_gate import assert_worm_writable

    with mock.patch.object(Path, "mkdir", side_effect=OSError("mocked mkdir fail")):
        with pytest.raises(RuntimeError, match="cannot be created"):
            assert_worm_writable()


# ── Scenario 4: paper trading → guard never invoked ──────────────────────────


def test_paper_trading_skips_worm_guard(monkeypatch):
    """When PAPER_TRADING=True, assert_live_trading_config() must NOT call assert_worm_writable()."""
    monkeypatch.setattr("core.engine.live_trading_guard.PAPER_TRADING", True)

    with mock.patch("core.hitl_gate.assert_worm_writable") as mock_guard:
        from core.engine.live_trading_guard import assert_live_trading_config

        assert_live_trading_config()
        mock_guard.assert_not_called()
