import { vi } from "vitest";

/** Shared fixtures of the S5 advanced-section suites (#3158) — NOT a test file. */

export const DEFAULT_SETTINGS: Record<string, string> = {
  SIGNAL_BUY_THRESHOLD: "0.65",
  SIGNAL_SELL_THRESHOLD: "0.35",
  ROTATION_EXIT_ENABLED: "true",
  ROTATION_MAX_EXITS_PER_CYCLE: "1",
  ROTATION_MAX_EXITS_PER_SESSION: "2",
  ROTATION_PANEL_MAX_AGE_DAYS: "3",
  EXIT_POLICY_TRAILING_ENABLED: "true",
  TRAILING_FROM_PEAK_PCT: "10.0",
  EXIT_TRAIL_PROFILE: "midterm",
  STOP_LOSS_PCT: "7.0",
  TAKE_PROFIT_PCT: "25.0",
  MIN_POSITION_PERCENT: "0.05",
  MAX_POSITION_PERCENT: "0.25",
  MAX_POSITION_PERCENT_SIZING: "0.30",
  MAX_TOTAL_EXPOSURE_PCT: "0.95",
  GLOBAL_BUY_COOLDOWN_MINUTES: "30",
  MAX_BUYS_PER_HOUR: "2",
  NO_BUY_OPENING_MINUTES: "30",
  FULL_UNIVERSE_TRADING_ENABLED: "true",
  ROUND_TABLE_TOP_K_EVAL: "30",
};

export const payload = (overrides: Partial<Record<string, string>> = {}) => ({
  agents: [],
  implied_vol_forecast_enabled: true,
  settings: { ...DEFAULT_SETTINGS, ...overrides },
  meta: Object.entries(DEFAULT_SETTINGS).map(([key, def]) => ({
    key,
    kind: "float",
    default: def,
  })),
  guardrails: { HARD_STOP_LOSS_PCT: "-8.0" },
});

export const setBridge = (setup: Record<string, unknown> = {}) => {
  (window as unknown as { aaagents?: unknown }).aaagents = {
    isDesktop: true,
    getSetupState: vi.fn().mockResolvedValue(setup),
    saveSetupState: vi.fn().mockResolvedValue(undefined),
  };
};
