import { act, renderHook, waitFor } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach } from "vitest";

vi.mock("@/lib/api", () => ({
  cancelOrder: vi.fn().mockResolvedValue({ status: "success" }),
  fetchOpenOrders: vi.fn().mockResolvedValue({ status: "success", orders: [] }), stop: vi.fn().mockResolvedValue({ status: "ok" }) }));
vi.mock("@/lib/killSwitch", () => ({
  resetKillSwitchAndResume: vi
    .fn()
    .mockResolvedValue({ reset: true, message: "Kill switch reset — trading resumed." }),
}));

import { stop } from "@/lib/api";
import { resetKillSwitchAndResume } from "@/lib/killSwitch";
import { useKillSwitch } from "@/console/desktop/useKillSwitch";
import { useStore } from "@/console/store/useStore";

describe("useKillSwitch (#1642)", () => {
  beforeEach(() => vi.clearAllMocks());

  it("#2467: handleKill raises the emergency switch-to-paper signal (real kill), NOT a soft POST /stop", async () => {
    useStore.setState({ requestSwitchToPaper: false });
    const { result } = renderHook(() => useKillSwitch());
    await act(async () => {
      await result.current.handleKill();
    });
    // The KILL SWITCH is a REAL emergency stop: it triggers the audited toPaper path in the global
    // LiveConsentModal (liveDisable → kill_switch.trip() block+mass-cancel + force_paper + WORM →
    // restart fail-closed to Paper), NOT the old soft /stop that neither trips nor cancels nor WORMs.
    expect(useStore.getState().requestSwitchToPaper).toBe(true);
    expect(stop).not.toHaveBeenCalled();
  });

  it("reset clears the switch and resumes, surfacing the outcome message", async () => {
    const { result } = renderHook(() => useKillSwitch());
    await act(async () => {
      await result.current.handleResetKill();
    });
    expect(resetKillSwitchAndResume).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(useStore.getState().sidebarStatusMsg).toMatch(/reset/i));
  });

  it("reset FAILURE surfaces the real failure message, never a false 'successful' (CQ-HIGH)", async () => {
    vi.mocked(resetKillSwitchAndResume).mockResolvedValueOnce({
      reset: false,
      message: "Could not reach the engine — kill switch not reset.",
    });
    const { result } = renderHook(() => useKillSwitch());
    await act(async () => {
      await result.current.handleResetKill();
    });
    await waitFor(() =>
      expect(useStore.getState().sidebarStatusMsg).toMatch(/not reset/i),
    );
    expect(useStore.getState().sidebarStatusMsg).not.toMatch(/successful/i);
  });
});
