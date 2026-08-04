"""LSR R2 (#2253): core/live_worm.py — the compensating WORM-``disable`` self-heal.

A live boot that degrades TERMINALLY (e.g. Alpaca 401 on live creds) must append a compensating
``disable`` to the SAME Art-14 SHA-256 hash chain as ``/api/live/enable`` — so the desktop shell's
``verifyAuditChain`` reads live as REVOKED on the next boot and re-arms PAPER until the operator
deliberately re-enables. Python OWNS the WORM write (BORA — ``audit-chain.cjs`` stays read-only).

Two entrypoints, ONE implementation (both demand a DURABLE write so a swallowed sink failure is
never mistaken for a written disable):
  * ``revoke_live(...)``            — in-process for the boot-degrade branch in
                                      ``core/engine/__main__.py``; never raises but returns False
                                      when the disable did not durably land.
  * ``python -m core.live_worm revoke`` — standalone CLI (exit 0/1) for the shell's boot-start-retry
                                      and hard-boot-crash compensation (FastAPI dead → no HTTP API).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Point the WORM chain at a writable tmp dir and force a FRESH fallback audit logger.

    ``hitl_gate._resolve_audit_logger`` caches a process-wide ``LocalJSONAuditLogger`` singleton
    that keys on ``SENATE_LOG_DIR`` at construction — reset it so each test writes to its own dir
    (mirrors a fresh CLI process). Also isolates the round-table runner so the fallback path wins.
    """
    monkeypatch.setenv("AAA_USER_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SENATE_LOG_DIR", str(tmp_path))
    from core import hitl_gate

    monkeypatch.setattr(hitl_gate, "_fallback_audit_logger", None, raising=False)
    with patch("core.round_table.runner.get_audit_logger", return_value=None):
        yield tmp_path


