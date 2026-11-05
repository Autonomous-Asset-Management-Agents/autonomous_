import { describe, it, expect } from "vitest";
import { shouldRouteOffline } from "@/lib/offlineRouting";

// Operator finding #8: a deliberate Paper↔Live switch restarts the engine, which
// briefly refuses connections. The health-poll debounce must NOT flash /offline
// while that expected restart is in progress; it must resume normal offline
// routing once the switch clears (a genuinely failed/timed-out switch).

describe("shouldRouteOffline — suppress /offline during a deliberate mode switch (#8)", () => {
  it("does NOT route while a switch is active, even after many failed polls", () => {
    expect(
      shouldRouteOffline({
        consecutiveFail: 9,
        threshold: 3,
        alreadyOffline: false,
        modeSwitchActive: true,
      }),
    ).toBe(false);
  });

  it("routes once the switch has cleared and failures still exceed the threshold", () => {
    expect(
      shouldRouteOffline({
        consecutiveFail: 3,
        threshold: 3,
        alreadyOffline: false,
        modeSwitchActive: false,
      }),
    ).toBe(true);
  });

  it("never re-navigates when already on /offline", () => {
    expect(
      shouldRouteOffline({
        consecutiveFail: 9,
        threshold: 3,
        alreadyOffline: true,
        modeSwitchActive: false,
      }),
    ).toBe(false);
  });

  it("waits for the debounce threshold before routing (no single-transient flash)", () => {
    expect(
      shouldRouteOffline({
        consecutiveFail: 1,
        threshold: 3,
        alreadyOffline: false,
        modeSwitchActive: false,
      }),
    ).toBe(false);
  });
});
