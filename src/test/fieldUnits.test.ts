/**
 * #4999 (SIZE-UX 6a) — values in their unit. The conversion is a decimal shift on text:
 * a factor of 100 in the wrong place would be a wrong setting within the server's bounds.
 */
import { describe, it, expect } from "vitest";
import { ADVANCED_GROUPS } from "@/console/desktop/advancedTradingFields";
import { formatValue, fromDisplay, toDisplay, unitSymbol } from "@/console/desktop/fieldUnits";

const FIELDS = ADVANCED_GROUPS.flatMap((g) => g.fields);

describe("display of a stored value", () => {
  it.each([
    ["fraction", "0.05", "5%"],
    ["fraction", "0.30", "30%"],
    ["fraction", "0.015", "1.5%"],
    ["fraction", "0.0150", "1.5%"],
    ["percent", "-6.0", "-6%"],
    ["percent", "-7.9", "-7.9%"],
    ["lossPercent", "7.0", "-7%"],
    ["lossPercent", "7.5", "-7.5%"],
    ["lossPercent", "0", "0%"],
    ["usd", "10000", "$10,000"],
    ["usd", "1000", "$1,000"],
    ["usd", "100000", "$100,000"],
    ["usd", "950", "$950"],
    ["minutes", "30", "30 min"],
    ["tradingDays", "1", "1 trading day"],
    ["tradingDays", "3", "3 trading days"],
    ["days", "1", "1 day"],
    ["days", "3", "3 days"],
    ["hours", "2.0", "2 h"],
    ["hours", "0.5", "0.5 h"],
    ["percentile", "80", "80th percentile"],
    ["percentile", "75", "75th percentile"],
  ] as const)("%s %s -> %s", (unit, raw, shown) => {
    expect(formatValue(unit, raw)).toBe(shown);
  });

  it("a field without a unit shows its raw value", () => {
    expect(formatValue(undefined, "0.73")).toBe("0.73");
  });

  it("a blank or unreadable value is shown as it is, never as a made-up number", () => {
    expect(formatValue("fraction", "")).toBe("");
    expect(toDisplay("fraction", "")).toBe("");
    expect(formatValue("usd", "abc")).toBe("abc");
    expect(toDisplay("lossPercent", "abc")).toBe("abc");
  });
});

describe("input in the display unit", () => {
  it("7 % is exactly 0.07, on text, without a float product", () => {
    // 0.07 * 100 === 7.000000000000001, which is what the text shift is for.
    expect(toDisplay("fraction", "0.07")).toBe("7");
    expect(fromDisplay("fraction", "7")).toBe("0.07");
    expect(toDisplay("fraction", "0.29")).toBe("29");
    expect(toDisplay("fraction", "0.57")).toBe("57");
    expect(fromDisplay("fraction", "4")).toBe("0.04");
  });

  it("a decimal comma equals a decimal point", () => {
    expect(fromDisplay("fraction", "1,5")).toBe("0.015");
    expect(fromDisplay("fraction", "1.5")).toBe("0.015");
    expect(fromDisplay("percent", "-6,5")).toBe("-6.5");
  });

  it("the unit sign may be typed along", () => {
    expect(fromDisplay("fraction", " 5 % ")).toBe("0.05");
    expect(fromDisplay("usd", "$10000")).toBe("10000");
  });

  it("USD takes a thousands comma, and only in a whole group of three", () => {
    expect(fromDisplay("usd", "10,000")).toBe("10000");
    expect(fromDisplay("usd", "1,500,000")).toBe("1500000");
    // Dropping this comma would turn 1000,5 into 10005: a factor of ten.
    expect(fromDisplay("usd", "1000,5")).toBe("1000.5");
    expect(fromDisplay("usd", "10,5")).toBe("10.5");
  });

  it("the loss sign is flipped against the stored value, never guessed", () => {
    expect(toDisplay("lossPercent", "7.5")).toBe("-7.5");
    expect(fromDisplay("lossPercent", "-5")).toBe("5");
    // A positive entry is -5 stored, which the bounds refuse: same rule as the loss tiers.
    expect(fromDisplay("lossPercent", "5")).toBe("-5");
    expect(toDisplay("percent", "-6.0")).toBe("-6");
    expect(fromDisplay("percent", "-6")).toBe("-6");
  });

  it("rounds to the registry's decimals, half up on the digits", () => {
    expect(fromDisplay("fraction", "7.5", 2)).toBe("0.08");
    expect(fromDisplay("fraction", "7.4", 2)).toBe("0.07");
    expect(fromDisplay("fraction", "1.55", 4)).toBe("0.0155");
    expect(fromDisplay("fraction", "1.555", 4)).toBe("0.0156");
    expect(fromDisplay("fraction", "99.96", 3)).toBe("1");
    expect(fromDisplay("lossPercent", "-5.25", 1)).toBe("5.3");
    expect(fromDisplay("percent", "-5.25", 1)).toBe("-5.3");
    expect(fromDisplay("days", "2.5", 0)).toBe("3");
    expect(fromDisplay("usd", "1000.5", 0)).toBe("1001");
  });

  it.each(["", "   ", "abc", "1.2.3", "1,2,3", "-", ".", "1e3", "Infinity", "0x10", "--5", "5-"])(
    "%j is no number",
    (text) => {
      expect(fromDisplay("fraction", text)).toBeNull();
      expect(fromDisplay(undefined, text)).toBeNull();
    },
  );

  it("a field without a unit passes the number through", () => {
    expect(fromDisplay(undefined, "0,7", 2)).toBe("0.7");
    expect(toDisplay(undefined, "0.70")).toBe("0.70");
  });
});

