# core/hitl_policy_store.py
# INC-4 (#2213) — durable persistence for the operator's HITL/autonomy policy.
#
# apply_hitl_policy_update (config.py) mutates the running RuntimeConfigState IN MEMORY only, so
# every engine restart (and the live-enable flow restarts the engine) silently reset the operator's
# tuned autonomy back to env defaults — the "HITL policy reset" the user observed. This store
# persists the five runtime-adjustable limits to the SAME durable surface the Iron Dome policy uses
# (a SystemConfig row) and re-applies them at boot. HITL_ENABLED is NEVER persisted here — it is
# env-only (C2) and always engine-enforced on live.
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# The SystemConfig key under which the persisted policy is stored (mirrors iron_dome_policy).
HITL_POLICY_CONFIG_KEY = "hitl_policy"

# The five runtime-adjustable limits (mirror config._HITL_ADJUSTABLE). Only these persist.
_ADJUSTABLE_KEYS = (
    "HITL_MAX_VALUE_PER_TRADE",
    "HITL_MAX_VALUE_PER_DAY",
    "HITL_AUTONOMOUS_UNLIMITED",
    "HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS",
    "HITL_EXPIRY_SECONDS",
)


def _adjustable_only(values: dict) -> dict:
    """Filter to the persistable subset — HITL_ENABLED and stray keys are dropped (defence in
    depth behind the ``extra='forbid'`` POST DTO and config._HITL_ADJUSTABLE)."""
    return {k: values[k] for k in _ADJUSTABLE_KEYS if k in values}


async def _upsert_system_config(config_key: str, config_value: dict) -> None:
    """Upsert a SystemConfig row (same shape as api_routes._save_iron_dome_policy)."""
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


async def save_hitl_policy(values: dict) -> None:
    """Persist the operator's HITL limits durably so they survive an engine restart."""
    await _upsert_system_config(HITL_POLICY_CONFIG_KEY, _adjustable_only(values))


async def load_hitl_policy() -> dict:
    """Return the persisted HITL limits ({} if none stored)."""
    return await _read_system_config(HITL_POLICY_CONFIG_KEY)


async def load_and_apply_hitl_policy() -> None:
    """Boot hook: re-apply the persisted HITL limits so a restart does not silently reset the
    operator's autonomy to env defaults (#2213 INC-4). Fail-SAFE + WARNING (never breaks boot,
    §5.6): a DB error or empty store leaves the env defaults standing."""
    try:
        stored = await load_hitl_policy()
    except Exception:  # noqa: BLE001 — persistence must never break the boot
        logger.warning(
            "HITL policy load failed at boot; using env defaults instead",
            exc_info=True,
        )
        return
    values = _adjustable_only(stored)
    if not values:
        return
    try:
        import config

        config.apply_hitl_policy_update(values)
        logger.info(
            "HITL autonomy policy restored from persistence across restart: %s",
            sorted(values),
        )
        # H2 (#2370): the import-time _enforce_hitl_boot_gate ran with the ENV-derived
        # HITL_AUTONOMOUS_UNLIMITED (default False) BEFORE this persisted value was applied, so a
        # DB-persisted Mode C re-armed unlimited autonomous LIVE execution on every reboot with NO
        # CRITICAL audit signal. Re-emit it here so the Art-14 waiver is attributable each boot.
        if (not getattr(config, "PAPER_TRADING", True)) and getattr(
            config, "HITL_AUTONOMOUS_UNLIMITED", False
        ):
            logger.critical(
                "COMPLIANCE: HITL_AUTONOMOUS_UNLIMITED (Mode C) restored from persistence on a LIVE "
                "boot - unlimited autonomous real-money execution re-armed with NO per-session human "
                "re-approval (EU AI Act Art. 14). Disable via POST /api/hitl/policy or the Kill Switch."
            )
    except Exception:  # noqa: BLE001
        logger.warning(
            "HITL policy re-apply failed at boot; using env defaults instead",
            exc_info=True,
        )
