# core/engine/live_trading_guard.py
# ML-1 Pre-Live Gate — SIP data feed compliance check.
#
# MiFID II Article 27 best-execution applies only when executing real client
# orders with actual capital. Paper trading is out of scope.
#
# When PAPER_TRADING=False, the bot MUST use the SIP consolidated tape (NBBO)
# to satisfy best-execution evidence requirements. IEX is a single exchange
# and does not constitute NBBO.
#
# Call assert_live_trading_config() once at engine startup. It is a no-op
# during paper trading and a hard RuntimeError block for live trading without
# SIP.
#
# To go live:
#   1. Upgrade Alpaca account to Algo Trader Plus ($99/mo) OR configure
#      Polygon.io SIP tier (cheaper alternative — check pricing at go-live).
#   2. Set ALPACA_DATA_FEED=sip in Cloud Run env vars.
#   3. Set PAPER_TRADING=False in Cloud Run env vars.

from __future__ import annotations

import logging
import os

from config import ALPACA_DATA_FEED, PAPER_TRADING

logger = logging.getLogger(__name__)


def assert_live_trading_config() -> None:
    """Raise RuntimeError if going live without SIP data feed.

    No-op during paper trading (MiFID II Art. 27 does not apply).
    Hard block for live trading with non-NBBO feed.
    """
    if PAPER_TRADING:
        logger.debug(
            "[LiveGuard] Paper trading active — data feed check skipped (MiFID II Art. 27 N/A)."
        )
        return

    # GTM-1 (#1800) Brick-4 — signed Tier-Entitlement LIVE gate (Archon §1 CRITICAL fix).
    # Placed AFTER the paper-trading no-op (so "BASIC = paper only" keeps paper working —
    # this fn is called unconditionally at boot) but BEFORE the DEPLOYMENT_MODE==LOCAL
    # early-return below, so a LOCAL desktop can NEVER skip the tier check and go live.
    # For Cloud/Dev/CI resolve_entitlement() returns the full bundle (allow_live=True), so
    # this is a byte-identical no-op there.
    from core.entitlement import resolve_entitlement

    ent = resolve_entitlement()
    if not ent.allow_live:
        # MLR-14 (#1918): live was requested (PAPER_TRADING=False) but the resolved entitlement
        # forbids it — e.g. the signed license token lapsed AFTER the operator armed live on the
        # WORM chain (open real-money positions). RAISING here (the old behaviour) killed the
        # engine process BEFORE uvicorn.run, so /api/live/disable was unreachable and open
        # positions went UNMANAGED (no kill-switch, no HITL, no exits) while the desktop shell —
        # which only reads the WORM chain, not the tier — restarted straight into the same crash:
        # a boot-loop. Instead we DEGRADE fail-closed to paper: force PAPER_TRADING in-process,
        # alert + audit the downgrade, and let the engine boot in paper so positions stay managed.
        # BASIC still can NEVER trade live (it is forced to paper — strictly safer than before).
        _graceful_paper_downgrade(ent)
        return

    # LIVE-1 T1 (#1424): MiFID II Art. 27 best-execution binds investment firms executing CLIENT
    # orders. The OSS desktop edition (DEPLOYMENT_MODE=LOCAL) trades the operator's OWN capital on
    # their own responsibility — Art. 27 is N/A — so the SIP consolidated-tape requirement does
    # NOT apply. The strict cloud/Enterprise (Fremdkapital) path below stays byte-identical.
    if os.environ.get("DEPLOYMENT_MODE", "").upper() == "LOCAL":
        logger.info(
            "[LiveGuard] OSS own-account edition (DEPLOYMENT_MODE=LOCAL) — MiFID II Art. 27 SIP "
            "requirement N/A (own capital, not client orders); live data-feed check skipped."
        )
        return

    if ALPACA_DATA_FEED != "sip":
        raise RuntimeError(
            f"[LiveGuard] BLOCKED: PAPER_TRADING=False but ALPACA_DATA_FEED={ALPACA_DATA_FEED!r}. "
            "Live trading requires the consolidated SIP tape for MiFID II Art. 27 best-execution "
            "evidence. "
            "Fix: set ALPACA_DATA_FEED=sip in Cloud Run env vars after upgrading to "
            "Alpaca Algo Trader Plus ($99/mo) or Polygon.io SIP tier. "
            "See docs/1_architecture_and_adr/ADR-D01-institutional-data-sources.md."
        )

    logger.info(
        "[LiveGuard] Live trading config OK — ALPACA_DATA_FEED=sip, MiFID II Art. 27 compliant."
    )


