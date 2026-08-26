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

from core.round_table.senate_log import LocalJSONAuditLogger, TradingControlEvent

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


def _last_sealed(marker: Path) -> dict:
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {
        "vol_targeting_sizing_enabled": data.get("vol_targeting_sizing_enabled"),
        "vol_target_daily_vol": data.get("vol_target_daily_vol"),
    }


async def seal_trading_control() -> bool:
    """Seal the effective trading controls onto the WORM chain if changed (idempotent, best-effort).

    On the first boot (no marker) it seals a baseline; on later boots it seals only when the
    effective controls differ from the last-sealed fingerprint. Returns True iff a NEW record was
    sealed. Never raises — audit must not crash the boot (CLAUDE.md §5.6 / mirrors EULA seal).
    """
    udd = _user_data_dir()
    if not udd:
        return False

    current = _effective_controls()
    marker = Path(udd) / _MARKER_FILE
    if _last_sealed(marker) == current:
        return False  # unchanged since the last seal → no chain spam

    try:
        from core.telemetry import get_service_version

        app_version = str(get_service_version() or "")
    except Exception:
        app_version = ""

    event = TradingControlEvent(
        timestamp=_now(),
        actor=str(os.environ.get("AAA_TRADING_CONTROL_ACTOR", "operator")),
        app_version=app_version,
        **current,
    )
    try:
        # The engine restarts its in-memory chain each boot (mirrors EULA seal); the entry is
        # hash-sealed on its own content and links to the prior on-disk tail.
        await LocalJSONAuditLogger().log_hitl_event(event)
    except Exception as exc:
        logger.warning("[TRADING-CONTROL] failed to seal onto the WORM chain: %s", exc)
        return False

    try:
        marker.write_text(
            json.dumps({**current, "sealed_at": _now()}, indent=2),
            encoding="utf-8",
        )
    except (
        Exception
    ) as exc:  # sealed on-chain already; the marker is only the skip-fingerprint
        logger.warning(
            "[TRADING-CONTROL] sealed on-chain but could not write marker: %s", exc
        )
    logger.info(
        "[TRADING-CONTROL] sealed onto the WORM chain (vol_sizing=%s, daily_vol=%s)",
        current["vol_targeting_sizing_enabled"],
        current["vol_target_daily_vol"],
    )
    return True
