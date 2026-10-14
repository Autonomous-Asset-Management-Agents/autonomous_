import { describe, it, expect } from "vitest";
// The desktop persistence + engine-spawn whitelists. A user-editable trading-settings
// key MUST be in BOTH, or the Settings toggle is silently dropped before it ever reaches
// setup.json / the engine env (the QualityAgent "on-but-shows-off" bug, 2026-09).
import setupManager from "../../desktop/electron/setup-manager.cjs";

const { ALLOWED_SETUP_KEYS, INJECTED_ENV_KEYS } = setupManager as {
  ALLOWED_SETUP_KEYS: Set<string>;
  INJECTED_ENV_KEYS: string[];
};

/**
 * Canonical Round-Table agent knobs the Settings UI exposes — MIRRORS the roster in
 * `ai_trading_bot/core/trading_settings.py` (_SETTINGS enable flags + weight keys).
 * Every one is user-settable, so every one must survive the desktop whitelist AND be
 * injected into the engine env. Adding an agent to the roster REQUIRES adding it here
 * and to both lists in setup-manager.cjs — this guard fails until then.
 */
const AGENT_ENABLE_FLAGS = [
  "MOMENTUM_AGENT_ENABLED",
  "LSTM_SIGNAL_AGENT_ENABLED",
  "SPECIALIST_ALPHA_AGENT_ENABLED",
  "NEWS_SENTIMENT_AGENT_ENABLED",
  "VIX_RISK_AGENT_ENABLED",
  "UPSIDE_SKEW_AGENT_ENABLED",
  "TREND_AGENT_ENABLED",
  "VOLUME_CONFIRM_AGENT_ENABLED",
  "QUALITY_AGENT_ENABLED",
  "DRAWDOWN_GUARD_AGENT_ENABLED",
  "REGIME_DETECTION_AGENT_ENABLED",
  "FUNDAMENTALS_AGENT_ENABLED",
  "VALUATION_AGENT_ENABLED",
  "RL_CONFIDENCE_AGENT_ENABLED",
];

const AGENT_WEIGHT_KEYS = [
  "MOMENTUM_AGENT_WEIGHT",
  "LSTM_SIGNAL_WEIGHT",
  "VIX_RISK_WEIGHT",
  "SPECIALIST_ALPHA_WEIGHT",
  "NEWS_SENTIMENT_WEIGHT",
  "UPSIDE_SKEW_WEIGHT",
  "TREND_AGENT_WEIGHT",
  "VOLUME_CONFIRM_AGENT_WEIGHT",
  "RL_CONFIDENCE_WEIGHT",
  "FUNDAMENTALS_AGENT_WEIGHT",
  "VALUATION_AGENT_WEIGHT",
  "QUALITY_AGENT_WEIGHT",
];

const ALL_AGENT_KEYS = [...AGENT_ENABLE_FLAGS, ...AGENT_WEIGHT_KEYS];

describe("agent settings wiring (setup-manager whitelists)", () => {
  it("persists every agent enable flag + weight to setup.json (ALLOWED_SETUP_KEYS)", () => {
    const missing = ALL_AGENT_KEYS.filter((k) => !ALLOWED_SETUP_KEYS.has(k));
    expect(
      missing,
      `not persisted to setup.json → toggle silently dropped: ${missing.join(", ")}`,
    ).toEqual([]);
  });

  it("injects every agent enable flag + weight into the engine env (INJECTED_ENV_KEYS)", () => {
    const set = new Set(INJECTED_ENV_KEYS);
    const missing = ALL_AGENT_KEYS.filter((k) => !set.has(k));
    expect(
      missing,
      `not injected into engine env → engine boots with defaults: ${missing.join(", ")}`,
    ).toEqual([]);
  });
});
