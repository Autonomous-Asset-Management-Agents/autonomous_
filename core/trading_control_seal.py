# core/trading_control_seal.py
# #2863 Inc 4: seal the operator's effective trading-control settings onto the tamper-evident
# SHA-256 WORM hash chain, on each engine boot where they differ from the last-sealed state.
#
# The two operator-tunable risk controls — the volatility-sizing master switch
# (`VOL_TARGETING_SIZING_ENABLED`) and its daily-vol target (`VOL_TARGET_DAILY_VOL`) — are set by
# the operator in the desktop console (their own judgement, no advice) and take effect at engine
# boot via env injection (#2865). The ENGINE owns the WORM write (BORA — same pattern as EULA
# GTM-1 T3 / LIVE-1 T4; no JS-side crypto), sealing the effective state onto the same chain as the
# HITL / live-enablement / EULA audits. A `<AAA_USER_DATA_DIR>/trading_control_seal.json`
# fingerprint marker makes it idempotent (unchanged reboots do not spam the chain) while every
# distinct control transition still yields exactly one record.
#
# Values are read edition-neutrally via the `config` module attribute (the SAME accessor the engine
# uses in `core/risk_manager.py`), so the sealed record reflects what actually governed the run —
# never a re-derived default that could drift from the engine's real behaviour.
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from core.round_table.senate_log import (
    LocalJSONAuditLogger,
    TradingControlEvent,
    TradingSettingsEvent,
)

logger = logging.getLogger(__name__)

_MARKER_FILE = "trading_control_seal.json"


def _user_data_dir() -> str:
    return os.environ.get("AAA_USER_DATA_DIR", "").strip()


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _effective_controls() -> dict:
    """Read the effective trading controls as canonical, byte-stable strings (float-free).

    ``config.py`` exposes the values as class attributes via PEP-562 ``__getattr__`` and
    ``config.oss.py`` at module level — ``getattr`` resolves both (BORA), identical to the
    ``core/risk_manager.py`` read. ``repr(float(...))`` is the shortest round-trip string, so the
    stored value is deterministic and the JS verifier only ever re-hashes the string (no float math).
    """
    import config as _cfg

    enabled = bool(getattr(_cfg, "VOL_TARGETING_SIZING_ENABLED", False))
    daily_vol = float(getattr(_cfg, "VOL_TARGET_DAILY_VOL", 0.015))
    return {
        "vol_targeting_sizing_enabled": "true" if enabled else "false",
        "vol_target_daily_vol": repr(daily_vol),
    }


def _effective_settings_json() -> str:
    """Canonical sorted JSON text of the FULL trading-settings registry state (#3155 S2).

    Read at boot via ``core.trading_settings.current_settings`` (call-time ``get_config``,
    BORA-neutral). Text-only values by construction, so the string is byte-stable for the
    JS verifier. An unreadable registry yields ``""`` — the seal must never crash the boot.
    """
    try:
        from core.trading_settings import current_settings

        return json.dumps(current_settings(), sort_keys=True, separators=(",", ":"))
    except Exception as exc:  # noqa: BLE001 — audit must not crash the boot
        logger.warning("[TRADING-CONTROL] settings fingerprint unreadable: %s", exc)
        return ""


def _marker_data(marker: Path) -> dict:
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


async def seal_trading_control() -> bool:
    """Seal the effective trading controls AND registry settings onto the WORM chain
    if changed (idempotent, best-effort — never raises; audit must not crash the boot).

    Two fingerprint groups share the ONE marker file (#3155 S2, plan Rev. 2 Option B):

    * the two #2863 vol controls → ``TradingControlEvent`` (frozen schema);
    * the full ``core.trading_settings`` registry state → dedicated
      ``TradingSettingsEvent`` (own discriminator ``trading_settings_seal``).

    An old two-key marker (pre-S2) simply lacks the settings fingerprint → exactly one
    migration seal on the first boot after the upgrade. A failed write leaves that
    group's fingerprint untouched, so the next boot retries it. Returns True iff at
    least one NEW record was sealed.
    """
    udd = _user_data_dir()
    if not udd:
        return False

    controls = _effective_controls()
    settings_json = _effective_settings_json()
    marker = Path(udd) / _MARKER_FILE
    last = _marker_data(marker)
    need_controls = any(last.get(k) != v for k, v in controls.items())
    need_settings = (
        bool(settings_json) and last.get("trading_settings") != settings_json
    )
    if not need_controls and not need_settings:
        return False  # unchanged since the last seal → no chain spam

    try:
        from core.telemetry import get_service_version

        app_version = str(get_service_version() or "")
    except Exception:
        app_version = ""
    actor = str(os.environ.get("AAA_TRADING_CONTROL_ACTOR", "operator"))

    sealed_any = False
    new_marker = dict(last)

    if need_controls:
        try:
            # The engine restarts its in-memory chain each boot (mirrors EULA seal); the
            # entry is hash-sealed on its own content and links to the prior on-disk tail.
            await LocalJSONAuditLogger().log_hitl_event(
                TradingControlEvent(
                    timestamp=_now(), actor=actor, app_version=app_version, **controls
                )
            )
            sealed_any = True
            new_marker.update(controls)
            logger.info(
                "[TRADING-CONTROL] sealed onto the WORM chain (vol_sizing=%s, daily_vol=%s)",
                controls["vol_targeting_sizing_enabled"],
                controls["vol_target_daily_vol"],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[TRADING-CONTROL] failed to seal onto the WORM chain: %s", exc
            )

    if need_settings:
        try:
            await LocalJSONAuditLogger().log_hitl_event(
                TradingSettingsEvent(
                    timestamp=_now(),
                    actor=actor,
                    app_version=app_version,
                    settings=settings_json,
                )
            )
            sealed_any = True
            new_marker["trading_settings"] = settings_json
            logger.info(
                "[TRADING-CONTROL] sealed full trading-settings state onto the WORM chain (#3155)"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[TRADING-CONTROL] failed to seal trading settings onto the WORM chain: %s",
                exc,
            )

    if not sealed_any:
        return False

    try:
        new_marker["sealed_at"] = _now()
        marker.write_text(json.dumps(new_marker, indent=2), encoding="utf-8")
    except (
        Exception
    ) as exc:  # sealed on-chain already; the marker is only the skip-fingerprint
        logger.warning(
            "[TRADING-CONTROL] sealed on-chain but could not write marker: %s", exc
        )
    return True
