# core/live_worm.py
# LSR R2 (#2253): compensating WORM-`disable` self-heal for a live boot that degraded to PAPER.
#
# Live-arming is audit-BEFORE-enable: the desktop shell only boots the engine live once a
# deliberate, un-revoked EU-AI-Act-Art.-14 `enable` record verifies on the tamper-evident SHA-256
# WORM chain (audit-chain.cjs verifyAuditChain, read-only; the ENGINE owns every write — BORA).
#
# But a live boot can still degrade to PAPER (rotated Alpaca key → 401, LLM unreachable, bind race).
# When the degrade is TERMINAL (heals never without operator action) we must APPEND a compensating
# `disable` to the SAME chain, so `verifyAuditChain` reads live as REVOKED on the next boot and the
# shell re-arms PAPER — instead of silently re-arming live without a fresh, deliberate consent
# (audit findings #01/#10). Python owns this write; the shell NEVER writes the chain.
#
# Two entrypoints, ONE append implementation (mirrors core/engine/live_trading_guard.py
# _record_downgrade_audit and the core/engine/__main__.py `python -m` entrypoint pattern). Both
# demand a DURABLE write (strict=True) so a swallowed WORM-sink failure can never be mistaken for a
# written disable (that would leave the enable un-revoked and re-arm live — #01/#10):
#   * revoke_live(...)                    — in-process for the __main__.py boot-degrade branch;
#                                           NEVER raises (the degraded boot must not brick) but
#                                           RETURNS False when the disable did not land, so the
#                                           caller stamps disable_written=false and the shell
#                                           boot-start/crash retry compensates.
#   * python -m core.live_worm revoke ... — standalone CLI (exit 0 durable / exit 1 LOUD failure)
#                                           for the shell's boot-start-retry AND hard-boot-crash
#                                           paths, where FastAPI is dead and there is no /api/live.
#
# BORA: edition-neutral — NO tier/entitlement import, NO GCP-only import. Renamed-safe in the OSS
# snapshot. It only appends via hitl_gate.log_live_enablement_event; it NEVER reads or verifies.
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import uuid
from typing import Optional

from core.telemetry import get_tracer

logger = logging.getLogger(__name__)

_TRACER_NAME = "live-worm"


def _switch_id_from_env() -> Optional[str]:
    """The current switch's correlation id, threaded by the shell as ``AAA_SWITCH_ID`` (#2277 INC-1).

    Both entrypoints (in-process degrade, standalone CLI) inherit the shell's spawn env, so the
    compensating ``disable`` can carry the *enable's* switch_id and join the degrade back to it.
    """
    val = (os.environ.get("AAA_SWITCH_ID") or "").strip()
    return val or None


def _emit_compensating_disable(
    *, trigger: str, reason: str, terminal: bool, switch_id: Optional[str] = None
) -> None:
    """Best-effort OTEL span: a compensating WORM ``disable`` was durably written (I-3).

    ``trigger`` ∈ {boot-degrade (in-process), boot-crash (standalone CLI)}. ``switch_id`` (#2277
    INC-1) joins the compensating disable back to the enable that armed it. Never raises —
    telemetry must not break the self-heal.
    """
    try:
        with get_tracer(_TRACER_NAME).start_as_current_span(
            "live.worm.compensating_disable.written"
        ) as span:
            span.set_attribute("trigger", trigger)
            span.set_attribute("reason", reason)
            span.set_attribute("terminal", bool(terminal))
            if switch_id:
                span.set_attribute("switch_id", switch_id)
    except Exception as exc:  # noqa: BLE001 — telemetry must never break the write path
        logger.debug("[live_worm] telemetry emit skipped: %s", exc)


async def _append_disable(
    reason: str,
    actor: str,
    nonce: str,
    *,
    strict: bool,
    switch_id: Optional[str] = None,
) -> None:
    """Append a SYSTEM ``disable`` to the Art-14 hash chain via the ONE shared HITL audit sink.

    Imported lazily so the module stays cheap to import and patchable in tests. ``strict=True``
    re-raises on a failed durable write (the CLI must exit non-zero); ``strict=False`` is
    best-effort (the in-process degrade already forced PAPER — the safety action does not depend
    on the audit succeeding). ``switch_id`` (#2277 INC-1) is written into the WORM record so the
    compensating disable joins the enable it revokes.
    """
    from core import hitl_gate

    await hitl_gate.log_live_enablement_event(
        action="disable",
        acknowledgment=reason,
        nonce=nonce,
        actor=actor,
        switch_id=switch_id,
        strict=strict,
    )