def _entries(d: Path):
    out = []
    for f in sorted(Path(d).glob("audit_log_*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


def _live_events(d: Path):
    return [e for e in _entries(d) if e.get("event_type") == "live_enablement"]


# ── revoke_live() — in-process (a) ────────────────────────────────────────────


def test_revoke_live_writes_disable_to_worm_chain(env):
    from core.live_worm import revoke_live

    ok = revoke_live("degraded-live: Alpaca 401 (terminal)")
    assert ok is True

    live = _live_events(env)
    assert len(live) == 1
    e = live[0]
    assert e["action"] == "disable"
    assert e["actor"] == "system:boot-degrade"
    assert e["acknowledgment"] == "degraded-live: Alpaca 401 (terminal)"
    # on the tamper-evident chain, string-only (float-free) preimage
    assert "prev_hash" in e and "hash" in e
    for k in ("timestamp", "actor", "action", "acknowledgment", "nonce"):
        assert isinstance(e[k], str)


def test_revoke_live_honours_custom_actor(env):
    from core.live_worm import revoke_live

    revoke_live("boot crash", actor="shell:boot-crash")
    assert _live_events(env)[0]["actor"] == "shell:boot-crash"


def test_revoke_live_stamps_switch_id_from_env_on_worm_and_span(env, monkeypatch):
    # #2277 INC-1: the compensating disable inherits AAA_SWITCH_ID (the enable's switch_id) so a
    # TERMINAL degrade joins back to the enable that armed it — on the WORM record AND the span.
    monkeypatch.setenv("AAA_SWITCH_ID", "sw-boot-42")
    import core.live_worm as live_worm

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

    tracer = MagicMock()
    tracer.start_as_current_span = lambda name, **kw: _Span(name)
    with patch.object(live_worm, "get_tracer", return_value=tracer):
        ok = live_worm.revoke_live("degraded-live: Alpaca 401 (terminal)")
    assert ok is True
    assert _live_events(env)[0]["switch_id"] == "sw-boot-42"
    name, attrs = spans[-1]
    assert name == "live.worm.compensating_disable.written"
    assert attrs.get("switch_id") == "sw-boot-42"


def test_revoke_live_explicit_switch_id_wins_over_env(env, monkeypatch):
    # An explicit switch_id argument takes precedence over the env (in-process caller knows best).
    monkeypatch.setenv("AAA_SWITCH_ID", "sw-env")
    from core.live_worm import revoke_live

    revoke_live("degraded", switch_id="sw-explicit")
    assert _live_events(env)[0]["switch_id"] == "sw-explicit"


def test_cli_revoke_stamps_switch_id_from_env(env, monkeypatch):
    # #2277 INC-1: the hard-boot-crash CLI (spawned by the shell with AAA_SWITCH_ID) writes switch_id.
    monkeypatch.setenv("AAA_SWITCH_ID", "sw-crash-7")
    from core.live_worm import main

    rc = main(
        ["revoke", "--reason", "boot crash (terminal)", "--actor", "shell:boot-crash"]
    )
    assert rc == 0
    assert _live_events(env)[-1]["switch_id"] == "sw-crash-7"


def test_revoke_live_emits_compensating_disable_otel(env):
    import core.live_worm as live_worm

    spans = []  # list of (name, attrs)

    class _Span:
        def __init__(self, name):
            self._attrs = {}
            spans.append((name, self._attrs))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def set_attribute(self, k, v):
            self._attrs[k] = v

    tracer = MagicMock()
    tracer.start_as_current_span = lambda name, **kw: _Span(name)

    with patch.object(live_worm, "get_tracer", return_value=tracer):
        live_worm.revoke_live("degraded-live: Alpaca 403", trigger="boot-degrade")

    assert spans, "revoke_live must emit an OTEL span"
    name, attrs = spans[-1]
    assert name == "live.worm.compensating_disable.written"
    assert attrs.get("trigger") == "boot-degrade"
    assert attrs.get("reason") == "degraded-live: Alpaca 403"
    assert attrs.get("terminal") is True


def test_revoke_live_is_best_effort_returns_false_on_write_error(env):
    """A WORM sink failure must NOT raise (boot must not brick) — returns False instead."""
    import core.live_worm as live_worm

    with patch(
        "core.hitl_gate.log_live_enablement_event",
        new=AsyncMock(side_effect=RuntimeError("disk full")),
    ):
        ok = live_worm.revoke_live(
            "degraded",
        )
    assert ok is False  # honest: the disable was not durable


def test_revoke_live_returns_false_when_durable_write_silently_fails(env):
    """HIGH (#2262 review): a SWALLOWED durable-write failure must NOT be reported as written.

    The real ``log_live_enablement_event`` only re-raises a failed durable write with strict=True;
    with strict=False it logs a warning and returns normally. ``revoke_live`` MUST demand the strict
    write internally so it returns False (and the caller stamps ``disable_written=false``) — never
    claim a disable that never hit the chain (re-opens #01/#10 under the write-failure condition).
    """
    import core.live_worm as live_worm
    from core.round_table import senate_log

    spans = []  # a success OTEL span must NOT be emitted on a failed write

    class _Span:
        def __init__(self, name):
            spans.append(name)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def set_attribute(self, *a):
            pass

    tracer = MagicMock()
    tracer.start_as_current_span = lambda name, **kw: _Span(name)

    with patch.object(
        senate_log.LocalJSONAuditLogger,
        "log_hitl_event",
        new=AsyncMock(side_effect=RuntimeError("WAL locked")),
    ), patch.object(live_worm, "get_tracer", return_value=tracer):
        ok = live_worm.revoke_live("degraded-live: Alpaca 401 (terminal)")

    assert ok is False  # honest: the disable did NOT land durably
    assert _live_events(env) == []  # nothing appended to the chain
    assert (
        "live.worm.compensating_disable.written" not in spans
    )  # no false "written" event


# ── CLI: python -m core.live_worm revoke — standalone (c) ─────────────────────


def test_cli_revoke_appends_disable_and_exits_zero(env):
    from core.live_worm import main

    rc = main(
        ["revoke", "--reason", "boot crash (terminal)", "--actor", "shell:boot-crash"]
    )
    assert rc == 0
    live = _live_events(env)
    assert live and live[-1]["action"] == "disable"
    assert live[-1]["actor"] == "shell:boot-crash"


def test_cli_revoke_failure_exits_one_loud(env):
    """strict=True — a durable-write failure must exit NON-ZERO (LOUD, never swallowed)."""
    from core.live_worm import main

    with patch(
        "core.hitl_gate.log_live_enablement_event",
        new=AsyncMock(side_effect=RuntimeError("sink down")),
    ):
        rc = main(["revoke", "--reason", "boot crash"])
    assert rc == 1


def test_cli_revoke_uses_strict_true(env):
    """The CLI must demand a durable write (strict=True) — capital safety is on the line."""
    from core.live_worm import main

    spy = AsyncMock()
    with patch("core.hitl_gate.log_live_enablement_event", new=spy):
        main(
            [
                "revoke",
                "--reason",
                "r",
                "--nonce",
                "fixed-nonce",
                "--actor",
                "shell:boot-crash",
            ]
        )
    spy.assert_awaited_once()
    kwargs = spy.await_args.kwargs
    assert kwargs["action"] == "disable"
    assert kwargs["strict"] is True
    assert kwargs["nonce"] == "fixed-nonce"
    assert kwargs["actor"] == "shell:boot-crash"


def test_module_never_imports_entitlement_or_tier(env):
    """BORA: live_worm is edition-neutral — no tier/entitlement IMPORT may sneak in."""
    src = (_ROOT / "core" / "live_worm.py").read_text(encoding="utf-8")
    import_lines = [
        ln.strip()
        for ln in src.splitlines()
        if ln.strip().startswith(("import ", "from "))
    ]
    joined = "\n".join(import_lines).lower()
    assert "entitlement" not in joined
    assert "tier" not in joined
    assert "resolve_entitlement" not in src
