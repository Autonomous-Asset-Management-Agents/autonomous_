# core/engine/__main__.py
# Epic 1.7 / PR-C: Ermöglicht `python -m core.engine` im Docker/Cloud-Run-Container.
# Delegate to api_routes.main() which starts uvicorn on ENGINE_HOST:ENGINE_PORT.

import json
import logging
import os
from datetime import datetime, timezone


def _resolve_host() -> str:
    """Resolve the uvicorn bind host (G0b, #1050 / AUDIT-008, INV-03).

    Default is LOOPBACK: the old implicit ``0.0.0.0`` default exposed the
    engine API to the entire LAN on desktop machines. Cloud Run/Docker are
    unaffected — every deploy artifact sets ``ENGINE_HOST=0.0.0.0``
    EXPLICITLY (Dockerfile.backend:17, cloudbuild-backend-deploy.yaml:41,
    cloudbuild.yaml:74, cloudbuild-engine-only.yaml:51, docker-compose*.yml;
    verified before this flip). Binding all interfaces is now an explicit
    operator decision, never an accident.
    """
    return os.environ.get("ENGINE_HOST", "127.0.0.1")


def _preflight_action(preflight_ok: bool, is_desktop: bool) -> str:
    """Decide the FAIL-SAFE response to the boot pre-flight (shadow-boot + live-config gate).

    HOTFIX (live-boot-failsafe): a pre-flight *raise* used to propagate to an exit-1 restart loop
    that BRICKS the desktop (engine dies before uvicorn binds → the shell restarts → same crash →
    boot loop the operator can't escape). This decision function NEVER yields an exit-1 action:

      - pre-flight OK                → "continue" (boot uvicorn normally)
      - pre-flight FAILED, desktop   → "degrade_paper" (force PAPER, keep booting — never brick)
      - pre-flight FAILED, cloud     → "exit0" (stop cleanly; Cloud Run marks unhealthy, no loop)
    """
    if preflight_ok:
        return "continue"
    return "degrade_paper" if is_desktop else "exit0"


# ── LSR R2 (#2253): live-arm-lifecycle honesty + compensating WORM self-heal ──────────────────────


def _requested_live() -> bool:
    """True iff THIS boot was requested LIVE (PAPER_TRADING=false), read BEFORE force_paper flips it.

    The shell sets PAPER_TRADING in the env from the verified WORM chain; the degrade branch runs
    before ``force_paper_trading`` mutates it, so the env still reflects the boot request.
    """
    return os.environ.get("PAPER_TRADING", "true").strip().lower() in (
        "false",
        "0",
        "no",
        "off",
    )


def _degrade_reason(result) -> str:
    """Human, operator-visible reason string from the structured ShadowBootResult failures."""
    failures = getattr(result, "failures", None) or []
    if not failures:
        return "shadow-boot pre-flight failed"
    return "; ".join(f"{f.name}: {f.reason}" for f in failures)


def _switch_id() -> str:
    """The current switch's correlation id (#2277 INC-1), threaded by the shell as ``AAA_SWITCH_ID``.

    Empty when the engine wasn't restarted by a Live⇄Paper switch (e.g. tray start) → the breadcrumb
    and spans then simply carry no switch_id.
    """
    return (os.environ.get("AAA_SWITCH_ID") or "").strip()


def _write_boot_outcome(
    *, terminal: bool, reason: str, disable_written: bool, switch_id: str = ""
) -> None:
    """Best-effort breadcrumb ``<AAA_USER_DATA_DIR>/live_boot_outcome.json`` (I-3/I-4).

    Read by the desktop shell's ``_bootFailed`` branch to compensate a HARD boot-crash (FastAPI
    dead → no HTTP API) by spawning ``python -m core.live_worm revoke``. ``switch_id`` (#2277 INC-1)
    lets the shell stamp the crash/revoke spans with the same correlation id. Never raises.
    """
    udd = os.environ.get("AAA_USER_DATA_DIR", "").strip()
    if not udd:
        return
    try:
        payload = {
            "terminal": bool(terminal),
            "reason": reason,
            "ts": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(),
            "disable_written": bool(disable_written),
        }
        if switch_id:
            payload["switch_id"] = switch_id
        with open(
            os.path.join(udd, "live_boot_outcome.json"), "w", encoding="utf-8"
        ) as fh:
            json.dump(payload, fh)
    except (
        Exception
    ) as exc:  # noqa: BLE001 — a breadcrumb write must never brick the boot
        logging.warning("Could not write live_boot_outcome breadcrumb: %s", exc)


def _emit_degraded_transient(reason: str, switch_id: str = "") -> None:
    """Best-effort OTEL span for a TRANSIENT live-degrade where the WORM arm is KEPT (I-3).

    ``switch_id`` (#2277 INC-1) joins the degrade span to the enable/restart it belongs to.
    """
    try:
        from core.telemetry import get_tracer

        with get_tracer("live-boot").start_as_current_span(
            "live.boot.degraded_transient"
        ) as span:
            span.set_attribute("reason", reason)
            span.set_attribute("arm_kept", True)
            if switch_id:
                span.set_attribute("switch_id", switch_id)
    except Exception as exc:  # noqa: BLE001 — telemetry must never brick the boot
        logging.debug("degraded_transient telemetry emit skipped: %s", exc)


