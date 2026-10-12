import { describe, it, expect, vi, afterEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { makeBridge, installBridge, resetBridge } from "./fixtures/mockBridge";
import {
  useProvisionStatus,
  PROVISION_STALL_MS,
  PROVISION_STALL_ERROR,
} from "@/console/splash/useProvisionStatus";
import { PROVISION_IDLE } from "@/console/splash/bootStatus";

// B4: the first-launch provisioning state for ConsoleApp / BootSplash. It follows the shell's
// engine-log stream (replay on mount + live lines, exactly as useEngine does), publishes a new
// object only when the derived state changes, and reports a stall when the shell's ~500 ms
// heartbeat has been silent for PROVISION_STALL_MS (a hung main process must not spin forever).

describe("useProvisionStatus", () => {
  afterEach(() => {
    resetBridge();
    vi.useRealTimers();
  });

  it("no bridge (browser build): idle, nothing subscribed", () => {
    const { result } = renderHook(() => useProvisionStatus());
    expect(result.current).toBe(PROVISION_IDLE);
  });

  it("replays the shell's buffered lines on mount", async () => {
    const b = makeBridge({
      engineStatus: "stopped",
      logs: ["First launch: fetching the engine runtime (a few minutes, once).", "[provisioning] 23% Python-Laufzeit"],
    });
    installBridge(b.bridge);
    const { result } = renderHook(() => useProvisionStatus());
    await waitFor(() => expect(result.current.percent).toBe(23));
    expect(result.current).toEqual({ active: true, percent: 23, stage: "Python-Laufzeit", error: null });
  });

  it("follows live lines and keeps the same object while nothing changed (heartbeat repeats)", async () => {
    const b = makeBridge({ engineStatus: "stopped", logs: [] });
    installBridge(b.bridge);
    const { result } = renderHook(() => useProvisionStatus());
    await act(async () => {}); // let the (empty) replay settle
    act(() => b.emitLog("[provisioning] 23% Python-Laufzeit"));
    const first = result.current;
    expect(first.percent).toBe(23);
    act(() => b.emitLog("[provisioning] 23% Python-Laufzeit")); // heartbeat re-emits the same tick
    act(() => b.emitLog('INFO: 127.0.0.1 - "GET /health" 200')); // unrelated line
    expect(result.current).toBe(first);
    act(() => b.emitLog("[provisioning] 32% Prüfen"));
    expect(result.current).toEqual({ active: true, percent: 32, stage: "Prüfen", error: null });
    act(() => b.emitLog("[provisioning] 32% Prüfen — SHA-256 mismatch for x"));
    expect(result.current.active).toBe(false);
    expect(result.current.error).toBe("SHA-256 mismatch for x");
  });

  it("live lines that arrive before the replay resolves are applied AFTER it (no regression)", async () => {
    const b = makeBridge({ engineStatus: "stopped" });
    let resolveReplay: (lines: string[]) => void = () => {};
    b.bridge.getEngineLogs = () => new Promise<string[]>((r) => (resolveReplay = r));
    installBridge(b.bridge);
    const { result } = renderHook(() => useProvisionStatus());
    act(() => b.emitLog("[provisioning] 40% Prüfen")); // newer than anything the replay can hold
    await act(async () => {
      resolveReplay(["[provisioning] 23% Python-Laufzeit"]);
    });
    await waitFor(() => expect(result.current.percent).toBe(40));
    expect(result.current.stage).toBe("Prüfen");
  });

  it("reports a stall when no progress line arrives for PROVISION_STALL_MS, and recovers on the next one", async () => {
    vi.useFakeTimers();
    const b = makeBridge({ engineStatus: "stopped", logs: [] });
    installBridge(b.bridge);
    const { result } = renderHook(() => useProvisionStatus());
    await act(async () => {}); // replay settles
    act(() => b.emitLog("[provisioning] 23% Python-Laufzeit"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(PROVISION_STALL_MS - 1000);
    });
    act(() => b.emitLog("[provisioning] 23% Python-Laufzeit")); // heartbeat keeps it alive
    await act(async () => {
      await vi.advanceTimersByTimeAsync(PROVISION_STALL_MS - 1000);
    });
    expect(result.current.active).toBe(true);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000); // now silent for the full window
    });
    expect(result.current.active).toBe(false);
    expect(result.current.error).toBe(PROVISION_STALL_ERROR);
    act(() => b.emitLog("[provisioning] 24% Python-Laufzeit"));
    expect(result.current).toEqual({ active: true, percent: 24, stage: "Python-Laufzeit", error: null });
  });

  it("a finished or failed phase never stalls (nothing is expected any more)", async () => {
    vi.useFakeTimers();
    const b = makeBridge({ engineStatus: "stopped", logs: [] });
    installBridge(b.bridge);
    const { result } = renderHook(() => useProvisionStatus());
    await act(async () => {});
    act(() => b.emitLog("Provisioning failed: network died"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(PROVISION_STALL_MS + 1000);
    });
    expect(result.current.error).toBe("network died");
  });

  it("unsubscribes on unmount", async () => {
    const b = makeBridge({ engineStatus: "stopped", logs: [] });
    installBridge(b.bridge);
    const { result, unmount } = renderHook(() => useProvisionStatus());
    await act(async () => {});
    unmount();
    act(() => b.emitLog("[provisioning] 23% Python-Laufzeit"));
    expect(result.current).toBe(PROVISION_IDLE);
  });
});
