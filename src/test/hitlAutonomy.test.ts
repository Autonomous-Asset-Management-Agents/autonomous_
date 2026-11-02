import { describe, it, expect } from "vitest";
import { autonomyDisclosure, defaultPerTradeLimit } from "../console/live/hitlAutonomy";
import type { HitlPolicy } from "../lib/api";

const policy = (over: Partial<HitlPolicy> = {}): HitlPolicy => ({
  HITL_ENABLED: true,
  HITL_MAX_VALUE_PER_TRADE: 0,
  HITL_MAX_VALUE_PER_DAY: 0,
  HITL_AUTONOMOUS_UNLIMITED: false,
  HITL_ALWAYS_ALLOW_RISK_REDUCING_SELLS: false,
  HITL_EXPIRY_SECONDS: 900,
  ...over,
});

describe("autonomyDisclosure (#2454)", () => {
  it("explicit unlimited → Full autonomous", () => {
    const d = autonomyDisclosure(policy({ HITL_AUTONOMOUS_UNLIMITED: true }));
    expect(d.fullAutonomous).toBe(true);
    expect(d.label).toMatch(/full autonomous/i);
    expect(d.detail).toMatch(/no per-trade approval/i);
  });

  it("no limit set, not unlimited → Manual approval (fail-closed floor)", () => {
    // #2454 (revised): the default is a 10%-limit set at go-live, NOT full autonomous. A bare
    // {0,0,false} policy is the fail-closed floor — every order needs approval.
    const d = autonomyDisclosure(policy());
    expect(d.fullAutonomous).toBe(false);
    expect(d.label).toMatch(/manual/i);
    expect(d.detail).toMatch(/waits for your approval/i);
  });

  it("positive per-trade limit → Semi-autonomous with the amount", () => {
    const d = autonomyDisclosure(policy({ HITL_MAX_VALUE_PER_TRADE: 5000 }));
    expect(d.fullAutonomous).toBe(false);
    expect(d.label).toMatch(/semi-autonomous/i);
    expect(d.detail).toMatch(/per trade/i);
    expect(d.detail).toMatch(/approval/i);
  });

  it("positive per-day limit → Semi-autonomous", () => {
    const d = autonomyDisclosure(policy({ HITL_MAX_VALUE_PER_DAY: 20000 }));
    expect(d.fullAutonomous).toBe(false);
    expect(d.detail).toMatch(/per day/i);
  });

  it("null policy (not loaded yet) → Manual approval (fail-closed)", () => {
    expect(autonomyDisclosure(null).fullAutonomous).toBe(false);
    expect(autonomyDisclosure(null).label).toMatch(/manual/i);
  });
});

describe("defaultPerTradeLimit (#2454)", () => {
  it("10% of live equity, rounded to whole euros", () => {
    expect(defaultPerTradeLimit(50000)).toBe(5000);
    expect(defaultPerTradeLimit(12345)).toBe(1235); // 1234.5 → 1235
  });
  it("non-positive / non-finite equity → 0 (operator sets a value explicitly)", () => {
    expect(defaultPerTradeLimit(0)).toBe(0);
    expect(defaultPerTradeLimit(-100)).toBe(0);
    expect(defaultPerTradeLimit(null)).toBe(0);
    expect(defaultPerTradeLimit(undefined)).toBe(0);
    expect(defaultPerTradeLimit(NaN)).toBe(0);
  });
});
