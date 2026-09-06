# core/engine/uvicorn_boot.py
# INC-1a (#2215, plan docs/desktop-live-autonomy) — bind-race-hardened uvicorn launch.
#
# Desktop restart race: the shell's Listen-state port probe (#2141/#2143,
# native-engine-manager.cjs) reclaims a *listening* socket, but after a taskkill the
# port lingers briefly in TIME_WAIT — no longer LISTEN, yet not bindable. The probe
# then classifies "spawn", the fresh engine calls uvicorn's bind(), and it fails with
# WinError 10048 → the process exits(1) → the shell restart-storms → recovers on PAPER.
# (Verified: 10048 recurs on 0.1.18, i.e. WITH #2143.)
#
# LSR R3 (#2254, plan #2251 §R3) — THE REAL FIX. Real uvicorn catches the bind OSError
# itself (site-packages/uvicorn/server.py:179-182: `except OSError: logger.error(exc);
# await self.lifespan.shutdown(); sys.exit(1)`), so an address-in-use bind never surfaces
# as an OSError here — it surfaces as `SystemExit(1)` (a BaseException, not OSError) and
# escapes the OSError-only handler uncaught → engine exit(1) → restart-storm → paper.
# run_with_bind_retry therefore also catches SystemExit(1) and retries it, but ONLY when
# the port is still busy (a BIND probe — never connect: a TIME_WAIT port refuses connect
# yet is unbindable, so a connect-based check would falsely report it free). A genuine
# exit (code != 1, or a free port) is re-raised immediately; after the attempt cap the
# last cause is re-raised (a genuinely stuck port must still surface).
from __future__ import annotations

import errno
import logging
import socket
import sys
import time
from typing import Any, Callable, Optional

from core.telemetry import get_tracer

logger = logging.getLogger(__name__)

_TRACER_NAME = "uvicorn-boot"


def _emit_bind_span(name: str, attrs: dict) -> None:
    """Best-effort OTEL span so the bind-race states reach ``telemetry.jsonl`` (#2277 INC-3, §4b R3).

    The recover-then-succeed path was previously observable ONLY via ``logger.warning`` (kept), so a
    bind race that recovered left no structured trace in the diagnose-export. Never raises —
    telemetry must not break the boot.
    """
    try:
        with get_tracer(_TRACER_NAME).start_as_current_span(name) as span:
            for k, v in attrs.items():
                if v is not None:
                    span.set_attribute(k, v)
    except Exception as exc:  # noqa: BLE001 — telemetry must never break the boot
        logger.debug("bind-retry telemetry emit skipped: %s", exc)


# Windows sockets report WSAEADDRINUSE (10048); Python may surface it as .errno and/or
# .winerror. POSIX uses errno.EADDRINUSE. Treat both as the retryable bind race.
_WSAEADDRINUSE = 10048
_ADDR_IN_USE_ERRNOS = {errno.EADDRINUSE, _WSAEADDRINUSE}


def _is_address_in_use(exc: OSError) -> bool:
    return (
        getattr(exc, "errno", None) in _ADDR_IN_USE_ERRNOS
        or getattr(exc, "winerror", None) == _WSAEADDRINUSE
    )


