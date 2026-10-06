import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

/**
 * #3361 — read-only "Market regime reading" block. It shows what the throttle would
 * do at the APPLIED settings (also while off), never crashes on a data gap, and
 * carries no advice wording.
 */

vi.mock("../lib/api", () => ({
  getRegimePreview: vi.fn(),
}));

import { getRegimePreview } from "../lib/api";
import { RegimePreviewBlock } from "../console/desktop/RegimePreviewBlock";

const mockGet = getRegimePreview as unknown as ReturnType<typeof vi.fn>;

const reading = (over: Record<string, unknown> = {}) => ({
  status: "success",
  available: true,
  asof: "2026-09-10",
  score: 85.65,
  threshold: 67.0,
  threshold_percentile: 75,
  components: { credit: 86.8, rates: 83.4, oil: 89.4, correlation: null, spy_drawdown: 78.1 },
  weights: { credit: 0.4, rates: 0.2, oil: 0.2, correlation: 0.1, spy_drawdown: 0.1 },
  history_days: 180,
  would_throttle: true,
  factor: 0.5,
  enabled: true,
  size_factor_setting: 0.5,
  ...over,
});

describe("Regime preview block (#3361)", () => {
  afterEach(() => vi.clearAllMocks());

  it("R1: armed and elevated — shows reading, threshold and the applied multiplier", async () => {
    mockGet.mockResolvedValue(reading());
    render(<RegimePreviewBlock />);
    const state = await waitFor(() => screen.getByTestId("regime-preview-state"));
    expect(state.textContent).toBe("× 0.50");
    expect(screen.getByText("85.7")).toBeTruthy();
    expect(screen.getByText("67.0")).toBeTruthy();
    expect(screen.getByText(/as of 2026-09-10/)).toBeTruthy();
  });

  it("R2: throttle off — states what would apply, clearly conditional", async () => {
    mockGet.mockResolvedValue(reading({ enabled: false }));
    render(<RegimePreviewBlock />);
    const state = await waitFor(() => screen.getByTestId("regime-preview-state"));
    expect(state.textContent).toBe("× 0.50 if on");
  });

  it("R3: calm reading — normal size", async () => {
    mockGet.mockResolvedValue(reading({ score: 40, would_throttle: false, factor: 1 }));
    render(<RegimePreviewBlock />);
    const state = await waitFor(() => screen.getByTestId("regime-preview-state"));
    expect(state.textContent).toBe("Normal size");
  });

  it("R4: a missing component renders a dash, never NaN", async () => {
    mockGet.mockResolvedValue(reading());
    render(<RegimePreviewBlock />);
    await waitFor(() => screen.getByTestId("regime-preview-state"));
    expect(screen.getByTestId("regime-preview").textContent).not.toContain("NaN");
    expect(screen.getByTestId("regime-preview").textContent).toContain("—");
  });

  it("R5: data gap and request failure both degrade to the neutral line", async () => {
    mockGet.mockResolvedValue({ status: "error", available: false, would_throttle: false, factor: 1 });
    const { unmount } = render(<RegimePreviewBlock />);
    await waitFor(() => screen.getByText(/No current reading available/));
    unmount();
    mockGet.mockRejectedValue(new Error("engine down"));
    render(<RegimePreviewBlock />);
    await waitFor(() => screen.getByText(/No current reading available/));
  });

  it("R6: no advice wording", async () => {
    mockGet.mockResolvedValue(reading());
    render(<RegimePreviewBlock />);
    await waitFor(() => screen.getByTestId("regime-preview-state"));
    const text = screen.getByTestId("regime-preview").textContent ?? "";
    for (const re of [/recommend/i, /should/i, /safe/i, /risky/i, /advice/i]) {
      expect(text).not.toMatch(re);
    }
  });
});
