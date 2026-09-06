"""Windowless engine-port reaper for the desktop shell (console-window fix).

The Electron shell used to free the engine port and reap orphaned engines by
spawning
console-subsystem binaries — ``powershell`` / ``taskkill`` / ``netstat`` — from
``native-engine-manager.cjs``. On Windows each of those flashed a black
conhost window on
EVERY engine restart/stop/quit, because ``windowsHide`` is an unreliable
suppressor for
console processes (see ``desktop/electron/quiet-python.cjs``). The python
spawns were already
routed through the GUI-subsystem ``pythonw.exe`` (#2432); the console binaries
have no GUI twin
and were never covered — that is the "5 black cmd windows after an LLM switch"
the operator saw.

This module does the same job from ONE ``pythonw -m core.port_reaper`` call. A
GUI-subsystem
process can never allocate a console, so there is no window. It also replaces
the fragile
``netstat`` string parsing (the #2384 ``parseNetstatPids`` ReferenceError /
403-freeze class)
with psutil's structured lookup.

It imports only ``psutil`` + stdlib (``core/__init__.py`` is a 1-line comment,
so
``-m core.port_reaper`` stays cheap and never pulls in config/torch). Every
kill is best-effort
and the process always exits 0 — a reaper that raised would break the restart
it is serving.

Kills, in order (see :func:`select_targets`):
  1. ``--child-pid``     — our just-stopped engine, tree-killed
  unconditionally.
  2. ``--persisted-pid`` — our previous engine, orphaned by an unclean main
  death; tree-killed
                           ONLY when ownership is proven (a python*.exe whose
                           command line runs
                           ``core.engine``) so a recycled pid on the user's
                           own Python is safe.
  3. ``--port``          — whatever LISTENs on the engine port.
  4. ``--engine-path``   — stray python*/pythonw* under the install root
  (lost-parent backstop).
The reaper never kills its own pid or its parent (the Electron shell).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

# NOTE (OTel): this standalone reaper intentionally does NOT call
# core.telemetry_local /
# init_telemetry(). It is spawned by the shell for a sub-second, one-shot port
# reclaim BEFORE the
# engine boots; wiring up the OTel exporter here would add import/setup
# latency to the very restart
# path it serves and produce no useful spans (it exits immediately). Failures
# are surfaced instead
# via the JSON `warnings`/`error` summary on stdout (read + logged by native-
# engine-manager.cjs).

_PYTHON_NAMES = frozenset({"python.exe", "pythonw.exe", "python", "pythonw"})
# Our engine is always launched as `python -u -m core.engine` (native-engine-
# manager.cjs).
_CORE_ENGINE_RE = re.compile(r"\bcore\.engine\b", re.IGNORECASE)


def _cmdline_str(cmdline) -> str:
    if isinstance(cmdline, str):
        return cmdline
    if isinstance(cmdline, (list, tuple)):
        return " ".join(str(x) for x in cmdline)
    return ""


def _is_python_name(name) -> bool:
    return bool(name) and str(name).strip().lower() in _PYTHON_NAMES


def is_engine_process(name, cmdline) -> bool:
    """True iff a process is OUR engine: a python*/pythonw* interpreter
    whose command line runs ``core.engine``. The command-line proof is what
    makes reaping a persisted pid safe against pid
    recycling — a recycled pid now owning ``python train.py`` has no
    ``core.engine`` and is refused.
    Parity with ``orphan-reap.shouldReapPersistedPid`` on the JS side.
    """
    if not _is_python_name(name):
        return False
    return _CORE_ENGINE_RE.search(_cmdline_str(cmdline)) is not None


def _norm(path) -> str:
    return os.path.normcase(os.path.normpath(str(path))) if path else ""


def _under_root(exe, root_norm) -> bool:
    if not exe or not root_norm:
        return False
    return _norm(exe).startswith(root_norm)


def select_targets(
    procs,
    *,
    port_pids,
    child_pid,
    persisted_pid,
    engine_root,
    self_pid,
    parent_pid,
):
    """Pure decision core: given a process snapshot and parameters, return
    the ordered, de-duplicated list of pids to tree-kill. NEVER includes
    ``self_pid`` / ``parent_pid`` / pid 0 — the reaper is a pythonw under
    the install root and must not kill itself or the Electron shell.

    ``procs``: iterable of ``{"pid", "name", "exe", "cmdline"}`` dicts.
    ``port_pids``: pids holding the engine port (resolved via psutil).
    """
    by_pid = {}
    for p in procs:
        try:
            by_pid[int(p["pid"])] = p
        except (KeyError, TypeError, ValueError):
            continue

    protected = {int(self_pid), int(parent_pid), 0}
    out = []

    def add(pid):
        if pid is None:
            return
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            return
        if pid <= 0 or pid in protected or pid in out:
            return
        out.append(pid)

    # 1. our just-stopped engine — the shell vouches it is ours.
    add(child_pid)

    # 2. our orphaned previous engine — ownership-gated against pid recycling.
    if persisted_pid is not None:
        try:
            pp = int(persisted_pid)
        except (TypeError, ValueError):
            pp = None
        if pp is not None and pp != child_pid:
            proc = by_pid.get(pp)
            if proc and is_engine_process(proc.get("name"), proc.get("cmdline")):
                add(pp)

    # 3. whoever holds the engine port.
    for pid in port_pids or ():
        add(pid)

    # 4. stray engine pythons under the install root (backstop for lost-parent
    # orphans).
    root_norm = _norm(engine_root)
    if root_norm:
        for p in procs:
            if _is_python_name(p.get("name")) and _under_root(p.get("exe"), root_norm):
                add(p.get("pid"))

    return out


# ── Thin psutil I/O layer (safety logic lives in select_targets; the pure
# parts are unit-tested) ──
#
# POLICY-01: this is a best-effort reaper that must NEVER raise (it serves an
# engine restart), but a swallowed failure here is exactly what leaves :8001
# blocked ("Backend Offline") with no clue why. So every caught exception is
# APPENDED to a `warnings` list — surfaced in the stdout JSON summary, which
# native-engine-manager.cjs logs — instead of a silent `pass`.


def _dedupe(seq):
    seen = set()
    return [x for x in seq if not (x in seen or seen.add(x))]


def _port_owner_pids(port, warnings):
    """Pids with a connection whose local port == ``port`` (any state —
    matches the old ``netstat | findstr :port`` sweep). Best-effort; on a
    psutil error it records a warning and returns whatever it collected."""
    if not port:
        return set()
    import psutil

    pids = set()
    try:
        conns = psutil.net_connections(kind="inet")
    except Exception as exc:
        warnings.append(f"net_connections failed ({exc.__class__.__name__}: {exc})")
        return pids
    for c in conns:
        try:
            if c.laddr and c.laddr.port == port and c.pid:
                pids.add(int(c.pid))
        except Exception as exc:
            warnings.append(f"connection parse failed ({exc.__class__.__name__})")
            continue
    return pids


def _snapshot_procs(warnings):
    import psutil

    out = []
    try:
        it = psutil.process_iter(["pid", "name", "exe", "cmdline"])
    except Exception as exc:
        warnings.append(f"process_iter failed ({exc.__class__.__name__}: {exc})")
        return out
    for proc in it:
        try:
            info = proc.info
            out.append(
                {
                    "pid": info.get("pid"),
                    "name": info.get("name"),
                    "exe": info.get("exe") or "",
                    "cmdline": info.get("cmdline") or [],
                }
            )
        except Exception:
            # A process that vanished mid-iteration (NoSuchProcess) is normal
            # here — skip it without noise; a genuine reap failure is caught at
            # the _kill_tree layer instead.
            continue
    return out


def _kill_tree(pid, warnings):
    """Force-kill ``pid`` and its whole descendant tree (parity with
    ``taskkill /T /F``). Best-effort; returns the pids it ACTUALLY killed and
    records a warning for each failure so a blocked port is debuggable."""
    import psutil

    try:
        parent = psutil.Process(int(pid))
    except Exception as exc:
        warnings.append(f"pid {pid}: not reachable ({exc.__class__.__name__})")
        return []
    victims = []
    try:
        victims = list(parent.children(recursive=True))
    except Exception as exc:
        warnings.append(f"pid {pid}: children() failed ({exc.__class__.__name__})")
        victims = []
    victims.append(parent)
    killed = []
    for v in victims:
        try:
            v.kill()
            killed.append(v.pid)  # only report a pid we actually signalled
        except Exception as exc:
            warnings.append(
                f"pid {getattr(v, 'pid', '?')}: kill failed "
                f"({exc.__class__.__name__})"
            )
    try:
        psutil.wait_procs(victims, timeout=3)
    except Exception as exc:
        warnings.append(f"wait_procs failed ({exc.__class__.__name__})")
    return killed


def main(argv=None):
    parser = argparse.ArgumentParser(prog="core.port_reaper", add_help=True)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--child-pid", dest="child_pid", type=int, default=None)
    parser.add_argument("--persisted-pid", dest="persisted_pid", type=int, default=None)
    parser.add_argument("--engine-path", dest="engine_path", default=None)
    args = parser.parse_args(argv)

    warnings = []
    summary = {
        "reaped": [],
        "port": args.port,
        "error": None,
        "warnings": warnings,
    }
    try:
        port_pids = _port_owner_pids(args.port, warnings)
        procs = _snapshot_procs(warnings)
        targets = select_targets(
            procs,
            port_pids=port_pids,
            child_pid=args.child_pid,
            persisted_pid=args.persisted_pid,
            engine_root=args.engine_path,
            self_pid=os.getpid(),
            parent_pid=os.getppid(),
        )
        killed = []
        for pid in targets:
            killed.extend(_kill_tree(pid, warnings))
        # never let the reaper raise — it serves a restart
    except Exception as exc:
        summary["error"] = f"{exc.__class__.__name__}: {exc}"
    summary["warnings"] = _dedupe(warnings)

    payload = json.dumps(summary)
    try:
        sys.stdout.write(payload)
        sys.stdout.flush()
    except Exception as exc:
        # stdout unusable (pipe closed) → last-resort stderr so a
        # failed reclaim is still debuggable rather than vanishing. Guarded so
        # even this can never raise out of the reaper.
        try:
            sys.stderr.write(
                f"port_reaper: stdout write failed ({exc}); " f"summary={payload}\n"
            )
            sys.stderr.flush()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