def revoke_live(
    reason: str,
    actor: str = "system:boot-degrade",
    *,
    trigger: str = "boot-degrade",
    nonce: Optional[str] = None,
    switch_id: Optional[str] = None,
) -> bool:
    """In-process compensating WORM ``disable``. Returns True IFF the disable DURABLY landed.

    Called from ``core/engine/__main__.py`` when a live boot degraded TERMINALLY but the engine
    still booted (on PAPER). Demands a DURABLE write (``strict=True``) so a swallowed WORM-sink
    failure (WAL contention, disk full, mis-resolved audit dir) can NEVER be mis-reported as a
    written disable — that would leave the ``enable`` un-revoked and re-arm live on the next boot
    (#01/#10). Wrapped so it NEVER raises (the already-degraded boot must not brick): on a failed
    durable write it logs LOUD (critical) and returns False, and the caller stamps
    ``disable_written=false`` in the breadcrumb so the shell's boot-start/crash retry compensates.
    """
    sid = switch_id or _switch_id_from_env()
    try:
        asyncio.run(
            _append_disable(
                reason, actor, nonce or uuid.uuid4().hex, strict=True, switch_id=sid
            )
        )
    except (
        Exception
    ) as exc:  # noqa: BLE001 — must not brick the (already-degraded) boot
        logger.critical(
            "[live_worm] compensating WORM disable did NOT land (durable write failed): %s "
            "— live remains un-revoked; the shell boot-start/crash retry must compensate.",
            exc,
        )
        return False
    _emit_compensating_disable(
        trigger=trigger, reason=reason, terminal=True, switch_id=sid
    )
    logger.critical(
        "[live_worm] compensating WORM disable written (trigger=%s): %s",
        trigger,
        reason,
    )
    return True


def _revoke_cli(
    reason: str, actor: str, nonce: str, switch_id: Optional[str] = None
) -> int:
    """Standalone-CLI revoke: durable (strict=True) append; exit 0 on success, 1 on failure.

    Used by the desktop shell when a live boot CRASHED (FastAPI dead → /api/live unreachable).
    A failed durable write is LOUD (CRITICAL log + non-zero exit) — never swallowed (I-3/I-4).
    ``switch_id`` (#2277 INC-1) defaults to ``AAA_SWITCH_ID`` from the shell's spawn env.
    """
    sid = switch_id or _switch_id_from_env()
    try:
        asyncio.run(_append_disable(reason, actor, nonce, strict=True, switch_id=sid))
    except (
        Exception
    ) as exc:  # noqa: BLE001 — a failed WORM write MUST be loud + non-zero
        logger.critical(
            "[live_worm] CLI revoke FAILED — compensating WORM disable NOT durable: %s",
            exc,
            exc_info=True,
        )
        return 1
    _emit_compensating_disable(
        trigger="boot-crash", reason=reason, terminal=True, switch_id=sid
    )
    logger.critical("[live_worm] CLI compensating WORM disable written: %s", reason)
    return 0


def main(argv: Optional[list] = None) -> int:
    """``python -m core.live_worm revoke --reason "<text>" [--nonce N] [--actor A]``.

    Resolves the audit dir (SENATE_LOG_DIR/AAA_USER_DATA_DIR) from the env exactly like the engine
    (``LocalJSONAuditLogger`` via ``hitl_gate._resolve_audit_logger``'s fallback) — no config here.
    """
    parser = argparse.ArgumentParser(prog="core.live_worm")
    sub = parser.add_subparsers(dest="command", required=True)
    rev = sub.add_parser(
        "revoke", help="append a compensating WORM `disable` (self-heal)"
    )
    rev.add_argument("--reason", required=True, help="operator-visible degrade reason")
    rev.add_argument(
        "--nonce", default=None, help="WORM replay-distinct id (default: random)"
    )
    rev.add_argument(
        "--actor", default="system:cli", help="who/what triggered the revoke"
    )
    rev.add_argument(
        "--switch-id",
        default=None,
        help="switch correlation id (default: AAA_SWITCH_ID env)",
    )
    args = parser.parse_args(argv)

    if args.command == "revoke":
        return _revoke_cli(
            args.reason,
            args.actor,
            args.nonce or uuid.uuid4().hex,
            args.switch_id,
        )
    return 2


if __name__ == "__main__":
    sys.exit(main())
