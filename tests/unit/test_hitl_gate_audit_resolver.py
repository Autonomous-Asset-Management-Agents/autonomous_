# tests/unit/test_hitl_gate_audit_resolver.py
# ii-4b (PR-0a-ii, GAP2): _resolve_audit_logger — Art-14 audit-logger resolution + fallback.
#
# Addresses the PR #1206 review note: the module-level `_fallback_audit_logger` singleton was
# untested (a `# pragma: no cover` hid the fallback) and could transfer state across parallel
# tests. These tests pin it explicitly and reset the global per case so no state leaks between
# them:
#   1. reuse the runner's _senate when configured (one shared hash chain),
#   2. a STABLE fallback singleton when the runner logger is absent (chain continuity needs a
#      single instance),
#   3. the defensive fallback when the round-table accessor raises (covers the except branch),
#   4. thread-safety of the lazy singleton — dev-env §2.8: a new global needs a concurrency
#      test, not just a lock.
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

_AI_BOT = Path(__file__).resolve().parents[2]  # ai_trading_bot/
if str(_AI_BOT) not in sys.path:
    sys.path.insert(0, str(_AI_BOT))

import core.hitl_gate as hg  # noqa: E402
from core.round_table.senate_log import LocalJSONAuditLogger  # noqa: E402


def _reset():
    """Clear the process-wide fallback so no state transfers between tests (the review note)."""
    hg._fallback_audit_logger = None


def test_reuses_runner_logger_when_configured():
    _reset()
    sentinel = object()
    with patch("core.round_table.runner.get_audit_logger", return_value=sentinel):
        assert hg._resolve_audit_logger() is sentinel
    assert hg._fallback_audit_logger is None  # no fallback ever constructed


def test_falls_back_to_stable_singleton_when_runner_none():
    _reset()
    with patch("core.round_table.runner.get_audit_logger", return_value=None):
        first = hg._resolve_audit_logger()
        second = hg._resolve_audit_logger()
    assert isinstance(first, LocalJSONAuditLogger)
    assert first is second  # ONE instance ⇒ one tamper-evident hash chain
    _reset()


def test_falls_back_when_runner_accessor_raises():
    # Covers the defensive except branch (previously `# pragma: no cover`).
    _reset()
    with patch(
        "core.round_table.runner.get_audit_logger",
        side_effect=RuntimeError("boot order"),
    ):
        logger = hg._resolve_audit_logger()
    assert isinstance(logger, LocalJSONAuditLogger)
    _reset()


def test_fallback_singleton_is_thread_safe():
    # dev-env §2.8: prove the double-checked-locked singleton yields a single consistent
    # instance under concurrent construction — not merely assert a lock exists.
    _reset()
    with patch("core.round_table.runner.get_audit_logger", return_value=None):
        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(lambda _: hg._resolve_audit_logger(), range(64)))
    assert len({id(r) for r in results}) == 1  # exactly one instance across all threads
    _reset()