def _handle_live_degrade(result) -> None:
    """Desktop live-boot degrade → PAPER, ehrlich + self-healing. LSR R2 (#2253). Never raises.

    Only reached on the desktop degrade path (``_preflight_action`` yields ``degrade_paper`` for
    ``is_desktop`` only; cloud gets ``exit0``), so no edition guard is needed here.

    TERMINAL (Alpaca 401/403) → force paper + compensating WORM ``disable`` (``core.live_worm``) so
    the next boot re-arms PAPER (not silently live) + breadcrumb ``terminal:true``. TRANSIENT (LLM
    warming/5xx/timeout) → force paper + KEEP the WORM enable (retry next boot) + ``DEGRADED_LIVE``
    + ``LIVE_ENABLE_FAILED_REASON`` + breadcrumb ``terminal:false``. A boot that was ALREADY paper
    has no live arm to heal → force paper only.
    """
    import config

    was_live = _requested_live()
    terminal = any(
        getattr(f, "terminal", False) for f in getattr(result, "failures", []) or []
    )
    reason = _degrade_reason(result)
    switch_id = (
        _switch_id()
    )  # #2277 INC-1: correlation id from the shell's AAA_SWITCH_ID

    # The safety action (force PAPER) runs FIRST and unconditionally — it must never depend on the
    # audit/breadcrumb succeeding (mirrors _record_downgrade_audit's ordering).
    config.force_paper_trading(reason=reason)

    if not was_live:
        # The boot was already paper — nothing live to heal; keep the pre-R2 (log-only) behaviour.
        return

    if terminal:
        disable_written = False
        try:
            from core.live_worm import revoke_live

            disable_written = bool(
                revoke_live(reason, trigger="boot-degrade", switch_id=switch_id or None)
            )
        except (
            Exception
        ) as exc:  # noqa: BLE001 — best-effort; the shell hard-crash path compensates
            logging.warning("compensating WORM disable failed (best-effort): %s", exc)
        # Surface WHY live is off on THIS boot too (I-4), even though the arm is revoked.
        try:
            config.LIVE_ENABLE_FAILED_REASON = reason
        except Exception as exc:  # noqa: BLE001
            logging.warning("Could not record LIVE_ENABLE_FAILED_REASON: %s", exc)
        logging.critical(
            "LIVE_ENABLE_FAILED (terminal): compensating WORM disable %s. reason=%s",
            "written" if disable_written else "NOT written (shell will compensate)",
            reason,
        )
        _write_boot_outcome(
            terminal=True,
            reason=reason,
            disable_written=disable_written,
            switch_id=switch_id,
        )
    else:
        # Transient: keep the WORM enable (next boot re-arms + retries); surface + mark degraded.
        try:
            config.LIVE_ENABLE_FAILED_REASON = f"degraded-live: {reason}"
            config.DEGRADED_LIVE = True
        except Exception as exc:  # noqa: BLE001 — surfacing must never brick the boot
            logging.warning("Could not record degraded-live flags: %s", exc)
        logging.warning(
            "LIVE_ENABLE_FAILED (transient): kept the WORM arm, degraded to PAPER, will retry "
            "next boot. reason=%s",
            reason,
        )
        _emit_degraded_transient(reason, switch_id)
        _write_boot_outcome(
            terminal=False,
            reason=reason,
            disable_written=False,
            switch_id=switch_id,
        )


if __name__ == "__main__":
    import config

    if hasattr(config, "init_logging"):
        config.init_logging()

    try:
        from core.engine.health import check_startup_preconditions

        check_startup_preconditions()

        if "K_SERVICE" in os.environ and os.path.exists(".env"):
            raise RuntimeError(
                "CRITICAL: Local .env found in prod container! Aborting to prevent secret leakage."
            )

        import asyncio
        import sys

        from core.engine.api_routes import app
        from core.engine.live_trading_guard import assert_live_trading_config
        from scripts.shadow_boot import run_shadow_boot_result

        _is_desktop = os.environ.get("DEPLOYMENT_MODE", "").upper() == "LOCAL"

        try:
            _shadow_result = asyncio.run(run_shadow_boot_result())
        except Exception as _sb_err:  # noqa: BLE001
            logging.critical(
                "Shadow Boot raised — treating as failed (fail-safe): %s",
                _sb_err,
                exc_info=True,
            )
            from scripts.shadow_boot import CheckFailure, ShadowBootResult

            _shadow_result = ShadowBootResult(
                ok=False,
                failures=[
                    CheckFailure("shadow_boot", f"raised: {_sb_err}", terminal=False)
                ],
            )

        _action = _preflight_action(_shadow_result.ok, _is_desktop)
        if _action == "exit0":
            logging.critical(
                "CRITICAL: Shadow Boot FAILED — stopping cleanly (exit 0, no restart loop)."
            )
            sys.exit(0)
        elif _action == "degrade_paper":
            logging.critical(
                "CRITICAL: Shadow Boot FAILED — degrading to PAPER and continuing (fail-safe)."
            )
            _handle_live_degrade(_shadow_result)

        try:
            assert_live_trading_config()
        except Exception as _lg_err:  # noqa: BLE001
            logging.critical(
                "Live-config gate raised — fail-safe: %s", _lg_err, exc_info=True
            )
            if _is_desktop:
                config.force_paper_trading(reason="live-config gate raised at boot")
            else:
                sys.exit(0)

        try:
            from core.eula_seal import seal_eula_acceptance

            asyncio.run(seal_eula_acceptance())
        except Exception as exc:
            logging.warning("EULA acceptance seal skipped: %s", exc)

        host = _resolve_host()
        port = int(os.environ.get("PORT", os.environ.get("ENGINE_PORT", "8001")))
        from core.engine.uvicorn_boot import run_with_bind_retry

        run_with_bind_retry(app, host, port)

    except Exception as exc:
        logging.critical("Fatal error during engine boot: %s", exc, exc_info=True)
        import sys

        sys.exit(1)
