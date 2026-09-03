import { beforeEach, describe, expect, it, vi } from "vitest";

import { resetKillSwitch, startLive } from "../api";
import { resetKillSwitchAndResume } from "../killSwitch";

vi.mock("../api", () => ({
  resetKillSwitch: vi.fn(),
  startLive: vi.fn(),
}));

describe("resetKillSwitchAndResume", () => {
  beforeEach(() => vi.clearAllMocks());

  it("reports success when reset and resume both succeed", async () => {
    vi.mocked(resetKillSwitch).mockResolvedValue({ status: "ok" });
    vi.mocked(startLive).mockResolvedValue({ status: "ok" });

    const r = await resetKillSwitchAndResume();

    expect(r.reset).toBe(true);
    expect(r.message).toMatch(/trading resumed/i);
  });

  it("reports 'not reset' only when the reset itself fails", async () => {
    vi.mocked(resetKillSwitch).mockRejectedValue(new Error("network"));

    const r = await resetKillSwitchAndResume();

    expect(r.reset).toBe(false);
    expect(r.message).toMatch(/not reset/i);
    expect(startLive).not.toHaveBeenCalled();
  });

  it("does not claim 'not reset' when reset succeeds but resume fails", async () => {
    vi.mocked(resetKillSwitch).mockResolvedValue({ status: "ok" });
    vi.mocked(startLive).mockRejectedValue(new Error("start failed"));

    const r = await resetKillSwitchAndResume();

    expect(r.reset).toBe(true); // the kill switch WAS cleared on the backend
    expect(r.message).not.toMatch(/not reset/i);
    expect(r.message).toMatch(/Start engine/i);
  });
});
