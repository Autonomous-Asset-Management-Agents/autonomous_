import { describe, it, expect } from "vitest";
import { tradingLabel } from "../console/live/trading";

// #2743/S3 (A4): the sidebar run-status must be honest — a degraded engine (broker/model fault)
// reports strategy_running=true but cannot actually trade, so it must NOT read "Live"/"Paper".

describe("tradingLabel — degraded honesty", () => {
  it("running + degraded → 'Degraded' (not Live/Paper)", () => {
    expect(tradingLabel(true, false, true, true)).toEqual({ text: "Degraded", live: false });
    expect(tradingLabel(true, true, true, true)).toEqual({ text: "Degraded", live: false });
  });
  it("running + NOT degraded → Paper/Live as before", () => {
    expect(tradingLabel(true, false, true, false)).toEqual({ text: "Paper", live: false });
    expect(tradingLabel(true, true, true, false)).toEqual({ text: "Live", live: true });
  });
  it("degraded flag unknown (null) does not change the healthy behaviour", () => {
    expect(tradingLabel(true, false, true, null)).toEqual({ text: "Paper", live: false });
  });
  it("not running is unaffected by degraded", () => {
    expect(tradingLabel(false, false, true, true)).toEqual({ text: "Idle", live: false });
    expect(tradingLabel(null, false, true, true)).toEqual({ text: "—", live: false });
  });
});
