# #1126 — Checkpointer context-manager retention (graph._build_checkpointer).
# Plan: 2026-06-11-bugfix-batch/plan_A (Papa APPROVED; W-1: autouse reset fixture).
#
# Bug: both checkpointer branches entered the from_conn_string() context manager
# but discarded `result`, so the GC could finalise the @contextmanager generator
# and close the LIVE checkpointer's DB/Redis connection mid-run. Fix: singleton —
# created once per process; every subsequent _build_checkpointer() call returns
# the same instance (no connection leak even when called every trading cycle).

import gc
import threading
from unittest.mock import MagicMock, patch

import pytest

from core.orchestration import graph


@pytest.fixture(autouse=True)
def _reset_singleton():
    """W-1: reset the module-level singleton between tests so each test starts
    from a clean state (the singleton persists for the process lifetime by design)."""
    graph._CHECKPOINTER_INSTANCE = None
    graph._CHECKPOINTER_CM = None
    yield
    graph._CHECKPOINTER_INSTANCE = None
    graph._CHECKPOINTER_CM = None


class _FakeCM:
    """A from_conn_string()-style context manager whose connection is 'closed'
    by __exit__ — mirrors langgraph-checkpoint >=3.x's @contextmanager."""

    def __init__(self):
        self.saver = MagicMock(name="saver")
        self.saver.alive = True
        self.exited = False

    def __enter__(self):
        return self.saver

    def __exit__(self, *exc):
        self.exited = True
        self.saver.alive = False  # the cleanup that closes the connection
        return False


# ---------------------------------------------------------------------------
# _enter_cm / _commit_singleton unit behaviour
# ---------------------------------------------------------------------------
def test_enter_cm_returns_checkpointer_and_cm_without_committing():
    cm = _FakeCM()
    checkpointer, retained = graph._enter_cm(cm)
    assert checkpointer is cm.saver
    assert retained is cm
    assert cm.exited is False
    # _enter_cm must NOT touch the singleton globals (commit happens later)
    assert graph._CHECKPOINTER_INSTANCE is None
    assert graph._CHECKPOINTER_CM is None


def test_enter_cm_plain_saver_passthrough():
    plain = MagicMock(spec=[])  # no __enter__
    checkpointer, retained = graph._enter_cm(plain)
    assert checkpointer is plain
    assert retained is None


def test_commit_singleton_publishes_and_retains():
    cm = _FakeCM()
    checkpointer, retained = graph._enter_cm(cm)
    out = graph._commit_singleton(checkpointer, retained)
    assert out is cm.saver
    assert graph._CHECKPOINTER_INSTANCE is cm.saver
    assert graph._CHECKPOINTER_CM is cm  # ref retained → GC can't close the conn


def test_committed_cm_survives_gc():
    """The core bug symptom: after the local result ref is dropped and the GC
    runs, the committed manager must NOT have been finalised/exited."""
    checkpointer, retained = graph._enter_cm(_FakeCM())
    graph._commit_singleton(checkpointer, retained)
    del checkpointer, retained
    gc.collect()
    assert graph._CHECKPOINTER_CM is not None
    assert graph._CHECKPOINTER_CM.exited is False
    assert graph._CHECKPOINTER_CM.saver.alive is True


# ---------------------------------------------------------------------------
# Redis branch retains the CM; the no-Redis desktop path retains nothing (→ None)
# ---------------------------------------------------------------------------
def test_no_redis_path_returns_none_no_retention(monkeypatch):
    """Desktop/OSS (REDIS_URL empty): the linear symbol_eval graph uses NO checkpointer.
    _build_checkpointer returns None and never touches the singleton — no SQLite I/O and
    no CM to retain (fix/checkpointer-no-redis-none; replaces the old SQLite-branch test).
    """
    monkeypatch.setenv("REDIS_URL", "")
    out = graph._build_checkpointer()
    assert out is None
    assert graph._CHECKPOINTER_INSTANCE is None
    assert graph._CHECKPOINTER_CM is None


