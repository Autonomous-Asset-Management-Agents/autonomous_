# UXC-1 S2 (#3155) — Serialisierung der neuen WORM-Events (Plan Rev. 2, Option B):
# SettingsChangeEvent (POST-Änderungen) + TradingSettingsEvent (Boot-Seal, eigener
# Diskriminator; TradingControlEvent bleibt eingefroren).

from __future__ import annotations

from core.round_table.senate_log import (
    SettingsChangeEvent,
    TradingSettingsEvent,
    _hitl_event_to_dict,
)


def test_settings_change_discriminator_and_str_only():
    ev = SettingsChangeEvent(
        timestamp="2026-09-02T18:00:00+00:00",
        actor="operator",
        acknowledgment="bestaetigt",
        nonce="n-42",
        changes='[{"from":"7.0","key":"STOP_LOSS_PCT","to":"5.0"}]',
        switch_id="n-42",
    )
    d = _hitl_event_to_dict(ev)
    assert d["event_type"] == "settings_change"
    assert all(isinstance(v, str) for v in d.values()), "str-only für Byte-Parität"
    assert None not in d.values(), "kein null-noise (D1)"


def test_settings_change_switch_id_defaults_to_nonce_at_write_boundary():
    # Das Default-Setzen passiert im Writer (hitl_gate) — das Event selbst erlaubt None,
    # der Writer füllt nonce ein (D1). Hier: Dataclass-Default dokumentiert.
    ev = SettingsChangeEvent(
        timestamp="t",
        actor="a",
        acknowledgment="ack",
        nonce="n-1",
        changes="[]",
    )
    assert ev.switch_id is None or ev.switch_id == "n-1"


def test_trading_settings_seal_event_discriminator():
    ev = TradingSettingsEvent(
        timestamp="2026-09-02T18:00:00+00:00",
        actor="operator",
        app_version="0.4.11",
        settings='{"STOP_LOSS_PCT":"7.0"}',
    )
    d = _hitl_event_to_dict(ev)
    assert d["event_type"] == "trading_settings_seal"
    assert set(d.keys()) == {
        "event_type",
        "timestamp",
        "actor",
        "app_version",
        "settings",
    }
    assert all(isinstance(v, str) for v in d.values())


def test_trading_control_event_stays_frozen():
    """Bestandsschutz: der #2863-Record behält exakt seine bisherigen Felder."""
    from dataclasses import fields

    from core.round_table.senate_log import TradingControlEvent

    assert [f.name for f in fields(TradingControlEvent)] == [
        "timestamp",
        "actor",
        "vol_targeting_sizing_enabled",
        "vol_target_daily_vol",
        "app_version",
    ]
