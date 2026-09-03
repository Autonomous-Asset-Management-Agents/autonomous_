/**
 * UXC-1 S3 (#3156) — ONE source for the user-editable engine keys + their shipped
 * defaults as canonical text. Today the four #2863/#3132 keys (mirrored until now in
 * core/portfolio_shape.py and TradingControlsCard — double-maintained no longer);
 * S4/S5 extend ONLY this registry / the S2 meta. Guardrail keys (ESMA cap, PDT/FINRA,
 * RISK_FORBID_LEVERAGE, HARD_STOP_LOSS_PCT, HITL_ENABLED) are NEVER part of it.
 */

export interface TradingSettingDefault {
  key: string;
  label: string;
  /** Shipped default as canonical text (engine boots with this when the key is absent). */
  default: string;
}

export const TRADING_SETTINGS_DEFAULTS: TradingSettingDefault[] = [
  { key: "VOL_TARGETING_SIZING_ENABLED", label: "Volatility sizing", default: "true" },
  { key: "VOL_TARGET_DAILY_VOL", label: "Daily vol target", default: "0.015" },
  { key: "FULL_UNIVERSE_MAX_POSITIONS", label: "Maximum positions", default: "10" },
  { key: "SMART_EXIT_MIN_HOLD_DAYS", label: "Minimum holding period", default: "20.0" },
];

export interface SettingsDeviation {
  key: string;
  label: string;
  current: string;
  default: string;
}

const numEq = (a: string, b: string): boolean => {
  if (a === b) return true;
  const na = Number(a);
  const nb = Number(b);
  return Number.isFinite(na) && Number.isFinite(nb) && na === nb;
};

/** Keys that are SET in setup.json AND differ from the shipped default. */
export function computeDeviations(
  setup: Record<string, unknown>,
): SettingsDeviation[] {
  const out: SettingsDeviation[] = [];
  for (const d of TRADING_SETTINGS_DEFAULTS) {
    const raw = setup?.[d.key];
    if (raw === undefined || raw === null || raw === "") continue;
    const current = String(raw);
    if (!numEq(current, d.default)) {
      out.push({ key: d.key, label: d.label, current, default: d.default });
    }
  }
  return out;
}

/** The defaults map (engine keys → canonical text) for a "reset to defaults" persist. */
export function defaultsMap(keys: string[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const d of TRADING_SETTINGS_DEFAULTS) {
    if (keys.includes(d.key)) out[d.key] = d.default;
  }
  return out;
}
