"""Parity + failure-isolation tests for the create_live_features process-pool off-load (Fix A, #2499).

The event-loop-stall fix moves the per-symbol pandas_ta feature build off the engine's main thread into
a ProcessPoolExecutor. Because a trading decision MUST NOT change and one bad symbol MUST NOT disturb the
~20 siblings evaluated concurrently on the shared pool, these tests pin:

  1. Flag ON  -> featurize() == inline create_live_features() (byte-identical), and it genuinely ran in a
     SEPARATE process (not a silent inline fallback that would make the parity assertion vacuous).
  2. Flag OFF / offload=False (backtest) / latched-broken -> inline, no pool created — byte-identical 0.3.1.
  3. FeatureGenerationError (bad-data ADR-SEC-03 abstention) propagates UNCHANGED, pool untouched, never 0.0.
  4. A crashed worker (BrokenProcessPool) degrades THIS symbol inline and LATCHES the pool off.
  5. A benign per-symbol failure NEVER cancels sibling futures and NEVER tears the shared pool down
     (regression for the HIGH cross-symbol decision-drift finding).
"""

import asyncio
import concurrent.futures
import os
from concurrent.futures.process import BrokenProcessPool

import numpy as np
import pandas as pd
import pytest

import config
import models.feature_pool as fp
import models.torch_model as torch_model
from models.feature_pool import featurize
from models.torch_model import FeatureGenerationError, create_live_features


def _make_hist(rows: int = 120) -> pd.DataFrame:
    """Deterministic synthetic OHLCV with enough rows for the slowest indicator (sma_50) to warm up."""
    rng = np.random.default_rng(42)
    idx = pd.date_range("2024-01-01", periods=rows, freq="D")
    close = 100 + np.cumsum(rng.normal(0, 1, rows))
    high = close + rng.uniform(0.1, 2.0, rows)
    low = close - rng.uniform(0.1, 2.0, rows)
    open_ = close + rng.normal(0, 0.5, rows)
    volume = rng.uniform(1e5, 1e6, rows)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


