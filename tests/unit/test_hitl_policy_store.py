# tests/unit/test_hitl_policy_store.py
# INC-4 (#2213) — the HITL/autonomy policy must SURVIVE an engine restart. Today
# apply_hitl_policy_update mutates in-memory only, so the live-enable restart wipes the
# operator's tuned limits back to env defaults. This store persists them to SystemConfig
# (mirroring the Iron Dome policy) and re-applies them at boot. TDD Red→Green.
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from core.hitl_policy_store import (
    HITL_POLICY_CONFIG_KEY,
    load_and_apply_hitl_policy,
    save_hitl_policy,
)


def test_save_persists_only_the_adjustable_keys():
    # HITL_ENABLED (env-only, C2) and any stray key must NEVER be persisted — only the five
    # runtime-adjustable limits.
    seen = {}
    # AsyncMock (§5.2): _upsert_system_config is an async interface — never a bare `async def`
    # mock (that breaks the moment the caller adds a kwarg).
    fake_upsert = AsyncMock(
        side_effect=lambda key, value: seen.update(key=key, value=value)
    )

    with patch("core.hitl_policy_store._upsert_system_config", fake_upsert):
        asyncio.run(
            save_hitl_policy(
                {
                    "HITL_ENABLED": True,
                    "HITL_MAX_VALUE_PER_TRADE": 5000.0,
                    "HITL_AUTONOMOUS_UNLIMITED": True,
                    "SOME_STRAY": 1,
                }
            )
        )
    assert seen["key"] == HITL_POLICY_CONFIG_KEY
    assert seen["value"] == {
        "HITL_MAX_VALUE_PER_TRADE": 5000.0,
        "HITL_AUTONOMOUS_UNLIMITED": True,
    }
    assert "HITL_ENABLED" not in seen["value"]


def test_boot_reapplies_the_persisted_policy():
    applied = {}
    # load_hitl_policy is async → AsyncMock. apply_hitl_policy_update is SYNC → plain callable.
    fake_load = AsyncMock(
        return_value={
            "HITL_MAX_VALUE_PER_TRADE": 250.0,
            "HITL_AUTONOMOUS_UNLIMITED": False,
        }
    )
    fake_apply = lambda v: applied.update(v)  # noqa: E731

    with patch("core.hitl_policy_store.load_hitl_policy", fake_load), patch(
        "config.apply_hitl_policy_update", fake_apply
    ):
        asyncio.run(load_and_apply_hitl_policy())
    assert applied == {
        "HITL_MAX_VALUE_PER_TRADE": 250.0,
        "HITL_AUTONOMOUS_UNLIMITED": False,
    }


def test_boot_with_nothing_stored_is_a_noop():
    calls = {"n": 0}
    fake_load = AsyncMock(return_value={})

    def fake_apply(v):
        calls["n"] += 1

    with patch("core.hitl_policy_store.load_hitl_policy", fake_load), patch(
        "config.apply_hitl_policy_update", fake_apply
    ):
        asyncio.run(load_and_apply_hitl_policy())
    assert calls["n"] == 0  # nothing persisted → env defaults stand, no apply


def test_boot_is_fail_safe_when_load_raises():
    # Persistence must NEVER break the boot — a DB error degrades to env defaults + a warning.
    calls = {"n": 0}
    fake_load = AsyncMock(side_effect=RuntimeError("db down"))

    def fake_apply(v):
        calls["n"] += 1

    with patch("core.hitl_policy_store.load_hitl_policy", fake_load), patch(
        "config.apply_hitl_policy_update", fake_apply
    ):
        asyncio.run(load_and_apply_hitl_policy())  # must not raise
    assert calls["n"] == 0


def test_save_upsert_uses_the_real_db_layer_signature():
    # Guard: save delegates to _upsert_system_config (the SystemConfig upsert), not a raw session.
    with patch(
        "core.hitl_policy_store._upsert_system_config", new_callable=AsyncMock
    ) as up:
        asyncio.run(save_hitl_policy({"HITL_EXPIRY_SECONDS": 900}))
    up.assert_awaited_once()
    assert up.await_args.args[0] == HITL_POLICY_CONFIG_KEY