def test_redis_branch_retains_cm(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://dummy:6379")  # → Redis branch
    cm = _FakeCM()
    fake_mod = MagicMock()
    fake_mod.RedisSaver.from_conn_string.return_value = cm
    with patch.dict("sys.modules", {"langgraph.checkpoint.redis": fake_mod}):
        ck = graph._build_checkpointer()
    assert ck is cm.saver
    assert graph._CHECKPOINTER_CM is cm
    assert cm.exited is False
    cm.saver.setup.assert_called_once()  # Redis structures still created


def test_redis_import_error_returns_none(monkeypatch):
    """Redis path but langgraph.checkpoint.redis unavailable → None, singleton untouched
    (fail-closed; the graph still runs without a checkpointer)."""
    monkeypatch.setenv("REDIS_URL", "redis://dummy:6379")
    # sys.modules[name] = None makes `from langgraph.checkpoint.redis import …` raise ImportError
    with patch.dict("sys.modules", {"langgraph.checkpoint.redis": None}):
        out = graph._build_checkpointer()
    assert out is None
    assert graph._CHECKPOINTER_INSTANCE is None
    assert graph._CHECKPOINTER_CM is None


def test_enter_cm_enter_raises_leaves_singleton_untouched():
    """If __enter__() raises, _enter_cm never touches the globals → no orphan ref
    and a clean retry next call."""

    class _FailingCM:
        def __enter__(self):
            raise RuntimeError("connection refused")

        def __exit__(self, *a):
            pass

    with pytest.raises(RuntimeError, match="connection refused"):
        graph._enter_cm(_FailingCM())

    assert graph._CHECKPOINTER_CM is None
    assert graph._CHECKPOINTER_INSTANCE is None


def test_redis_setup_failure_does_not_poison_singleton(monkeypatch):
    """Antigravity-review fix: if RedisSaver.setup() raises (transient outage at
    first-call time), the singleton must stay None so the next call retries —
    never returns the un-setup'd saver forever — and the entered CM is closed."""
    monkeypatch.setenv("REDIS_URL", "redis://dummy:6379")
    cm = _FakeCM()
    cm.saver.setup.side_effect = RuntimeError("redis unreachable")
    fake_mod = MagicMock()
    fake_mod.RedisSaver.from_conn_string.return_value = cm
    with patch.dict("sys.modules", {"langgraph.checkpoint.redis": fake_mod}):
        out = graph._build_checkpointer()
    assert out is None  # fail-safe: graph runs without checkpointer
    assert graph._CHECKPOINTER_INSTANCE is None  # NOT poisoned
    assert graph._CHECKPOINTER_CM is None
    assert cm.exited is True  # entered CM was closed on the failure path


# ---------------------------------------------------------------------------
# Singleton / no-leak guarantee (P1 from Antigravity review)
# ---------------------------------------------------------------------------
def test_build_checkpointer_singleton_no_leak(monkeypatch):
    """Calling _build_checkpointer() 100 times must return the same instance and create
    only ONE connection — the core fix for the trading-loop leak. Exercised on the Redis
    branch, the only path that still builds a checkpointer after the no-Redis None change.
    """
    monkeypatch.setenv("REDIS_URL", "redis://dummy:6379")
    cm = _FakeCM()
    fake_mod = MagicMock()
    fake_mod.RedisSaver.from_conn_string.return_value = cm
    with patch.dict("sys.modules", {"langgraph.checkpoint.redis": fake_mod}):
        instances = [graph._build_checkpointer() for _ in range(100)]
    assert all(i is instances[0] for i in instances)
    assert fake_mod.RedisSaver.from_conn_string.call_count == 1


def test_build_checkpointer_thread_safe(monkeypatch):
    """Concurrent calls must produce exactly one checkpointer (no double-init) — Redis branch."""
    monkeypatch.setenv("REDIS_URL", "redis://dummy:6379")
    cm = _FakeCM()
    fake_mod = MagicMock()
    fake_mod.RedisSaver.from_conn_string.return_value = cm

    results = []
    with patch.dict("sys.modules", {"langgraph.checkpoint.redis": fake_mod}):

        def _call():
            results.append(graph._build_checkpointer())

        threads = [threading.Thread(target=_call) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    assert fake_mod.RedisSaver.from_conn_string.call_count == 1
    assert all(r is results[0] for r in results)