class _FakePool:
    """In-process stand-in for the ProcessPoolExecutor: lets a test decide each submit's outcome and
    records teardown so we can prove siblings are never cancelled."""

    def __init__(self):
        self.futures = []
        self.shutdown_calls = []  # list of cancel_futures values
        self.next_exc = (
            None  # exception to attach to the NEXT submit's future (else run inline)
        )

    def submit(self, fn, *args):
        f = concurrent.futures.Future()
        if self.next_exc is not None:
            f.set_exception(self.next_exc)
        else:
            f.set_result(fn(*args))
        self.futures.append(f)
        return f

    def shutdown(self, wait=True, cancel_futures=False):
        self.shutdown_calls.append(cancel_futures)


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Each test starts from a clean pool + latch state."""
    fp.shutdown_pool()
    fp._pool_broken = False
    fp._warned_offload_error = False
    yield
    fp.shutdown_pool()
    fp._pool_broken = False
    fp._warned_offload_error = False


def _enable(monkeypatch, on=True):
    monkeypatch.setattr(config, "FEATURE_POOL_ENABLED", on, raising=False)


# ---- 1. parity + real cross-process proof ---------------------------------------------------------


def test_pool_on_is_byte_identical_to_inline(monkeypatch):
    _enable(monkeypatch, True)
    hist = _make_hist()
    inline = create_live_features(hist)
    pooled = asyncio.run(featurize(hist))
    assert pooled is not None
    pd.testing.assert_frame_equal(pooled, inline, check_exact=True)
    # Prove the pool path was actually taken (not a silent inline early-return, which would make the
    # equality above vacuous): a pool object exists and was not latched broken.
    assert fp._pool is not None
    assert fp._pool_broken is False


def test_pool_actually_runs_in_a_separate_process():
    # Directly prove the executor crosses the process boundary: a worker reports a different PID.
    pool = fp._get_pool(2)
    worker_pid = pool.submit(os.getpid).result(timeout=60)
    assert worker_pid != os.getpid()


# ---- 2. no-op paths: flag off / backtest / latched ------------------------------------------------


def test_pool_off_matches_inline_and_creates_no_pool(monkeypatch):
    _enable(monkeypatch, False)
    hist = _make_hist()
    result = asyncio.run(featurize(hist))
    pd.testing.assert_frame_equal(result, create_live_features(hist), check_exact=True)
    assert fp._pool is None  # the inline early-return never touched the pool


def test_backtest_offload_false_stays_inline_even_when_flag_on(monkeypatch):
    _enable(monkeypatch, True)
    hist = _make_hist()
    result = asyncio.run(featurize(hist, offload=False))
    pd.testing.assert_frame_equal(result, create_live_features(hist), check_exact=True)
    assert fp._pool is None


def test_latched_broken_pool_goes_inline_without_rebuild(monkeypatch):
    _enable(monkeypatch, True)
    fp._pool_broken = True
    hist = _make_hist()
    # _get_pool must never be called once latched — assert by making it explode if used.
    monkeypatch.setattr(
        fp, "_get_pool", lambda w: (_ for _ in ()).throw(AssertionError("rebuilt!"))
    )
    result = asyncio.run(featurize(hist))
    pd.testing.assert_frame_equal(result, create_live_features(hist), check_exact=True)


# ---- 3. FeatureGenerationError propagates unchanged (ADR-SEC-03), pool untouched -------------------


def test_feature_generation_error_reraises_and_leaves_pool_intact(monkeypatch):
    _enable(monkeypatch, True)
    fake = _FakePool()
    fake.next_exc = FeatureGenerationError("thin data")
    monkeypatch.setattr(fp, "_get_pool", lambda w: fake)
    calls = []
    monkeypatch.setattr(
        torch_model, "create_live_features", lambda h: calls.append(h) or "INLINE"
    )

    with pytest.raises(FeatureGenerationError):
        asyncio.run(featurize(_make_hist()))

    assert (
        calls == []
    )  # re-raised, NOT recomputed inline (no double compute / GIL blip)
    assert (
        fake.shutdown_calls == []
    )  # the shared pool is NOT torn down on a business error
    assert fp._pool_broken is False


# ---- 4. crashed worker: inline + latch ------------------------------------------------------------


def test_broken_process_pool_inlines_and_latches(monkeypatch):
    _enable(monkeypatch, True)
    fake = _FakePool()
    fake.next_exc = BrokenProcessPool("a worker died")
    monkeypatch.setattr(fp, "_get_pool", lambda w: fake)
    monkeypatch.setattr(torch_model, "create_live_features", lambda h: "INLINE_RESULT")

    result = asyncio.run(featurize(_make_hist()))

    assert (
        result == "INLINE_RESULT"
    )  # ADR-SEC-03: infra failure degrades, never abstains to 0.0
    assert fp._pool_broken is True  # latched off so it does not respawn per symbol
    assert fake.shutdown_calls == [False]  # dropped WITHOUT cancel_futures


# ---- 5. THE regression: a benign per-symbol failure must not touch siblings ------------------------


def test_benign_failure_does_not_cancel_siblings_or_teardown_pool(monkeypatch):
    _enable(monkeypatch, True)
    fake = _FakePool()
    monkeypatch.setattr(fp, "_get_pool", lambda w: fake)
    monkeypatch.setattr(torch_model, "create_live_features", lambda h: "INLINE")

    # A sibling symbol's future is still in flight on the shared pool.
    sibling = concurrent.futures.Future()
    fake.futures.append(sibling)

    # This symbol fails to serialize (a benign, per-symbol error).
    fake.next_exc = TypeError("cannot pickle this symbol's frame")
    result = asyncio.run(featurize(_make_hist()))

    assert result == "INLINE"  # this symbol degraded inline
    assert not sibling.cancelled()  # the HIGH bug: sibling MUST NOT be cancelled
    assert not sibling.done()  # still pending, its real vote will be cast
    assert fake.shutdown_calls == []  # pool NOT torn down on a benign per-symbol error
    assert fp._pool_broken is False


# ---- 6. Windows workers run windowless (pythonw) so they don't flash a console (#2424) --------------


def test_worker_executable_prefers_pythonw_on_windows(monkeypatch, tmp_path):
    (tmp_path / "python.exe").write_text("")
    (tmp_path / "pythonw.exe").write_text("")
    monkeypatch.setattr(fp.sys, "platform", "win32")
    monkeypatch.setattr(fp.sys, "executable", str(tmp_path / "python.exe"))
    assert fp._worker_executable(allow_pytest_override=True) == str(
        tmp_path / "pythonw.exe"
    )


def test_worker_executable_none_when_pythonw_missing(monkeypatch, tmp_path):
    (tmp_path / "python.exe").write_text("")  # no pythonw.exe alongside
    monkeypatch.setattr(fp.sys, "platform", "win32")
    monkeypatch.setattr(fp.sys, "executable", str(tmp_path / "python.exe"))
    assert fp._worker_executable() is None


def test_worker_executable_none_on_non_windows(monkeypatch):
    monkeypatch.setattr(fp.sys, "platform", "linux")
    assert fp._worker_executable() is None


# --- Orphan-reaping + RAM-fit (the 16 GB desktop-collapse follow-up) --------------------------------


def test_effective_workers_clamps_to_one_on_low_ram(monkeypatch):
    """<=16 GB machines get exactly 1 worker (2 torch workers add ~1 GB they can't spare); larger
    machines keep the configured count."""
    monkeypatch.setattr(fp, "_low_ram_machine", lambda: True)
    assert fp._effective_workers(4) == 1
    assert fp._effective_workers(2) == 1
    monkeypatch.setattr(fp, "_low_ram_machine", lambda: False)
    assert fp._effective_workers(4) == 4
    assert fp._effective_workers(0) == 1  # floor at 1


def test_low_ram_machine_threshold(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(fp, "_HAS_PSUTIL", True)
    monkeypatch.setattr(
        fp,
        "psutil",
        SimpleNamespace(virtual_memory=lambda: SimpleNamespace(total=16 * 1024**3)),
    )
    assert fp._low_ram_machine() is True  # 16 GiB -> constrained
    monkeypatch.setattr(
        fp,
        "psutil",
        SimpleNamespace(virtual_memory=lambda: SimpleNamespace(total=32 * 1024**3)),
    )
    assert fp._low_ram_machine() is False  # 32 GiB -> not constrained


def test_parent_gone_detects_dead_parent(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(fp, "_HAS_PSUTIL", True)
    monkeypatch.setattr(fp, "psutil", SimpleNamespace(pid_exists=lambda pid: True))
    assert fp._parent_gone(4242) is False  # parent alive
    monkeypatch.setattr(fp, "psutil", SimpleNamespace(pid_exists=lambda pid: False))
    assert fp._parent_gone(4242) is True  # parent gone -> worker should self-exit


def test_worker_init_arms_daemon_watchdog(monkeypatch):
    """The pool initializer starts a daemon parent-death watchdog thread when psutil is present."""
    rec = {}

    class FakeThread:
        def __init__(self, target, args, name, daemon):
            rec.update(target=target, args=args, name=name, daemon=daemon)

        def start(self):
            rec["started"] = True

    monkeypatch.setattr(fp, "_HAS_PSUTIL", True)
    monkeypatch.setattr(fp.threading, "Thread", FakeThread)
    fp._worker_init(9191)
    assert rec.get("started") is True
    assert rec["daemon"] is True
    assert rec["args"] == (9191,)
    assert rec["target"] is fp._parent_death_watch


def test_worker_init_noop_without_psutil(monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(fp, "_HAS_PSUTIL", False)
    monkeypatch.setattr(
        fp.threading, "Thread", lambda *a, **k: calls.__setitem__("n", calls["n"] + 1)
    )
    fp._worker_init(1)  # must not raise, must not spawn a thread
    assert calls["n"] == 0


def test_get_pool_applies_clamp_and_parent_watch_initializer(monkeypatch):
    """_get_pool builds the executor with the RAM-clamped worker count AND the orphan-reaping
    initializer — verified without spawning real processes."""
    captured = {}

    class FakeExecutor:
        def __init__(self, max_workers, mp_context, initializer, initargs):
            captured.update(
                max_workers=max_workers, initializer=initializer, initargs=initargs
            )

    monkeypatch.setattr(fp, "_pool", None, raising=False)
    monkeypatch.setattr(fp, "_low_ram_machine", lambda: True)  # force the clamp
    monkeypatch.setattr(fp.concurrent.futures, "ProcessPoolExecutor", FakeExecutor)
    try:
        fp._get_pool(4)
        assert captured["max_workers"] == 1  # clamped on <=16 GB
        assert captured["initializer"] is fp._worker_init
        assert isinstance(captured["initargs"][0], int)  # parent PID
    finally:
        fp._pool = None
