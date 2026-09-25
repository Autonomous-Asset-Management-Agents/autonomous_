import { vi } from "vitest";

/** Shared fixtures of the S5 advanced-section suites (#3158) — NOT a test file. */

export const DEFAULT_SETTINGS: Record<string, string> = {
  SIGNAL_BUY_THRESHOLD: "0.65",
  SIGNAL_SELL_THRESHOLD: "0.35",
  ROTATION_EXIT_ENABLED: "true",
  ROTATION_MAX_EXITS_PER_CYCLE: "1",
  ROTATION_MAX_EXITS_PER_SESSION: "2",
  ROTATION_PANEL_MAX_AGE_DAYS: "3",
  DISPLACEMENT_ENABLED: "true",
  // #3418 Sitzungsdeckel der Verdraengung — Auslieferungs-Default aus
  // config.py:619 / trading_settings.py:155, damit der Regler nicht
  // faelschlich als abweichend ("Changed") gilt.
  DISPLACEMENT_MAX_PER_SESSION: "2",
  CONSENSUS_RETENTION_THRESHOLD: "0.00",
  EXIT_TRAIL_PROFILE: "midterm",
  // #3632 one exit authority: broker backstop switch (ships on) + the exit engine's
  // loss tiers as settings; the smart-exit-only fields are gone.
  BROKER_STOPS_ENABLED: "true",
  STOP_LOSS_PCT: "7.0",
  // #3662 panic protection switch + window (ships on / 2.0 h).
  PANIC_PROTECTION_ENABLED: "true",
  PANIC_PROTECTION_HOURS: "2.0",
  LOSS_WATCH_PCT: "-2.0",
  LOSS_CUT_PCT: "-4.0",
  LOSS_ESCALATION_PCT: "-6.0",
  MIN_POSITION_PERCENT: "0.05",
  MAX_POSITION_PERCENT: "0.25",
  MAX_POSITION_PERCENT_SIZING: "0.30",
  MAX_TOTAL_EXPOSURE_PCT: "0.95",
  // #3199 skew sizing tilt (dark default) — mirrors current_settings() so the baseline
  // matches the rendered default and the field is not spuriously flagged "Changed".
  SKEW_SIZE_TILT_ENABLED: "false",
  SKEW_SIZE_TILT_CAP: "0.00",
  // #3210 coverage prudence sizing (dark default) — mirrors current_settings() so the
  // baseline matches the rendered default and the field is not spuriously flagged "Changed".
  COVERAGE_SIZING_STRENGTH: "0.00",
  // #3361 regime throttle (dark default) — mirrors current_settings().
  REGIME_THROTTLE_ENABLED: "false",
  REGIME_RISKOFF_PERCENTILE: "75",
  REGIME_THROTTLE_SIZE_FACTOR: "0.50",
  // #3220/#3221: Auslieferungs-Defaults, damit die neuen Regler nicht faelschlich
  // als abweichend ("Changed") gelten.
  COMPLIANCE_MAX_ORDER_VALUE: "10000",
  COMPLIANCE_MAX_DAILY_TRADES: "10",
  GLOBAL_BUY_COOLDOWN_MINUTES: "30",
  MAX_BUYS_PER_HOUR: "2",
  NO_BUY_OPENING_MINUTES: "30",
  // #3604 re-entry lockout - shipped default 1 trading day.
  REENTRY_LOCKOUT_DAYS: "1",
  // #3655 slot hold after stop-loss - shipped default 1 trading day.
  STOP_EXIT_SLOT_HOLD_DAYS: "1",
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
