# tests/unit/test_port_reaper.py
"""The desktop shell used to free the engine port + reap orphans with
console-subsystem spawns (powershell/taskkill/netstat) — each flashed a
black conhost window on every restart because `windowsHide` is unreliable for
console binaries. `core.port_reaper` does the same job from ONE windowless
`pythonw -m core.port_reaper` (psutil) call. These tests lock the SAFETY core
— the pure target-selection — so a regression can't (a) miss the port owner,
(b) kill a recycled pid on the user's own Python, or (c) kill the reaper itself
/ the Electron shell.
"""

import json

from core.port_reaper import _kill_tree, is_engine_process, main, select_targets

# A pid so high it can never be a live process → psutil.Process raises → a
# deterministic
# "kill failed / not found" path for the error-surfacing tests (POLICY-01).
_MISSING_PID = 2147483647


def _p(pid, name, exe="", cmdline=None):
    return {"pid": pid, "name": name, "exe": exe, "cmdline": cmdline or []}


# ── is_engine_process (ownership proof) ─────────────────────────────────────


def test_is_engine_process_true_for_python_running_core_engine():
    assert (
        is_engine_process("python.exe", ["python", "-u", "-m", "core.engine"]) is True
    )
    assert (
        is_engine_process("pythonw.exe", ["pythonw", "-u", "-m", "core.engine"]) is True
    )


def test_is_engine_process_false_for_recycled_or_unrelated():
    # A recycled pid now owned by the user's own Python (no core.engine) —
    # must be refused.
    assert is_engine_process("python.exe", ["python", "train.py"]) is False
    # Not a python interpreter at all.
    assert is_engine_process("notepad.exe", ["notepad", "core.engine"]) is False
    # Missing data.
    assert is_engine_process(None, ["python", "-m", "core.engine"]) is False
    assert is_engine_process("python.exe", []) is False


# ── select_targets ──────────────────────────────────────────────────────────


def test_child_pid_is_always_selected():
    got = select_targets(
        [],
        port_pids=set(),
        child_pid=111,
        persisted_pid=None,
        engine_root=None,
        self_pid=1,
        parent_pid=2,
    )
    assert got == [111]


def test_persisted_pid_selected_only_with_ownership_proof():
    procs = [
        _p(
            200,
            "python.exe",
            "C:/app/python.exe",
            ["python", "-m", "core.engine"],
        )
    ]
    got = select_targets(
        procs,
        port_pids=set(),
        child_pid=None,
        persisted_pid=200,
        engine_root=None,
        self_pid=1,
        parent_pid=2,
    )
    assert 200 in got


def test_persisted_pid_refused_when_recycled():
    # engine.pid points at a pid now owned by an unrelated python (no
    # core.engine) → NOT killed.
    procs = [
        _p(
            200,
            "python.exe",
            "C:/x/python.exe",
            ["python", "notebook.py"],
        )
    ]
    got = select_targets(
        procs,
        port_pids=set(),
        child_pid=None,
        persisted_pid=200,
        engine_root=None,
        self_pid=1,
        parent_pid=2,
    )
    assert 200 not in got


def test_port_owners_are_selected():
    got = select_targets(
        [],
        port_pids={555, 556},
        child_pid=None,
        persisted_pid=None,
        engine_root=None,
        self_pid=1,
        parent_pid=2,
    )
    assert set(got) == {555, 556}


def test_sweep_selects_engine_pythons_under_root_only():
    procs = [
        _p(
            10,
            "python.exe",
            "C:/app/autonomous/py/python.exe",
            ["python", "-m", "core.engine"],
        ),
        _p(
            11,
            "pythonw.exe",
            "C:/app/autonomous/py/pythonw.exe",
            ["pythonw", "worker"],
        ),
        _p(
            12,
            "python.exe",
            "D:/other/python.exe",
            ["python", "-m", "core.engine"],
        ),  # outside root
        _p(
            13,
            "chrome.exe",
            "C:/app/autonomous/chrome.exe",
            ["chrome"],
        ),  # not python
    ]
    got = select_targets(
        procs,
        port_pids=set(),
        child_pid=None,
        persisted_pid=None,
        engine_root="C:/app/autonomous",
        self_pid=1,
        parent_pid=2,
    )
    assert set(got) == {10, 11}


def test_never_selects_self_or_parent_or_system():
    # Even if self/parent/pid-0 would match a filter, they must never be
    # killed.
    procs = [
        _p(
            99,
            "pythonw.exe",
            "C:/app/autonomous/pythonw.exe",
            ["pythonw", "-m", "core.port_reaper"],
        ),
        _p(
            42,
            "python.exe",
            "C:/app/autonomous/python.exe",
            ["python", "-m", "core.engine"],
        ),
    ]
    got = select_targets(
        procs,
        port_pids={99, 42, 0},
        child_pid=99,
        persisted_pid=42,
        engine_root="C:/app/autonomous",
        self_pid=99,
        parent_pid=42,
    )
    assert 99 not in got  # self
    assert 42 not in got  # parent
    assert 0 not in got  # system


def test_result_is_deduplicated():
    procs = [
        _p(
            77,
            "python.exe",
            "C:/app/autonomous/python.exe",
            ["python", "-m", "core.engine"],
        )
    ]
    got = select_targets(
        procs,
        port_pids={77},
        child_pid=77,
        persisted_pid=77,
        engine_root="C:/app/autonomous",
        self_pid=1,
        parent_pid=2,
    )
    assert got.count(77) == 1


# ── error surfacing (POLICY-01: no silent failures) ─────────────────────────


def test_kill_tree_surfaces_a_warning_when_the_pid_is_gone():
    # A failed kill must NOT be swallowed silently — it is appended to
    # `warnings` so a blocked port can be debugged, and it is NOT reported
    # as reaped.
    warnings = []
    killed = _kill_tree(_MISSING_PID, warnings)
    assert killed == [], "a pid that cannot be killed must not be reported as reaped"
    assert warnings, "a failed kill must surface a warning, never pass silently"


def test_main_summary_carries_warnings_for_a_failed_kill(capsys):
    # End-to-end: main() prints a JSON summary whose `warnings` surfaces the
    # failed kill (POLICY-01) instead of exiting 0 with no trace. port 0 → no
    # port scan; no sweep root.
    rc = main(["--child-pid", str(_MISSING_PID), "--port", "0"])
    assert rc == 0  # best-effort: the reaper never fails the restart it serves
    summary = json.loads(capsys.readouterr().out)
    assert summary["reaped"] == []
    assert summary["error"] is None
    assert summary["warnings"], "a failed kill must appear in the summary warnings"
