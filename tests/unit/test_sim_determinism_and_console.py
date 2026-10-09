# tests/unit/test_sim_determinism_and_console.py
# #2632 remaining items (P2b + P4) — the last two things between the Sim-Day harness and
# a usable A/B run. The blocker (P1, consensus -> order dispatch) is already fixed by
# `core/sim/runner._seed_lstm_panel` (PR #2633) and the specialist went offline in #2663.
#
# P2b — SIM network isolation: `core/latency_watchdog.py` ACTIVELY pings Alpaca every
#   `ping_interval_sec` from a background thread, and `core/engine/base.py` starts it inside
#   `start_live_strategy()` — which is exactly the entry point `core/sim/runner.py` uses.
#   Under SIM_MODE that means (a) a real socket per run (401 on paper-api/v2/clock, the
#   corpus is supposed to be the ONLY data source) and (b) non-determinism: a slow or
#   failing ping can trip the halt and change the run's outcome. An A/B whose two arms can
#   differ because of network weather is worthless. The watchdog must not start in SIM_MODE.
#
# P4 — Windows console: `core/sim/result.py` renders the summary/compare with U+2192 ("→").
#   On the operator's cp1252 console `print()` raises UnicodeEncodeError, so `--help` and the
#   report crash — the harness is unusable there. Render an ASCII arrow when the active
#   stdout encoding cannot represent the character; keep the nice arrow everywhere else.

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

# ---------------------------------------------------------------------------
# P2b — the watchdog must stay dormant under SIM_MODE
# ---------------------------------------------------------------------------


def _fresh_watchdog(monkeypatch):
    """A watchdog instance with the pytest short-circuit removed, so `start()` is
    actually exercised (the module guards on PYTEST_CURRENT_TEST)."""
    from core.latency_watchdog import LatencyWatchdog

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    return LatencyWatchdog()


def _arm_sim(monkeypatch, enabled: bool) -> None:
    import config

    monkeypatch.setattr(
        config, "get_config", lambda: SimpleNamespace(SIM_MODE=enabled), raising=False
    )


def test_p2b_watchdog_does_not_start_under_sim_mode(monkeypatch, caplog):
    """SIM_MODE -> no background thread, no socket. The corpus is the only data source."""
    import logging

    wd = _fresh_watchdog(monkeypatch)
    _arm_sim(monkeypatch, True)

    with caplog.at_level(logging.INFO):
        wd.start()

    assert wd._running is False, "the watchdog must not run under SIM_MODE"
    assert wd._thread is None, "no ping thread may be spawned under SIM_MODE"
    assert any(
        "sim" in rec.getMessage().lower() for rec in caplog.records
    ), "the skip must be stated, not silent (§5.6)"
    wd.stop()


def test_p2b_watchdog_still_starts_when_sim_mode_off(monkeypatch):
    """Live/paper path unchanged — the guard must be SIM-only, not a general kill."""
    wd = _fresh_watchdog(monkeypatch)
    _arm_sim(monkeypatch, False)
    monkeypatch.setattr(wd, "_ping_loop", lambda: None)  # no real socket in the test

    try:
        wd.start()
        assert wd._running is True
        assert wd._thread is not None
    finally:
        wd.stop()


def test_p2b_watchdog_start_fails_safe_to_live_on_broken_config(monkeypatch):
    """An unreadable config must NOT silently disable a safety component."""
    import config

    wd = _fresh_watchdog(monkeypatch)

    def _boom():
        raise ValueError("broken config")

    monkeypatch.setattr(config, "get_config", _boom, raising=False)
    monkeypatch.setattr(wd, "_ping_loop", lambda: None)

    try:
        wd.start()
        assert wd._running is True, "fail-safe direction is LIVE (watchdog on)"
    finally:
        wd.stop()


def test_p2b_env_var_alone_is_enough(monkeypatch):
    """The sim CLI sets AAA_SIM_MODE=true in the environment before importing config
    (core/sim/__main__.py). The guard must see that too, not only a patched config."""
    from core.latency_watchdog import LatencyWatchdog

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("AAA_SIM_MODE", "true")
    wd = LatencyWatchdog()
    wd.start()
    try:
        assert wd._running is False, "AAA_SIM_MODE=true must keep the watchdog dormant"
    finally:
        wd.stop()


# ---------------------------------------------------------------------------
# P4 — the console must not crash on a cp1252 terminal
# ---------------------------------------------------------------------------


