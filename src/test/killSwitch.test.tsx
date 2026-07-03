import { act, renderHook, waitFor } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach } from "vitest";

vi.mock("@/lib/api", () => ({ stop: vi.fn().mockResolvedValue({ status: "ok" }) }));
vi.mock("@/lib/killSwitch", () => ({
  resetKillSwitchAndResume: vi
    .fn()
    .mockResolvedValue({ reset: true, message: "Kill switch reset — trading resumed." }),
}));

import { stop } from "@/lib/api";
import { resetKillSwitchAndResume } from "@/lib/killSwitch";
import { useKillSwitch } from "@/console/desktop/useKillSwitch";

describe("useKillSwitch (#1642)", () => {
  beforeEach(() => vi.clearAllMocks());

  it("arms on the first click and only halts (POST /stop) on confirm", async () => {
    const { result } = renderHook(() => useKillSwitch());
    expect(result.current.killArmed).toBe(false);

    await act(async () => {
      await result.current.handleKill();
    });
    expect(result.current.killArmed).toBe(true);
    expect(stop).not.toHaveBeenCalled(); // armed, not yet halted

    await act(async () => {
      await result.current.handleKill();
    });
    expect(stop).toHaveBeenCalledTimes(1);
    expect(result.current.killArmed).toBe(false);
  });

  it("reset clears the switch and resumes, surfacing the outcome message", async () => {
    const { result } = renderHook(() => useKillSwitch());
    await act(async () => {
      await result.current.handleResetKill();
    });
    expect(resetKillSwitchAndResume).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(result.current.killMsg).toMatch(/reset/i));
  });
});
