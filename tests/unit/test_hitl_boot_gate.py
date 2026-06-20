# tests/unit/test_hitl_boot_gate.py
# PR-0a-i (GAP2, EU AI Act Art. 14): the HITL config values + boot gate + runtime guard.
#
# The gate is extracted as `config._enforce_hitl_boot_gate(paper, hitl, unlimited)` so it is
# testable without re-importing the singleton, and config.oss.py mirrors the same function
# (M1: all 6 HITL_* defined BEFORE the gate, so a flat-edition gate cannot NameError).
#
# Scope = config/boot only (dormant). No queue, no order path — those are PR-0a-ii.
from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path

import pytest

_AI_BOT = Path(__file__).resolve().parents[2]  # ai_trading_bot/
if str(_AI_BOT) not in sys.path:
    sys.path.insert(0, str(_AI_BOT))

import config  # noqa: E402

_OSS_CONFIG = _AI_BOT / "config.oss.py"


def _load_oss():
    """Load config.oss.py BY PATH (the OSS image swaps config.py -> config.oss.py)."""
    spec = importlib.util.spec_from_file_location(
        "config_oss_hitl_under_test", _OSS_CONFIG
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- the 6 dormant policy values exist with safe (all-manual) defaults ---


def test_config_py_has_six_hitl_defaults():
    cfg = config.RuntimeConfigState()
    assert cfg.HITL_ENABLED is False
    assert cfg.HITL_MAX_VALUE_PER_TRADE == 0.0
    assert cfg.HITL_MAX_VALUE_PER_DAY == 0.0
    assert cfg.HITL_AUTONOMOUS_UNLIMITED is False
    assert cfg.HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS is False
    assert cfg.HITL_EXPIRY_SECONDS == 900


def test_config_oss_has_six_hitl_defaults():
    oss = _load_oss()
    assert oss.HITL_ENABLED is False
    assert oss.HITL_MAX_VALUE_PER_TRADE == 0.0
    assert oss.HITL_MAX_VALUE_PER_DAY == 0.0
    assert oss.HITL_AUTONOMOUS_UNLIMITED is False
    assert oss.HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS is False
    assert oss.HITL_EXPIRY_SECONDS == 900


# --- boot gate (P1): live (PAPER_TRADING=False) + HITL off MUST refuse boot, both editions ---


def test_boot_gate_live_without_hitl_raises_config_py():
    with pytest.raises(RuntimeError, match="EU AI Act"):
        config._enforce_hitl_boot_gate(False, False, False)


def test_boot_gate_live_without_hitl_raises_config_oss():
    oss = _load_oss()
    with pytest.raises(RuntimeError, match="EU AI Act"):
        oss._enforce_hitl_boot_gate(False, False, False)


@pytest.mark.parametrize(
    "paper,hitl",
    [(True, False), (True, True), (False, True)],
)
def test_boot_gate_safe_combos_do_not_raise(paper, hitl):
    # paper/paper, paper+hitl, live+hitl all boot fine
    config._enforce_hitl_boot_gate(paper, hitl, False)
    _load_oss()._enforce_hitl_boot_gate(paper, hitl, False)


# --- Mode C (E2): live + HITL on + UNLIMITED boots but emits a loud CRITICAL warning ---


def test_mode_c_live_logs_critical(caplog):
    with caplog.at_level(logging.CRITICAL):
        config._enforce_hitl_boot_gate(False, True, True)  # no raise
    assert any(
        ("Mode C" in r.message or "AUTONOMOUS_UNLIMITED" in r.message)
        for r in caplog.records
    ), "Mode C on live must emit a CRITICAL compliance warning"


# --- runtime guard (E1): apply_remote_config must not silently flip to live without HITL ---


def test_apply_remote_config_refuses_live_without_hitl(monkeypatch):
    safe = config.RuntimeConfigState(PAPER_TRADING=True, HITL_ENABLED=False)
    monkeypatch.setattr(config, "_config_state", safe)
    config.apply_remote_config({"alpaca_paper": False})  # attempt to go live
    assert config.get_config().PAPER_TRADING is True, "live-flip must be refused"


def test_apply_remote_config_allows_live_with_hitl(monkeypatch):
    ok = config.RuntimeConfigState(PAPER_TRADING=True, HITL_ENABLED=True)
    monkeypatch.setattr(config, "_config_state", ok)
    config.apply_remote_config({"alpaca_paper": False})  # allowed: HITL configured
    assert config.get_config().PAPER_TRADING is False
