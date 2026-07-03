import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { useSpecialistPolling } from "../console/live/useSpecialistPolling";
import { useStore } from "../console/store/useStore";
import { fetchSpecialistReports } from "../lib/api";
import type { SpecialistReportsResponse } from "../lib/api";

vi.mock("../lib/api", () => ({ fetchSpecialistReports: vi.fn() }));

const mockFetch = vi.mocked(fetchSpecialistReports);

const okResp = (symbol: string): SpecialistReportsResponse => ({
  status: "ok",
  total: 1,
  reports: [{ symbol, sentiment_score: 60, recommendation: "buy", ml_direction: "up" }],
});

// The error contract the api layer returns on a network/parse failure — adapts
// to [] (no reports), which the hook must NOT let flash over the last good cards.
const errResp: SpecialistReportsResponse = { status: "error", reports: [] };

describe("useSpecialistPolling", () => {
  beforeEach(() => {
    useStore.setState({ specialistReports: [], specialistStatus: "", specialistMessage: null });
    mockFetch.mockReset();
  });

  it("a successful fetch populates the store", async () => {
    mockFetch.mockResolvedValue(okResp("AAPL"));
    renderHook(() => useSpecialistPolling());

    await waitFor(() => {
      expect(useStore.getState().specialistReports).toHaveLength(1);
    });
    expect(useStore.getState().specialistReports[0].symbol).toBe("AAPL");
    expect(useStore.getState().specialistStatus).toBe("ok");
  });

  it("a failed second poll keeps the last good value (status updates, reports don't flash empty)", async () => {
    vi.useFakeTimers();
    try {
      // First poll succeeds and seeds a card; the next poll returns the error
      // contract (empty reports) — the prior cards MUST survive.
      mockFetch.mockResolvedValueOnce(okResp("MSFT")).mockResolvedValue(errResp);
      renderHook(() => useSpecialistPolling());

      // Flush the first (immediate) poll's microtasks.
      await vi.advanceTimersByTimeAsync(0);
      expect(useStore.getState().specialistReports).toHaveLength(1);
      expect(useStore.getState().specialistReports[0].symbol).toBe("MSFT");

      // Advance past the 60s poll gap to trigger the failing second poll.
      await vi.advanceTimersByTimeAsync(60_000);

      expect(useStore.getState().specialistStatus).toBe("error"); // status refreshed
      expect(useStore.getState().specialistReports).toHaveLength(1); // cards kept
      expect(useStore.getState().specialistReports[0].symbol).toBe("MSFT");
    } finally {
      vi.useRealTimers();
    }
  });
});
