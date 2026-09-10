import { describe, it, expect } from "vitest";
import { engineStatusEffect } from "@/lib/engineStatusEffect";

// LSR R8 (#2261): a deliberate, EXPECTED restart must read as a benign transient —
// a neutral loading toast, NEVER a toast.error and NEVER a jump to /offline. A
// genuine "error" must still navigate to /offline and show the error toast. The
// status→effect mapping is a pure function so this is testable without React.

describe("engineStatusEffect — benign restart (LSR R8 #2261)", () => {
  it("'restarting' is benign: a loading toast, no /offline, no error toast", () => {
    const e = engineStatusEffect("restarting");
    expect(e.navigateOffline).toBe(false);
    expect(e.toast).toBeNull();
    expect(e.polling).toBe("stop");
    expect(e.engineReady).toBe(false);
  });

  it("'error' STILL navigates to /offline and shows an error toast", () => {
    const e = engineStatusEffect("error", "boom");
    expect(e.navigateOffline).toBe(true);
    expect(e.toast?.kind).toBe("error");
    expect(e.toast?.message).toBe("boom");
  });

  it("'running' starts health polling and marks the engine ready (no navigation)", () => {
    const e = engineStatusEffect("running");
    expect(e.polling).toBe("start");
    expect(e.engineReady).toBe(true);
    expect(e.navigateOffline).toBe(false);
  });

  it("'starting' shows a loading toast and never navigates to /offline", () => {
    const e = engineStatusEffect("starting");
    expect(e.toast).toBeNull();
    expect(e.navigateOffline).toBe(false);
  });

  it("'stopped' warns without navigating to /offline", () => {
    const e = engineStatusEffect("stopped");
    expect(e.toast).toBeNull();
    expect(e.navigateOffline).toBe(false);
  });
});
