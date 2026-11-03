/** STW-7 (#4633) — polling every engine of the fleet for the live/paper comparison. */
import { renderHook, act } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { FleetEngine } from "@/lib/desktopBridge";

const fetchBenchmarkEquityAt = vi.fn();
vi.mock("@/lib/api", async (orig) => ({ ...(await orig<typeof import("@/lib/api")>()), fetchBenchmarkEquityAt: (...a: unknown[]) => fetchBenchmarkEquityAt(...a) }));

const writeEquityCache = vi.fn();
vi.mock("@/console/live/equityCache", async (orig) => ({ ...(await orig<typeof import("@/console/live/equityCache")>()), writeEquityCache: (...a: unknown[]) => writeEquityCache(...a) }));

import { useStore } from "@/console/store/useStore";
import { useAccountComparison } from "@/console/desktop/strategyWorkshop/useAccountComparison";

const FLEET: FleetEngine[] = [
  { id: "p-a", kind: "paper", port: 8002 },
  { id: "live", kind: "live", port: 8001 },
  { id: "p-b", kind: "paper", port: 8003 },
  { id: "p-c", kind: "paper", port: 8004 },
];

const curve = (equity: number) => ({ points: [{ date: "2026-09-01", equity: 1000 }, { date: "2026-09-02", equity }], spy_points: [] });

async function flush() {
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
}

describe("useAccountComparison (#4633)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    vi.spyOn(console, "warn").mockImplementation(() => {});
    fetchBenchmarkEquityAt.mockImplementation(async (port: number) => curve(1000 + port));
  });
  afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

  it("lists live first, then the paper accounts in slot order, labelled Live / Paper account n", async () => {
    const { result } = renderHook(() => useAccountComparison(FLEET));
    expect(result.current.map((a) => a.label)).toEqual(["Live", "Paper account 1", "Paper account 2", "Paper account 3"]);
    expect(result.current.map((a) => a.port)).toEqual([8001, 8002, 8003, 8004]);
    expect(result.current.every((a) => a.state === "loading")).toBe(true);
    await flush();
    expect(result.current.every((a) => a.state === "ok")).toBe(true);
    expect(result.current[0].view!.equityCurve[1].eur).toBe(9001);
  });

  it("polls each engine once per 60 s interval (load = number of engines per interval)", async () => {
    renderHook(() => useAccountComparison(FLEET));
    await flush();
    expect(fetchBenchmarkEquityAt).toHaveBeenCalledTimes(4);
    expect(new Set(fetchBenchmarkEquityAt.mock.calls.map((c) => c[0]))).toEqual(new Set([8001, 8002, 8003, 8004]));
    await act(async () => { await vi.advanceTimersByTimeAsync(59_000); });
    expect(fetchBenchmarkEquityAt).toHaveBeenCalledTimes(4);
    await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
    expect(fetchBenchmarkEquityAt).toHaveBeenCalledTimes(8);
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(fetchBenchmarkEquityAt).toHaveBeenCalledTimes(12);
  });

  it("internal_error / fetch_failed keep the last good curve", async () => {
    const { result } = renderHook(() => useAccountComparison(FLEET));
    await flush();
    fetchBenchmarkEquityAt.mockImplementation(async (port: number) =>
      port === 8002 ? { points: [], spy_points: [], message: "internal_error" } : { points: [], spy_points: [], message: "fetch_failed" },
    );
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(result.current.every((a) => a.state === "ok")).toBe(true);
    expect(result.current[1].view!.equityCurve[1].eur).toBe(9002);
  });

  it("an engine that never answered is unavailable; the others are unaffected", async () => {
    fetchBenchmarkEquityAt.mockImplementation(async (port: number) =>
      port === 8004 ? { points: [], spy_points: [], message: "fetch_failed" } : curve(1000 + port),
    );
    const { result } = renderHook(() => useAccountComparison(FLEET));
    await flush();
    expect(result.current.map((a) => a.state)).toEqual(["ok", "ok", "ok", "unavailable"]);
  });

  it("never writes the Overview's store slot or cache", async () => {
    const setEquity = vi.fn();
    useStore.setState({ setEquity } as never);
    renderHook(() => useAccountComparison(FLEET));
    await flush();
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(setEquity).not.toHaveBeenCalled();
    expect(writeEquityCache).not.toHaveBeenCalled();
  });

  it("stops polling on unmount and polls nothing without a fleet", async () => {
    const { unmount } = renderHook(() => useAccountComparison(FLEET));
    await flush();
    unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(120_000); });
    expect(fetchBenchmarkEquityAt).toHaveBeenCalledTimes(4);

    fetchBenchmarkEquityAt.mockClear();
    const { result } = renderHook(() => useAccountComparison(null));
    await flush();
    expect(result.current).toEqual([]);
    expect(fetchBenchmarkEquityAt).not.toHaveBeenCalled();
  });
});
