// #5178: ONE gate for every place that raises the Paper/Live switch (sidebar + account areas under
// System → Trading keys). Pure, so the lock table is tested without rendering anything.
import { describe, it, expect } from "vitest";
import { liveSwitchGate, type LiveSwitchGateInput } from "@/console/desktop/switchStatus";

const base: LiveSwitchGateInput = {
  paperTrading: true,
  switchReconciling: false,
  modeSwitch: null,
  systemHalted: false,
  allowLive: true,
};

describe("liveSwitchGate (#5178)", () => {
  it("paper main engine, nothing in flight, Senior: live may be raised, paper may not", () => {
    expect(liveSwitchGate(base)).toEqual({ switching: false, canEnableLive: true, canSwitchToPaper: false });
  });

  it("live main engine: the way back to paper is open, live is not", () => {
    expect(liveSwitchGate({ ...base, paperTrading: false })).toEqual({
      switching: false,
      canEnableLive: false,
      canSwitchToPaper: true,
    });
  });

  it.each<[string, Partial<LiveSwitchGateInput>]>([
    ["unknown account state", { paperTrading: null }],
    ["reconcile in flight", { switchReconciling: true }],
    ["mode-switch overlay up", { modeSwitch: { to: "live" } }],
    ["kill switch active", { systemHalted: true }],
    ["Junior (allow_live=false)", { allowLive: false }],
  ])("live is locked: %s", (_name, patch) => {
    expect(liveSwitchGate({ ...base, ...patch }).canEnableLive).toBe(false);
  });

  it("an unknown entitlement (allowLive=null, before the first poll) does not lock — the modal re-checks", () => {
    expect(liveSwitchGate({ ...base, allowLive: null }).canEnableLive).toBe(true);
  });

  it("a halted engine with an unknown kill-switch state (null) does not lock", () => {
    expect(liveSwitchGate({ ...base, systemHalted: null }).canEnableLive).toBe(true);
  });

  it.each<[string, Partial<LiveSwitchGateInput>]>([
    ["unknown account state", { paperTrading: null }],
    ["already paper", { paperTrading: true }],
    ["reconcile in flight", { switchReconciling: true }],
    ["mode-switch overlay up", { modeSwitch: { to: "paper" } }],
  ])("paper is locked: %s", (_name, patch) => {
    expect(liveSwitchGate({ ...base, paperTrading: false, ...patch }).canSwitchToPaper).toBe(false);
  });

  it("the way back to paper stays open on a halted engine (risk-reducing, as LiveTradingSwitchCard.select)", () => {
    expect(liveSwitchGate({ ...base, paperTrading: false, systemHalted: true }).canSwitchToPaper).toBe(true);
  });

  it("switching is true while the reconcile runs or the overlay is up", () => {
    expect(liveSwitchGate({ ...base, switchReconciling: true }).switching).toBe(true);
    expect(liveSwitchGate({ ...base, modeSwitch: { to: "paper" } }).switching).toBe(true);
  });
});
