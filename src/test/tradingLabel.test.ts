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

// B11: on a fresh Mac without Ollama the engine's trading loop now WAITS for the LLM provider
// (re-probing every 30 s) and /health says why (`startup_blocked_reason: "llm_unreachable"`).
// A bare "Idle" reads like a fault the operator cannot act on; the label carries the hint.
describe("tradingLabel: LLM wait state hint", () => {
  const HINT = "AI model missing: Settings → System";
  it("Idle + llm_unreachable → Idle with the actionable hint", () => {
    expect(tradingLabel(false, false, true, null, "llm_unreachable")).toEqual({
      text: "Idle",
      live: false,
      hint: HINT,
    });
    expect(tradingLabel(false, false, false, null, "llm_unreachable")).toEqual({
      text: "Idle",
      live: false,
      hint: HINT,
    });
  });
  it("Idle without a blocked reason (or another reason) carries no hint", () => {
    expect(tradingLabel(false, false, true, null, null)).toEqual({ text: "Idle", live: false });
    expect(tradingLabel(false, false, true, null, "redis_unreachable")).toEqual({ text: "Idle", live: false });
    expect(tradingLabel(false, false, true, null)).toEqual({ text: "Idle", live: false });
  });
  it("strategy_running=true while the engine still waits for the LLM reads Idle + hint (the flag is set before the loop starts)", () => {
    expect(tradingLabel(true, false, true, null, "llm_unreachable")).toEqual({ text: "Idle", live: false, hint: HINT });
    expect(tradingLabel(true, true, true, null, "llm_unreachable")).toEqual({ text: "Idle", live: false, hint: HINT });
  });
  it("once the reason is cleared a running loop reads Paper/Live again", () => {
    expect(tradingLabel(true, false, true, null, null)).toEqual({ text: "Paper", live: false });
    expect(tradingLabel(true, true, true, null, null)).toEqual({ text: "Live", live: true });
  });
  it("no poll yet (null) stays '—' without a hint", () => {
    expect(tradingLabel(null, false, null, null, "llm_unreachable")).toEqual({ text: "—", live: false });
  });
  it("live account + market closed: the LLM wait state still reads Idle + hint (nothing can trade at the open either)", () => {
    expect(tradingLabel(false, true, false, null, "llm_unreachable")).toEqual({
      text: "Idle",
      live: false,
      hint: HINT,
    });
    expect(tradingLabel(false, true, false, null, null)).toEqual({ text: "Market closed", live: true });
  });
});
