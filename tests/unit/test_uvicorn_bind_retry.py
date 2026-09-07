# tests/unit/test_uvicorn_bind_retry.py
# INC-1a (#2215, plan #2213) — TDD for the desktop restart bind-race fix.
# LSR R3 (#2254, plan #2251 §R3) — real uvicorn catches the bind OSError itself
# (site-packages/uvicorn/server.py:179-182) and does sys.exit(1); a SystemExit(1)
# then escapes run_with_bind_retry uncaught → engine exit(1) → restart-storm → paper.
# The real fix catches SystemExit(1) and retries only when the port is still busy
# (a BIND probe, never connect — a TIME_WAIT port refuses connect yet is unbindable).
from __future__ import annotations

import errno
import logging
import socket

import pytest

from core.engine.uvicorn_boot import _probe_bind, run_with_bind_retry


def _addr_in_use(win: bool = False) -> OSError:
    e = OSError()
    if win:
        e.errno = 10048  # WSAEADDRINUSE
        e.winerror = 10048
    else:
        e.errno = errno.EADDRINUSE
    return e


# --- existing OSError branch (probe/port_busy injected as no-ops) -----------------


def test_retries_on_address_in_use_then_succeeds():
    calls = {"n": 0}
    slept: list[float] = []

    def run(app, host, port):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise _addr_in_use()
        return "served"

    r = run_with_bind_retry(
        "app",
        "127.0.0.1",
        8001,
        run=run,
        sleep=slept.append,
        base_delay=0.1,
        probe=lambda *_: None,
        port_busy=lambda *_: True,
    )
    assert r == "served"
    assert calls["n"] == 3  # 2 failed binds + 1 success
    assert len(slept) == 2  # backoff before each retry


def test_windows_10048_is_treated_as_address_in_use():
    calls = {"n": 0}

    def run(app, host, port):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _addr_in_use(win=True)
        return "ok"

    r = run_with_bind_retry(
        "a",
        "h",
        1,
        run=run,
        sleep=lambda *_: None,
        base_delay=0.01,
        probe=lambda *_: None,
        port_busy=lambda *_: True,
    )
    assert r == "ok"
    assert calls["n"] == 2


def _span_recorder(monkeypatch):
    """Patch uvicorn_boot.get_tracer with a recorder; returns the captured [(name, attrs)] list."""
    import core.engine.uvicorn_boot as ub

    spans = []

    class _Span:
        def __init__(self, name):
            self._a = {}
            spans.append((name, self._a))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def set_attribute(self, k, v):
            self._a[k] = v

    class _Tracer:
        def start_as_current_span(self, name, **kw):
            return _Span(name)

    monkeypatch.setattr(ub, "get_tracer", lambda *_a, **_k: _Tracer())
    return spans


def test_bind_retry_emits_otel_span_on_recoverable_race(monkeypatch):
    # #2277 INC-3 (§4b R3): the recover-THEN-succeed bind race must emit a structured OTEL span
    # (engine_bind_retry) so it reaches telemetry.jsonl — not only a logger.warning that surfaces
    # only if the engine exits.
    spans = _span_recorder(monkeypatch)
    calls = {"n": 0}

    def run(app, host, port):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _addr_in_use(win=True)
        return "served"

    r = run_with_bind_retry(
        "a",
        "127.0.0.1",
        8001,
        run=run,
        sleep=lambda *_: None,
        base_delay=0.01,
        probe=lambda *_: None,
        port_busy=lambda *_: True,
    )
    assert r == "served"
    retry = [a for (n, a) in spans if n == "engine_bind_retry"]
    assert retry, "engine_bind_retry span must be emitted on a recoverable bind race"
    assert retry[0].get("port") == 8001
    assert retry[0].get("cause") == "oserror"
    assert retry[0].get("winerror") == 10048


def test_bind_exhausted_emits_otel_span(monkeypatch):
    # #2277 INC-3: once the attempt cap is hit, engine_bind_exhausted is emitted as a span too.
    spans = _span_recorder(monkeypatch)

    def run(app, host, port):
        raise _addr_in_use()

    with pytest.raises(OSError):
        run_with_bind_retry(
            "a",
            "127.0.0.1",
            8080,
            run=run,
            sleep=lambda *_: None,
            max_attempts=3,
            base_delay=0.01,
            probe=lambda *_: None,
            port_busy=lambda *_: True,
        )
    exhausted = [a for (n, a) in spans if n == "engine_bind_exhausted"]
    assert exhausted, "engine_bind_exhausted span must be emitted at the cap"
    assert exhausted[0].get("port") == 8080
    assert exhausted[0].get("attempts") == 3
    assert exhausted[0].get("last_cause") == "oserror"