def _probe_bind(host: str, port: int) -> None:
    """Throwaway bind that mirrors uvicorn's socket options EXACTLY, so a busy port is
    detected up front (before the real, blocking ``run``).

    uvicorn/asyncio create the listening socket via ``loop.create_server`` which on POSIX
    sets ``SO_REUSEADDR`` but on Windows does NOT (a Windows TIME_WAIT port must stay
    unbindable). We copy that so the probe's verdict matches the real bind. Raises the
    bind ``OSError`` (incl. address-in-use) to the caller; always closes the socket.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if sys.platform != "win32":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
    finally:
        sock.close()


def _port_busy(host: str, port: int) -> bool:
    """True iff ``port`` is currently unbindable (address-in-use).

    This MUST be a BIND probe, not a ``connect``: a socket in TIME_WAIT refuses ``connect``
    yet is not bindable, so a connect-based check would falsely report the port free and
    make the SystemExit(1) guard re-raise a genuine bind race.
    """
    try:
        _probe_bind(host, port)
    except OSError as exc:
        return _is_address_in_use(exc)
    return False


def _retry_or_exhaust(
    port: int,
    attempt: int,
    max_attempts: int,
    base_delay: float,
    max_delay: float,
    sleep: Callable[[float], Any],
    cause: str,
    winerror: Optional[int],
) -> bool:
    """Emit OTEL and either back off (return True → caller retries) or report exhaustion
    (return False → caller re-raises the current exception).

    OTEL: ``engine_bind_retry{attempt,port,winerror,cause}`` on each backoff and
    ``engine_bind_exhausted{port,attempts,last_cause}`` once the cap is hit. The engine
    only has ``logging`` wired here, so events are structured log records (``extra=``) that
    the diagnostics export picks up — matching the module's existing style.
    """
    if attempt >= max_attempts:
        logger.warning(
            "engine_bind_exhausted: port %s still unbindable after %d attempts "
            "(last_cause=%s) — re-raising (a stray engine may be holding it).",
            port,
            attempt,
            cause,
            extra={
                "metric": "engine_bind_exhausted",
                "port": port,
                "attempts": attempt,
                "last_cause": cause,
            },
        )
        # #2277 INC-3 (§4b R3): structured OTEL alongside the log so the exhausted state lands in
        # telemetry.jsonl even when the engine re-raises and exits.
        _emit_bind_span(
            "engine_bind_exhausted",
            {"port": port, "attempts": attempt, "last_cause": cause},
        )
        return False
    delay = min(base_delay * attempt, max_delay)
    logger.warning(
        "engine_bind_retry: bind on :%s failed (cause=%s — likely TIME_WAIT after an "
        "engine restart); retry %d/%d in %.1fs.",
        port,
        cause,
        attempt,
        max_attempts,
        delay,
        extra={
            "metric": "engine_bind_retry",
            "attempt": attempt,
            "port": port,
            "winerror": winerror,
            "cause": cause,
        },
    )
    # #2277 INC-3 (§4b R3): emit a span on EACH backoff so a bind race that RECOVERS (never exits)
    # is still reconstructable from the diagnose-export — the gap the audit flagged (R3).
    _emit_bind_span(
        "engine_bind_retry",
        {"attempt": attempt, "port": port, "winerror": winerror, "cause": cause},
    )
    sleep(delay)
    return True


def run_with_bind_retry(
    app: Any,
    host: str,
    port: int,
    *,
    run: Optional[Callable[..., Any]] = None,
    sleep: Callable[[float], Any] = time.sleep,
    max_attempts: int = 6,
    base_delay: float = 0.5,
    max_delay: float = 3.0,
    probe: Optional[Callable[[str, int], Any]] = None,
    port_busy: Optional[Callable[[str, int], bool]] = None,
) -> Any:
    """Launch uvicorn, retrying ONLY on an address-in-use bind race (TIME_WAIT).

    ``run`` defaults to ``uvicorn.run`` (imported lazily so this module is importable —
    and unit-testable — without uvicorn). Blocks like ``uvicorn.run`` on success.

    The race surfaces two ways and both are retried while the port stays busy:
      * ``OSError`` (EADDRINUSE / WinError 10048) — the pre-bind probe, or a ``run`` that
        does not swallow the bind error; and
      * ``SystemExit(1)`` — real uvicorn catches the bind ``OSError`` and ``sys.exit(1)``
        (server.py:179-182). Any other exit code (or a str message) is a genuine exit and
        is re-raised unchanged; ``SystemExit(1)`` with a now-free port is likewise genuine.

    ``probe``/``port_busy`` default to the internal socket helpers and are injectable for
    tests.
    """
    if run is None:  # pragma: no cover - trivial lazy default
        import uvicorn

        run = uvicorn.run
    if probe is None:
        probe = _probe_bind
    if port_busy is None:
        port_busy = _port_busy

    attempt = 0
    while True:
        attempt += 1

        # 1. Pre-bind probe (optimisation): catch a busy port before the blocking run.
        try:
            probe(host, port)
        except OSError as exc:
            if not _is_address_in_use(exc):
                raise  # not the bind race — surface immediately
            if not _retry_or_exhaust(
                port,
                attempt,
                max_attempts,
                base_delay,
                max_delay,
                sleep,
                "probe",
                getattr(exc, "winerror", None),
            ):
                raise
            continue

        # 2. Real bind — blocks on success (unchanged).
        try:
            return run(app, host=host, port=port)
        except OSError as exc:
            if not _is_address_in_use(exc):
                raise  # not the bind race — surface immediately
            if not _retry_or_exhaust(
                port,
                attempt,
                max_attempts,
                base_delay,
                max_delay,
                sleep,
                "oserror",
                getattr(exc, "winerror", None),
            ):
                raise
            continue
        except SystemExit as exc:
            # THE REAL FIX: real uvicorn catches the bind OSError itself and sys.exit(1).
            if exc.code != 1:
                raise  # exit(2)/exit('boom') = genuine — never a bind race
            if not port_busy(host, port):
                raise  # port is free now ⇒ not a bind race ⇒ genuine exit(1)
            if not _retry_or_exhaust(
                port,
                attempt,
                max_attempts,
                base_delay,
                max_delay,
                sleep,
                "systemexit",
                None,
            ):
                raise
            continue
