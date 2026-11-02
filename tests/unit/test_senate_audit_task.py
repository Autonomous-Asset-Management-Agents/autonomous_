# tests/unit/test_senate_audit_task.py
# #1253 — Senate audit-log fire-and-forget tasks must keep a strong reference
# (no GC drop) and surface previously-swallowed write failures at WARNING.

from __future__ import annotations

import asyncio
import logging

import pytest


@pytest.fixture(autouse=True)
def _clear_audit_tasks():
    """W-2: isolate the module-global audit-task set between tests."""
    from core.round_table import senate_log

    senate_log._BACKGROUND_AUDIT_TASKS.clear()
    yield
    senate_log._BACKGROUND_AUDIT_TASKS.clear()


async def test_failing_audit_task_is_logged_not_swallowed(caplog):
    """A fire-and-forget audit-log task that raises must emit a WARNING with the
    traceback, instead of silently dropping the exception (#1253)."""
    from core.round_table.senate_log import spawn_audit_task

    async def boom() -> None:
        raise RuntimeError("audit write failed")

    with caplog.at_level(logging.WARNING):
        task = spawn_audit_task(boom())
        with pytest.raises(RuntimeError):
            await task
        # let the done-callback (scheduled via call_soon) run
        await asyncio.sleep(0)

    failures = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "audit-log task failed" in r.getMessage()
    ]
    assert len(failures) == 1, "audit-task failure was swallowed silently"
    assert failures[0].exc_info is not None, "WARNING must carry the traceback"


async def test_audit_task_reference_retained_until_done():
    """The task must be held in a strong-reference set while running (prevents
    GC drop) and discarded once complete."""
    from core.round_table import senate_log

    gate = asyncio.Event()

    async def slow() -> None:
        await gate.wait()

    task = senate_log.spawn_audit_task(slow())
    assert task in senate_log._BACKGROUND_AUDIT_TASKS, "no strong reference retained"

    gate.set()
    await task
    await asyncio.sleep(0)
    assert task not in senate_log._BACKGROUND_AUDIT_TASKS, "task not discarded on done"


async def test_successful_audit_task_emits_no_failure_warning(caplog):
    """A well-formed audit-log task must not produce any failure WARNING."""
    from core.round_table.senate_log import spawn_audit_task

    async def ok() -> None:
        return None

    with caplog.at_level(logging.WARNING):
        task = spawn_audit_task(ok())
        await task
        await asyncio.sleep(0)

    assert not [r for r in caplog.records if "audit-log task failed" in r.getMessage()]
