# core/engine/hwm_store.py
# #2557 — durable persistence for the per-symbol trailing-stop High-Water-Mark map.
#
# TradingLoopMixin._ratchet_high_water_marks (core/engine/trading_loop.py) keeps
# _position_high_water_marks IN MEMORY only, so every daily desktop restart reseeds the peak from
# the current price → the trailing stop measures drawdown only from boot, and on the pre-loop
# position_stop path a missing HWM defaults to max(entry, current) → drawdown≈0 → the tier-trail
# can never fire. This store persists the map to the SAME durable surface the HITL/Iron-Dome
# policies use (a SystemConfig row, config_key="position_hwm", JSON value) and reseeds it at boot.
#
# Fail-SAFE + WARNING (never DEBUG, §5.6): any DB error degrades to a safe fallback — {} on read
# (the trail simply re-measures from the current price, the conservative direction — a missing peak
# can only make the trail STRICTER, never manufacture a false exit) and a no-op on write. No schema
# migration is needed (SystemConfig already exists).
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# The SystemConfig key under which the persisted HWM map is stored (mirrors hitl_policy /
# iron_dome_policy).
POSITION_HWM_CONFIG_KEY = "position_hwm"


async def _upsert_system_config(config_key: str, config_value: dict) -> None:
    """Upsert a SystemConfig row (same shape as hitl_policy_store._upsert_system_config)."""
    from datetime import datetime, timezone

    import sqlalchemy as sa

    from core.database.models import SystemConfig
    from core.database.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        row = (
            (
                await session.execute(
                    sa.select(SystemConfig).filter_by(config_key=config_key)
                )
            )
            .scalars()
            .first()
        )
        now = datetime.now(timezone.utc)
        if row:
            row.config_value = config_value
            row.updated_at = now
        else:
            session.add(
                SystemConfig(
                    config_key=config_key, config_value=config_value, updated_at=now
                )
            )
        await session.commit()


async def _read_system_config(config_key: str) -> dict:
    import sqlalchemy as sa

    from core.database.models import SystemConfig
    from core.database.session import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        row = (
            (
                await session.execute(
                    sa.select(SystemConfig).filter_by(config_key=config_key)
                )
            )
            .scalars()
            .first()
        )
        return dict(row.config_value) if row and row.config_value else {}


async def load_position_hwm() -> dict:
    """Return the persisted ``{symbol: peak_price}`` map ({} if none/corrupt/error).

    Fail-SAFE (§5.6): a DB error, a non-dict value, or a corrupt per-symbol entry degrades to a
    safe subset + a WARNING — it NEVER raises. An empty/failed map is the conservative direction:
    the trail re-measures from the current price, which can only make it stricter, never force a
    false exit."""
    try:
        stored = await _read_system_config(POSITION_HWM_CONFIG_KEY)
    except Exception:  # noqa: BLE001 — persistence must never break the trading loop
        logger.warning(
            "position HWM load failed; seeding an empty map (trail re-measures from current price)",
            exc_info=True,
        )
        return {}
    if not isinstance(stored, dict):
        logger.warning(
            "position HWM store returned a non-dict (%s); seeding an empty map",
            type(stored).__name__,
        )
        return {}
    marks: dict = {}
    for sym, peak in stored.items():
        try:
            marks[str(sym)] = float(peak)
        except (TypeError, ValueError):
            logger.warning(
                "position HWM store: dropping corrupt entry %r=%r", sym, peak
            )
    return marks


async def save_position_hwm(marks: dict) -> None:
    """Persist the ``{symbol: peak_price}`` map durably so it survives a desktop restart.

    Fail-SAFE (§5.6): a DB error is swallowed as a no-op + a WARNING (the peak simply re-seeds from
    the current price on the next boot) — it NEVER raises into the trading loop."""
    try:
        payload = {str(sym): float(peak) for sym, peak in (marks or {}).items()}
        await _upsert_system_config(POSITION_HWM_CONFIG_KEY, payload)
    except Exception:  # noqa: BLE001 — persistence must never break the trading loop
        logger.warning(
            "position HWM save failed; the peak will re-seed from the current price on next boot",
            exc_info=True,
        )
