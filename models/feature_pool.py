"""Off-thread feature computation for the LIVE trading loop (event-loop-stall Fix A, #2499).

`create_live_features` is a pure but CPU-heavy pandas_ta transform run per symbol (~200-500/cycle) on
the engine's StrategyThread (core/engine/base.py). Because it is pure-Python pandas, it holds the one
process GIL for tenths of a second at a time and starves the uvicorn event loop on the main thread, so
every HTTP endpoint stalls for seconds (live telemetry 2026-07-26: /benchmark-equity median 14.9s, even
a no-op OPTIONS preflight 2.6s — while the real work, torch inference, is already off-loaded to a thread
and runs in 2ms). A *thread* pool does NOT help: pandas holds the GIL, so only a separate OS PROCESS —
with its own GIL — frees the server loop.

When FEATURE_POOL_ENABLED is on, `featurize` runs the transform in a spawn-based ProcessPoolExecutor.
The transform is deterministic and its input/output are plain DataFrames, so the result is byte-identical
across the process boundary — no trading decision changes (pinned by tests/unit/test_feature_pool.py).

Failure handling reproduces the INLINE semantics for the affected symbol ONLY and never disturbs the
sibling symbols evaluated concurrently on the shared pool in the same cycle:
  * `create_live_features` raising FeatureGenerationError (bad/thin data, its ADR-SEC-03 abstention
    signal) propagates UNCHANGED — the seam maps just that symbol to abstention, never a fabricated 0.0,
    and the pool is left intact.
  * A crashed worker (BrokenProcessPool) computes THIS symbol inline and LATCHES the pool off for the
    rest of the session (degrade to pure-inline = 0.3.1 behaviour) rather than re-spawning per symbol.
    Sibling futures are NEVER cancelled — each sibling on the broken pool independently falls back inline.
  * A symbol that cannot be serialized computes inline while the healthy pool keeps serving the others.

When the flag is off (the default), or on the backtest/SimulationAdapter path (offload=False, where a
worker process buys nothing — a backtest has no HTTP loop to unblock — and only adds IPC), `featurize`
is byte-identical to the previous inline call.
"""

import asyncio
import concurrent.futures
import logging
import multiprocessing
import os
import sys
import threading
import time
from concurrent.futures.process import BrokenProcessPool

try:
    import psutil  # optional — enables RAM-fit + parent-death orphan reaping

    _HAS_PSUTIL = True
except Exception:  # pragma: no cover — falls back to the Electron tree-kill when absent
    psutil = None
    _HAS_PSUTIL = False

logger = logging.getLogger(__name__)

# --- Orphan-reaping + RAM-fit for the spawn workers -------------------------------------------------
# Each spawn worker re-imports the full torch stack (~0.3-0.6 GB RSS) and, on Windows, can OUTLIVE a
# force-killed / crashed / OOM-killed parent engine (a `taskkill /F` WITHOUT `/T`, an internal
# force-stop, or a Windows OOM kill does not reap it). Unclean restarts then pile up orphan workers
# that exhaust RAM — a root cause of the 16 GB desktop collapse. Two mitigations below: clamp the
# worker count on RAM-constrained machines, and a per-worker parent-death watchdog.
_LOW_RAM_THRESHOLD_BYTES = (
    18 * 1024**3
)  # < 18 GiB total => a 16 GB (or smaller) machine
_PARENT_WATCH_INTERVAL_S = 5.0


def _low_ram_machine() -> bool:
    """True on a RAM-constrained (<=16 GB) machine. Fail-open (False) when psutil is unavailable."""
    if not _HAS_PSUTIL or psutil is None:
        return False
    try:
        return int(psutil.virtual_memory().total) < _LOW_RAM_THRESHOLD_BYTES
    except Exception:
        return False


def _effective_workers(workers: int) -> int:
    """Clamp the worker count to 1 on a RAM-constrained machine: two torch-loaded workers add ~1 GB
    that a 16 GB machine cannot spare on top of the engine + LLM server + browser. Larger machines
    keep the configured count."""
    return 1 if _low_ram_machine() else max(1, int(workers))


def _parent_gone(parent_pid: int) -> bool:
    """True when the parent engine PID no longer exists. Fail-safe False (assume alive) on any error
    so a probe glitch never kills a healthy worker."""
    if not _HAS_PSUTIL or psutil is None:
        return False
    try:
        return not psutil.pid_exists(parent_pid)
    except Exception:
        return False


def _parent_death_watch(parent_pid: int) -> None:
    """Daemon loop in each pool worker: hard-exit the worker once the parent engine PID is gone, so an
    orphaned torch worker can never linger and accumulate across unclean restarts. Reaps within
    ~5 s of parent death, regardless of HOW the parent died."""
    while True:
        if _parent_gone(parent_pid):
            os._exit(0)  # hard-exit: we are an orphan; skip atexit/flush
        time.sleep(_PARENT_WATCH_INTERVAL_S)


def _worker_init(parent_pid: int) -> None:
    """ProcessPoolExecutor initializer — runs once at the start of each worker process. Arms the
    parent-death watchdog (only when psutil is present; otherwise the Electron `/T` tree-kill is the
    backstop)."""
    if not _HAS_PSUTIL or psutil is None:
        return
    threading.Thread(
        target=_parent_death_watch,
        args=(parent_pid,),
        name="feature-pool-parent-watch",
        daemon=True,
    ).start()


# Module-global pool, created on first use. Guarded by _lock because it is reachable from BOTH the
# StrategyThread's event loop (featurize) and the MAIN uvicorn thread (shutdown_pool, via the FastAPI
# lifespan). Once a worker crashes we LATCH the pool off (degrade to pure-inline) for the rest of the
# process rather than thrashing spawn/import/kill per symbol.
_lock = threading.Lock()
_pool = None
_pool_broken = False  # latched True after a BrokenProcessPool; not rebuilt until the process restarts
_warned_offload_error = (
    False  # throttle the per-symbol serialization-fallback WARNING to once/session
)