def test_p4_arrow_helper_degrades_on_cp1252():
    """`_arrow()` returns the ASCII form when the target encoding cannot encode U+2192."""
    from core.sim.result import _arrow

    assert _arrow("cp1252") == "->"
    assert _arrow("utf-8") == "→"


def test_p4_summary_is_encodable_on_cp1252():
    """A rendered summary must survive `.encode('cp1252')` — that is exactly what
    `print()` does on the operator's console."""
    from core.sim.result import SimResult

    r = SimResult("2026-08-05", "09:30-16:00 ET", "15m")
    r.starting_equity = 100_000.0
    r.ending_equity = 100_500.0
    text = r.summary(encoding="cp1252")
    text.encode("cp1252")  # must not raise


def test_p4_compare_is_encodable_on_cp1252():
    from core.sim.result import SimResult, compare

    a = SimResult("2026-08-05", "09:30-16:00 ET", "15m")
    b = SimResult("2026-08-05", "09:30-16:00 ET", "15m")
    a.starting_equity = b.starting_equity = 100_000.0
    a.ending_equity, b.ending_equity = 100_100.0, 99_900.0
    text = compare(a, b, encoding="cp1252")
    text.encode("cp1252")  # must not raise


def test_p4_utf8_output_keeps_the_real_arrow():
    """No cosmetic regression where the console can handle it."""
    from core.sim.result import SimResult

    r = SimResult("2026-08-05", "09:30-16:00 ET", "15m")
    r.starting_equity = 100_000.0
    r.ending_equity = 100_500.0
    assert "→" in r.summary(encoding="utf-8")


def test_p4_default_encoding_is_taken_from_stdout(monkeypatch):
    """No explicit encoding -> read it off stdout, so plain `print(r.summary())` is safe."""
    from core.sim.result import SimResult

    r = SimResult("2026-08-05", "09:30-16:00 ET", "15m")
    r.starting_equity = 100_000.0
    r.ending_equity = 100_010.0
    monkeypatch.setattr("sys.stdout", SimpleNamespace(encoding="cp1252"))
    r.summary().encode("cp1252")  # must not raise


def test_p4_missing_stdout_encoding_falls_back_to_ascii_safe(monkeypatch):
    """A captured/pipe stdout can report encoding=None — must not crash."""
    from core.sim.result import SimResult

    r = SimResult("2026-08-05", "09:30-16:00 ET", "15m")
    r.starting_equity = 100_000.0
    r.ending_equity = 100_010.0
    monkeypatch.setattr("sys.stdout", SimpleNamespace(encoding=None))
    assert isinstance(r.summary(), str)


@pytest.mark.parametrize("enc", ["cp1252", "cp850", "ascii"])
def test_p4_no_non_encodable_char_survives(enc):
    """Belt-and-braces over the narrow codepages a Windows console can present."""
    from core.sim.result import SimResult, compare

    r = SimResult("2026-08-05", "09:30-16:00 ET", "15m")
    r.starting_equity, r.ending_equity = 100_000.0, 99_000.0
    r.summary(encoding=enc).encode(enc)
    compare(r, r, encoding=enc).encode(enc)


def test_p2b_sim_runner_documents_the_isolation():
    """The runner must state that the watchdog is isolated in sim — a reader of the
    harness has to be able to trust the offline contract without grepping."""
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2] / "core" / "sim" / "runner.py"
    ).read_text(encoding="utf-8")
    assert (
        "latency" in src.lower()
    ), "core/sim/runner.py must document the LatencyWatchdog isolation (#2632 P2b)"


def test_p2b_env_precedence_is_documented():
    """os.environ is the sim CLI's channel; keep that seam explicit.

    It now lives in the SHARED definition (``core/sim/__init__.py::is_sim_mode``) instead
    of being re-implemented per component — the news producer became the second consumer
    (#2632), so the check was consolidated. The guard follows the code: assert the env
    seam is documented where it is read, and that the watchdog delegates there.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    shared = (root / "core" / "sim" / "__init__.py").read_text(encoding="utf-8")
    assert "AAA_SIM_MODE" in shared, "the env seam must be named in the shared guard"

    watchdog = (root / "core" / "latency_watchdog.py").read_text(encoding="utf-8")
    assert (
        "from core.sim import is_sim_mode" in watchdog
    ), "the watchdog must delegate to the shared definition, not re-implement it"
    assert os.environ is not None
