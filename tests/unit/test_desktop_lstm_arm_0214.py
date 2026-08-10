# tests/unit/test_desktop_lstm_arm_0214.py
# RTR / #2388 + #2401 — Desktop 0.2.14 ARM (owner decision 2026-07-23).
#
# The desktop shell (native-engine-manager._childEnv) now ARMS, desktop-scoped, the full
# cross-sectional LSTM selection group + the trailing-exit profit-protection. This module pins:
#
#   1. BORA parity — the arm is a DESKTOP PROFILE (env injection in the shell); the engine
#      config DEFAULTS in config.py (cloud) and config.oss.py (desktop) are UNCHANGED and remain
#      byte-identical for every armed flag. Proves the arm did NOT move a cloud/enterprise default.
#   2. ACTIVATION (machinery) — with a broad, dispersive panel (≥ _MIN_PANEL_SYMBOLS, ≥2 distinct
#      scores) present, LSTMSignalAgent's cross-sectional vote FIRES with weight > 0 (a real
#      opinion, not an abstention).
#   3. THIN-PANEL ABSTAIN (safety floor) — with only a DEFAULT-watchlist-sized panel
#      (10 < _MIN_PANEL_SYMBOLS), the vote ABSTAINS (weight 0): record_cycle writes one snapshot per
#      day = the current cycle's symbols, so a 10-symbol universe never reaches the >=30 the vote
#      needs. This is why the 0.2.14 desktop arm ALSO sets FULL_UNIVERSE_TRADING_ENABLED (same armed
#      group, native-engine-manager._childEnv): it widens the trading universe to the full S&P 500 so
#      the panel reaches >=30 and the vote FIRES on the shipped desktop (offline-verified 2026-07-23 on
#      the 551-symbol cache: 528/528 evaluable symbols voted weight>0; 23 of 551 dropped for short/stale history). The sub-30 abstain below remains the honest floor
#      for any genuinely thin universe.
#
# Fully deterministic: no network / LLM / GPU / Redis. Only the real panel store + numpy math run.

from __future__ import annotations

import importlib.util
import os

import config
from core.report.lstm_panel_store import get_store
from core.round_table.agents import _MIN_PANEL_SYMBOLS, LSTMSignalAgent

# The round-table-v2 / LSTM cross-sectional flag group. Both editions must agree on every
# default (BORA parity — no config divergence between config.py and config.oss.py).
_LSTM_ARM_FLAGS = (
    "LSTM_RANK_PANEL_STRATEGY_INDEPENDENT",
    "REPORT_GENERATOR_V2_ENABLED",
    "LSTM_VOTE_USES_CROSS_SECTIONAL_RANK",
    "GATEKEEPER_STRICT_ML_REQUIRES_RL",
    "EXIT_POLICY_TRAILING_ENABLED",
    # Effectiveness switch: widens the trading universe to the full S&P 500 so the panel
    # reaches >= _MIN_PANEL_SYMBOLS and the cross-sectional vote is not inert on the desktop.
    "FULL_UNIVERSE_TRADING_ENABLED",
)

# The SHIPPED ship-defaults after the round-table-v2 constellation was activated (owner
# decision 2026-07-26): the whole cross-sectional LSTM selection group is now default-ON in
# BOTH editions, and the strict RL gate is relaxed to default-OFF. EXIT_POLICY_TRAILING_ENABLED
# was NOT part of that flip and stays False. These values document the new shipped defaults;
# each remains env-overridable. Verified against config.py + config.oss.py.
_EXPECTED_DEFAULTS = {
    "LSTM_RANK_PANEL_STRATEGY_INDEPENDENT": True,
    "REPORT_GENERATOR_V2_ENABLED": True,
    "LSTM_VOTE_USES_CROSS_SECTIONAL_RANK": True,
    "GATEKEEPER_STRICT_ML_REQUIRES_RL": False,
    "EXIT_POLICY_TRAILING_ENABLED": False,
    "FULL_UNIVERSE_TRADING_ENABLED": True,
}


def _load_config_oss():
    oss_path = os.path.join(os.path.dirname(config.__file__), "config.oss.py")
    spec = importlib.util.spec_from_file_location("config_oss_arm_0214", oss_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── 1. BORA parity: the arm did NOT touch a single engine config default ──────────────
def test_arm_flag_defaults_unchanged_enterprise():
    cfg = config.get_config()
    for flag, expected in _EXPECTED_DEFAULTS.items():
        assert getattr(cfg, flag) is expected, f"{flag} cloud default changed"


def test_arm_flag_defaults_unchanged_and_parity_oss():
    oss = _load_config_oss()
    for flag, expected in _EXPECTED_DEFAULTS.items():
        assert getattr(oss, flag) is expected, f"{flag} desktop default changed"


def test_config_py_and_oss_are_byte_parity_on_arm_flags():
    """The arm is desktop-profile only; both editions must still agree on every default
    (no config divergence introduced — the flags are flipped in the shell, not here)."""
    cfg = config.get_config()
    oss = _load_config_oss()
    for flag in _LSTM_ARM_FLAGS:
        assert getattr(cfg, flag) == getattr(
            oss, flag
        ), f"{flag} diverged between editions"


# ── 2 + 3. Activation vs. honest inert on the panel breadth the vote needs ────────────
def _seed_panel(n: int) -> None:
    """Write one dispersive snapshot of n symbols (strictly distinct scores) into the store."""
    from datetime import datetime, timezone

    ranked = [(f"S{i:03d}", float(i) * 0.05 - 1.0) for i in range(n)]
    get_store().reset()
    get_store().record_cycle(datetime.now(timezone.utc).date(), ranked)


def test_activation_lstm_vote_fires_on_a_broad_panel():
    """ACTIVATION: a broad dispersive panel (≥ _MIN_PANEL_SYMBOLS) frees the cross-sectional
    vote — weight > 0, a real opinion, no 'EXCLUDED' abstention."""
    n = _MIN_PANEL_SYMBOLS + 5
    _seed_panel(n)
    try:
        agent = LSTMSignalAgent()
        mid = f"S{n // 2:03d}"
        score, weight, reasoning = agent._vote_on_rank(mid, 0.3, "BUY")
        assert weight > 0.0, "a broad dispersive panel must free the vote (activation)"
        assert "EXCLUDED" not in reasoning
        assert 0.0 <= score <= 1.0
    finally:
        get_store().reset()


def test_honest_inert_default_watchlist_panel_abstains():
    """HONEST INERT: the desktop default watchlist is 10 symbols; a 10-symbol panel is
    < _MIN_PANEL_SYMBOLS, so the armed vote ABSTAINS (weight 0). Armed, but inert until the
    operator widens the watchlist to ≥ _MIN_PANEL_SYMBOLS — the documented 0.2.14 limitation.
    """
    oss = _load_config_oss()
    assert (
        len(oss.DEFAULT_SYMBOLS) < _MIN_PANEL_SYMBOLS
    ), "guard: the desktop default watchlist is expected to be below the panel floor"
    _seed_panel(len(oss.DEFAULT_SYMBOLS))  # a full default-watchlist cycle
    try:
        agent = LSTMSignalAgent()
        _score, weight, reasoning = agent._vote_on_rank("S005", 0.3, "BUY")
        assert (
            weight == 0.0
        ), "a sub-30 panel must abstain (armed-but-inert on default watchlist)"
        assert "EXCLUDED" in reasoning
    finally:
        get_store().reset()