def _tier_label(ent) -> str:
    """Best-effort human label for the resolved tier (for logs / audit)."""
    tier = getattr(ent, "tier", None)
    return getattr(tier, "value", str(tier if tier is not None else ent))


def _graceful_paper_downgrade(ent) -> None:
    """Degrade a requested-but-unentitled LIVE boot to PAPER instead of crashing (#1918).

    Called when PAPER_TRADING=False was requested but resolve_entitlement() forbids live
    (allow_live=False) — the live entitlement lapsed after the operator armed live. Steps:

      1. Force PAPER in-process (config.force_paper_trading) so the lazily-created broker uses
         the paper account/base-URL/data-feed, never the live keys. This is what keeps open
         positions MANAGED (kill-switch/HITL/exits run) instead of stranded.
      2. Log a loud CRITICAL alert (surfaced to the desktop console via engine stdout).
      3. Best-effort record the downgrade on the tamper-evident WORM chain as a SYSTEM 'disable'
         (see _record_downgrade_audit) — keeps the audit trail honest AND makes the desktop
         shell read verifyAuditChain()=false on the next boot, so it stops flipping PAPER_TRADING
         off (self-healing at the shell level too, without duplicating the Ed25519/tier check in
         JS). Resuming live then requires a fresh, deliberate operator re-arm (Art-14).

    Never raises — the whole point is that the engine keeps booting.
    """
    label = _tier_label(ent)
    logger.critical(
        "[LiveGuard] CRITICAL: live trading requested (PAPER_TRADING=False) but the resolved "
        "entitlement forbids live (tier=%s, allow_live=False) — the live entitlement lapsed "
        "after arming. Degrading to PAPER (fail-closed) so the engine boots and any open "
        "positions stay MANAGED (kill-switch/HITL/exits). Re-arm live via /api/live/enable after "
        "restoring a valid license. (#1918)",
        label,
    )

    import config

    config.force_paper_trading(
        reason=f"live entitlement lapsed (tier={label}, allow_live=False) [#1918]"
    )
    # Keep this module's import-bound view consistent with the flip (we return immediately, but
    # a defensive re-read by any future code below must not see a stale live flag).
    globals()["PAPER_TRADING"] = True

    _record_downgrade_audit(label)


def _record_downgrade_audit(tier_label: str) -> None:
    """Best-effort WORM-chain record of the auto-downgrade. NEVER crashes the boot.

    Recorded as a SYSTEM ``disable`` on the same Art-14 hash chain as /api/live/enable so
    ``verifyAuditChain`` (desktop shell) treats live as revoked on the next boot. ``strict=False``
    → a WORM-write failure only logs a warning; the paper downgrade (the actual safety action) has
    already taken effect and does not depend on the audit succeeding.
    """
    try:
        import asyncio
        import uuid

        from core import hitl_gate

        asyncio.run(
            hitl_gate.log_live_enablement_event(
                action="disable",
                acknowledgment=(
                    "AUTO-DOWNGRADE: live entitlement lapsed "
                    f"(tier={tier_label}, allow_live=False); forced PAPER to keep positions "
                    "managed (EU AI Act Art. 14 fail-closed, #1918)"
                ),
                nonce=uuid.uuid4().hex,
                actor="system:live_guard",
                strict=False,  # best-effort — the audit write must NEVER crash the boot
            )
        )
    except Exception as exc:  # noqa: BLE001 — audit must never crash the boot
        logger.warning("[LiveGuard] downgrade audit record skipped: %s", exc)