describe("every field of the table", () => {
  it("carries the unit of its key", () => {
    const units = Object.fromEntries(FIELDS.filter((f) => f.unit).map((f) => [f.key, f.unit]));
    expect(units).toEqual({
      ROTATION_PANEL_MAX_AGE_DAYS: "days",
      LOSS_CUT_PCT: "percent",
      LOSS_WATCH_PCT: "percent",
      LOSS_ESCALATION_PCT: "percent",
      STOP_LOSS_PCT: "lossPercent",
      PANIC_PROTECTION_HOURS: "hours",
      MIN_POSITION_PERCENT: "fraction",
      MAX_POSITION_PERCENT: "fraction",
      MAX_POSITION_PERCENT_SIZING: "fraction",
      MAX_TOTAL_EXPOSURE_PCT: "fraction",
      REGIME_RISKOFF_PERCENTILE: "percentile",
      COMPLIANCE_MAX_ORDER_VALUE: "usd",
      GLOBAL_BUY_COOLDOWN_MINUTES: "minutes",
      NO_BUY_OPENING_MINUTES: "minutes",
      REENTRY_LOCKOUT_DAYS: "tradingDays",
      STOP_EXIT_SLOT_HOLD_DAYS: "tradingDays",
      POSITION_TOPUP_DEAD_BAND_REL: "fraction",
    });
  });

  const sliders = FIELDS.filter((f) => f.kind === "slider");

  it.each(sliders.map((f) => [f.key, f] as const))("%s survives the round trip", (_key, f) => {
    const places = (String(f.step).split(".")[1] ?? "").length;
    const steps = Math.round((f.max! - f.min!) / f.step!);
    for (let i = 0; i <= steps; i += 1) {
      const raw = (f.min! + i * f.step!).toFixed(places);
      const back = fromDisplay(f.unit, toDisplay(f.unit, raw), places);
      expect(Number(back), `${f.key} at ${raw}`).toBe(Number(raw));
    }
  });

  it("each unit names its sign for the input", () => {
    expect(unitSymbol("fraction")).toBe("%");
    expect(unitSymbol("percent")).toBe("%");
    expect(unitSymbol("lossPercent")).toBe("%");
    expect(unitSymbol("usd")).toBe("$");
    expect(unitSymbol("minutes")).toBe("min");
    expect(unitSymbol("tradingDays")).toBe("trading days");
    expect(unitSymbol("days")).toBe("days");
    expect(unitSymbol("hours")).toBe("h");
    expect(unitSymbol("percentile")).toBe("percentile");
    expect(unitSymbol(undefined)).toBe("");
  });
});