def test_non_address_error_is_reraised_immediately():
    calls = {"n": 0}

    def run(app, host, port):
        calls["n"] += 1
        raise OSError(errno.EACCES, "permission denied")

    with pytest.raises(OSError):
        run_with_bind_retry(
            "a",
            "h",
            1,
            run=run,
            sleep=lambda *_: None,
            probe=lambda *_: None,
            port_busy=lambda *_: True,
        )
    assert calls["n"] == 1  # non-bind error must NOT retry


def test_reraises_after_max_attempts():
    calls = {"n": 0}

    def run(app, host, port):
        calls["n"] += 1
        raise _addr_in_use()

    with pytest.raises(OSError):
        run_with_bind_retry(
            "a",
            "h",
            1,
            run=run,
            sleep=lambda *_: None,
            max_attempts=4,
            base_delay=0.01,
            probe=lambda *_: None,
            port_busy=lambda *_: True,
        )
    assert calls["n"] == 4  # a genuinely stuck port still surfaces after the cap


# --- pre-bind probe optimisation (real socket) -----------------------------------


def test_real_listen_socket_blocks_probe_then_frees_and_run_invoked_once():
    lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    lsock.bind(("127.0.0.1", 0))
    port = lsock.getsockname()[1]
    lsock.listen(1)

    slept: list[float] = []

    def sleep(delay):
        slept.append(delay)
        if len(slept) == 2:  # release the port during the 2nd backoff
            lsock.close()

    run_calls = {"n": 0}

    def run(app, host, port):
        run_calls["n"] += 1
        return "served"

    try:
        r = run_with_bind_retry(
            "app",
            "127.0.0.1",
            port,
            run=run,
            sleep=sleep,
            base_delay=0.01,
            probe=_probe_bind,  # real throwaway bind
            port_busy=lambda *_: False,
        )
    finally:
        try:
            lsock.close()
        except OSError:
            pass

    assert r == "served"
    assert run_calls["n"] == 1  # run only after the probe could bind
    assert len(slept) == 2  # two backoffs while the listener held the port


# --- SystemExit(1) guard (the real fix) ------------------------------------------


def test_systemexit1_port_busy_retries_then_succeeds(caplog):
    calls = {"n": 0}
    slept: list[float] = []

    def run(app, host, port):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise SystemExit(1)  # mimic uvicorn Server.startup -> sys.exit(1)
        return "served"

    with caplog.at_level(logging.WARNING):
        r = run_with_bind_retry(
            "a",
            "127.0.0.1",
            1,
            run=run,
            sleep=slept.append,
            base_delay=0.01,
            probe=lambda *_: None,
            port_busy=lambda *_: True,
        )
    assert r == "served"
    assert calls["n"] == 3
    assert len(slept) == 2
    retries = [
        r for r in caplog.records if getattr(r, "metric", None) == "engine_bind_retry"
    ]
    assert retries and all(r.cause == "systemexit" for r in retries)


def test_systemexit1_port_free_reraises_immediately():
    calls = {"n": 0}
    slept: list[float] = []

    def run(app, host, port):
        calls["n"] += 1
        raise SystemExit(1)

    with pytest.raises(SystemExit):
        run_with_bind_retry(
            "a",
            "127.0.0.1",
            1,
            run=run,
            sleep=slept.append,
            probe=lambda *_: None,
            port_busy=lambda *_: False,  # port free ⇒ not a bind race
        )
    assert calls["n"] == 1  # genuine exit(1), no retry
    assert slept == []


def test_systemexit_non_one_reraised_without_consulting_port_busy():
    pb_calls = {"n": 0}

    def port_busy(host, port):
        pb_calls["n"] += 1
        return True

    for code in (2, "boom"):

        def run(app, host, port, _c=code):
            raise SystemExit(_c)

        with pytest.raises(SystemExit):
            run_with_bind_retry(
                "a",
                "127.0.0.1",
                1,
                run=run,
                sleep=lambda *_: None,
                probe=lambda *_: None,
                port_busy=port_busy,
            )
    assert pb_calls["n"] == 0  # exit(2)/exit('boom') are genuine — never retried


def test_systemexit1_busy_reraises_after_max_attempts(caplog):
    calls = {"n": 0}

    def run(app, host, port):
        calls["n"] += 1
        raise SystemExit(1)

    with caplog.at_level(logging.WARNING):
        with pytest.raises(SystemExit):
            run_with_bind_retry(
                "a",
                "127.0.0.1",
                1,
                run=run,
                sleep=lambda *_: None,
                max_attempts=3,
                base_delay=0.01,
                probe=lambda *_: None,
                port_busy=lambda *_: True,
            )
    assert calls["n"] == 3  # capped
    assert any(
        getattr(r, "metric", None) == "engine_bind_exhausted"
        and getattr(r, "last_cause", None) == "systemexit"
        for r in caplog.records
    )
