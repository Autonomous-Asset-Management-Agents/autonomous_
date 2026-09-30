import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { useEquityPolling } from "../console/live/useEquityPolling";
import { useStore } from "../console/store/useStore";
import { fetchBenchmarkEquity } from "../lib/api";
import type { BenchmarkEquityResponse } from "../lib/api";

// #2588: the engine's /benchmark-equity went dark (a DB schema-drift exception ->
// {"points": [], "message": "internal_error"} with HTTP 200) and the poll silently
// wrote that EMPTY view into the store + cache — every non-intraday chart range
// showed "Collecting equity history…" and the Since-inception/Started/MaxDD KPIs
// died. The poll must treat an ERROR-empty response as degraded: WARN + keep the
// last good curve. Legitimate empty states ("No benchmark run yet.",
// "no_live_history") still flow through — an empty curve is then the truth.

vi.mock("../lib/api", () => ({
  fetchBenchmarkEquity: vi.fn(),
}));

const mockFetch = vi.mocked(fetchBenchmarkEquity);

const okResp: BenchmarkEquityResponse = {
  points: [
    { date: "2026-07-01", equity: 100000 },
    { date: "2026-07-02", equity: 101000 },
  ],
  spy_points: [
    { date: "2026-07-01", equity: 100000 },
    { date: "2026-07-02", equity: 100500 },
  ],
};

describe("useEquityPolling (#2588 degraded-response guard)", () => {
  beforeEach(() => {
    useStore.setState({ equityCurve: [], benchmarkCurve: [], lastEquity: null });
    mockFetch.mockReset();
    localStorage.clear();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("a successful poll populates the store curves", async () => {
    vi.useFakeTimers();
    mockFetch.mockResolvedValue(okResp);
    const { unmount } = renderHook(() => useEquityPolling());
    await vi.advanceTimersByTimeAsync(0);
    expect(useStore.getState().equityCurve).toHaveLength(2);
    expect(useStore.getState().benchmarkCurve).toHaveLength(2);
    unmount();
  });

  it("an internal_error response keeps the last good curve and warns", async () => {
    vi.useFakeTimers();
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    // First poll succeeds; the second returns the engine's error contract
    // (HTTP 200 + empty points + message) — the good curve MUST survive.
    mockFetch
      .mockResolvedValueOnce(okResp)
      .mockResolvedValue({ points: [], spy_points: [], message: "internal_error" });
    const { unmount } = renderHook(() => useEquityPolling());

    await vi.advanceTimersByTimeAsync(0);
    expect(useStore.getState().equityCurve).toHaveLength(2);

    await vi.advanceTimersByTimeAsync(60_000); // second (degraded) poll

    expect(useStore.getState().equityCurve).toHaveLength(2); // curve kept
    expect(warn).toHaveBeenCalled(); // degraded poll is LOUD, never silent
    unmount();
  });

  it("a fetch_failed response (network error contract) keeps the last good curve", async () => {
    vi.useFakeTimers();
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    mockFetch
      .mockResolvedValueOnce(okResp)
      .mockResolvedValue({ points: [], spy_points: [], message: "fetch_failed" });
    const { unmount } = renderHook(() => useEquityPolling());

    await vi.advanceTimersByTimeAsync(0);
    expect(useStore.getState().equityCurve).toHaveLength(2);

    await vi.advanceTimersByTimeAsync(60_000);

    expect(useStore.getState().equityCurve).toHaveLength(2);
    expect(warn).toHaveBeenCalled();
    unmount();
  });

  it("a legitimate empty state (No benchmark run yet.) still updates the store", async () => {
    vi.useFakeTimers();
    // Seed a stale curve, then let the engine report the honest fresh-account
    // empty state — that MUST overwrite (the empty curve is the truth).
    useStore.setState({
      equityCurve: [
        { t: new Date("2026-07-01T16:00:00Z"), eur: 1 },
        { t: new Date("2026-07-02T16:00:00Z"), eur: 2 },
      ],
    });
    mockFetch.mockResolvedValue({
      points: [],
      spy_points: [],
      message: "No benchmark run yet.",
    });
    const { unmount } = renderHook(() => useEquityPolling());
    await vi.advanceTimersByTimeAsync(0);
    expect(useStore.getState().equityCurve).toHaveLength(0);
    unmount();
  });
});