def _worker_executable(allow_pytest_override: bool = False):
    """Interpreter to launch pool workers with, or None for the default.

    On Windows the engine runs HIDDEN (Electron spawns it with windowsHide), so it has no console. A
    worker launched with the console-subsystem `python.exe` would then get its OWN fresh console window
    — a visible flash on every spawn / every live⇄paper restart (regression of the #2424 windowsHide
    guarantee). `pythonw.exe` (bundled next to python.exe) is the windowless GUI-subsystem interpreter;
    it runs the create_live_features workload identically (verified) and pops no console. stderr is None
    under pythonw, which the transform tolerates (logging swallows emit errors). Non-Windows: no console
    concept — use the default.
    """
    if sys.platform != "win32":
        return None
    if not allow_pytest_override and "PYTEST_CURRENT_TEST" in os.environ:
        return None
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return pyw if os.path.exists(pyw) else None


def _get_pool(workers: int):
    """Return the shared spawn-based ProcessPoolExecutor, creating it on first use (thread-safe)."""
    global _pool
    with _lock:
        if _pool is None:
            # spawn on EVERY OS: the engine process is multithreaded (uvicorn) and torch-loaded, so a
            # fork-based pool (Linux default in 3.12) would fork-after-threads — a deadlock hazard.
            ctx = multiprocessing.get_context("spawn")
            exe = _worker_executable()
            if exe:
                ctx.set_executable(
                    exe
                )  # windowless workers on Windows (no console flash, #2424)
            _pool = concurrent.futures.ProcessPoolExecutor(
                max_workers=max(
                    1, _effective_workers(workers)
                ),  # clamp to 1 on <=16 GB machines
                mp_context=ctx,
                # arm the parent-death watchdog in every worker so an orphaned torch worker self-exits
                # if this engine dies (force-kill / crash / OOM), instead of piling up across restarts.
                initializer=_worker_init,
                initargs=(os.getpid(),),
            )
        return _pool


def _latch_broken_pool(broken) -> None:
    """A worker crashed: latch the pool off and drop the dead executor WITHOUT cancelling in-flight
    futures — sibling symbols on the same broken pool each raise BrokenProcessPool and fall back inline
    on their own, so no sibling's vote is collaterally cancelled."""
    global _pool, _pool_broken
    with _lock:
        _pool_broken = True
        if _pool is broken or _pool is None:
            _pool = None
    try:
        broken.shutdown(wait=False)  # NEVER cancel_futures here
    except Exception:
        pass


def shutdown_pool() -> None:
    """Tear the pool down on graceful engine shutdown (FastAPI lifespan). cancel_futures is safe here:
    the engine is stopping, so there is no live cycle whose sibling votes could be lost. No-op when the
    flag was never switched on (no pool was ever created)."""
    global _pool
    with _lock:
        pool, _pool = _pool, None
    if pool is not None:
        try:
            pool.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass


async def featurize(hist, offload: bool = True):
    """Compute create_live_features(hist), off-loaded to a worker process on the LIVE serving path.

    See the module docstring for the full failure taxonomy. The invariant: featurize returns/raises
    EXACTLY what the inline `create_live_features(hist)` would, for this symbol, without disturbing any
    sibling symbol — the only observable difference (flag ON) is that the GIL-heavy work runs in another
    process so the engine's HTTP event loop keeps its time-slices.
    """
    import config

    # Lazy import — keeps the torch chain off this module's import, mirroring the strategies' idiom.
    from models.torch_model import FeatureGenerationError, create_live_features

    # Read the flag via the module attribute (cheap on both editions — PEP-562 __getattr__ delegates to
    # the singleton; OSS reads a module global). NOT get_config(), whose OSS form rebuilds a
    # SimpleNamespace over every global on each call — unacceptable on a ~500x/cycle hot path.
    if (
        not offload
        or _pool_broken
        or not getattr(config, "FEATURE_POOL_ENABLED", False)
    ):
        return create_live_features(hist)

    workers = int(getattr(config, "FEATURE_POOL_WORKERS", 2) or 2)
    loop = asyncio.get_running_loop()
    pool = _get_pool(workers)
    try:
        return await loop.run_in_executor(pool, create_live_features, hist)
    except BrokenProcessPool as exc:
        _latch_broken_pool(pool)
        logger.warning(
            "feature_pool: worker pool crashed (%s) — degrading to inline feature computation for the "
            "rest of this session.",
            exc,
        )
        return create_live_features(hist)
    except FeatureGenerationError:
        # The worker's OWN ADR-SEC-03 abstention signal on bad/thin data. Propagate unchanged, exactly as
        # inline: the seam marks this symbol as abstention (never 0.0). Do NOT touch the shared pool.
        raise
    except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
        raise  # genuine cancellation / interpreter shutdown — never mask it
    except Exception as exc:
        # This one symbol could not cross the process boundary (serialization). The pool is healthy —
        # compute THIS symbol inline; leave the pool and every sibling future untouched. (If this were
        # somehow a business error, the inline recompute reproduces it identically.)
        global _warned_offload_error
        if not _warned_offload_error:
            _warned_offload_error = True
            logger.warning(
                "feature_pool: one symbol fell back to inline (%s: %s); further such fallbacks this "
                "session are logged at DEBUG.",
                type(exc).__name__,
                exc,
            )
        else:
            logger.debug(
                "feature_pool: inline fallback (%s: %s).", type(exc).__name__, exc
            )
        return create_live_features(hist)
