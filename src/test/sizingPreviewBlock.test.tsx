import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

/**
 * #5005 — read-only effect block of the Sizing group: the target of a new position,
 * cash per open slot and the limit that binds, as a model calculation without
 * single-name signals. Never crashes on an unreachable engine, carries no advice.
 */

vi.mock("../lib/api", () => ({
  getSizingPreview: vi.fn(),
}));

import { getSizingPreview } from "../lib/api";
import { SizingPreviewBlock } from "../console/desktop/SizingPreviewBlock";

const mockGet = getSizingPreview as unknown as ReturnType<typeof vi.fn>;

const preview = (over: Record<string, unknown> = {}) => ({
  status: "success",
  available: true,
  sizing_mode: "b",
  target_weight: 0.1,
  target_value: 2000,
  order_value: 100,
  cash_per_slot: 100,
  slots: 10,
  binding_limit: "cash",
  binding_cap_value: 100,
  zero_reason: null,
  ...over,
});

describe("Sizing preview block (#5005)", () => {
  afterEach(() => vi.clearAllMocks());

  it("S1: method b, cash below the slot — target, cash per slot and the binding limit", async () => {
    mockGet.mockResolvedValue(preview());
    render(<SizingPreviewBlock />);
    const limit = await waitFor(() => screen.getByTestId("sizing-preview-limit"));
    expect(limit.textContent).toBe("cash");
    expect(screen.getByTestId("sizing-preview-target").textContent).toBe("10% · $2,000");
    expect(screen.getByTestId("sizing-preview-cash").textContent).toBe("$100");
    expect(screen.getByTestId("sizing-preview-method").textContent).toContain(
      "Equal weight with risk tilts",
    );
  });

  it("S2: says it is a model calculation without single-name signals", async () => {
    mockGet.mockResolvedValue(preview());
    render(<SizingPreviewBlock />);
    await waitFor(() => screen.getByTestId("sizing-preview-limit"));
    expect(screen.getByTestId("sizing-preview").textContent).toMatch(
      /model calculation without single-name signals/i,
    );
  });

  it("S3: nothing binds — states that, not an empty cell", async () => {
    mockGet.mockResolvedValue(preview({ binding_limit: null, binding_cap_value: null }));
    render(<SizingPreviewBlock />);
    const limit = await waitFor(() => screen.getByTestId("sizing-preview-limit"));
    expect(limit.textContent).toBe("None");
  });

  it("S4: a zero without a binding limit names the zero reason", async () => {
    mockGet.mockResolvedValue(
      preview({ binding_limit: null, zero_reason: "exposure_check_failed" }),
    );
    render(<SizingPreviewBlock />);
    const limit = await waitFor(() => screen.getByTestId("sizing-preview-limit"));
    expect(limit.textContent).toBe("exposure check failed");
  });

  it("S5: engine not reachable and data gap both degrade to the neutral line", async () => {
    mockGet.mockResolvedValue({ status: "error", available: false });
    const { unmount } = render(<SizingPreviewBlock />);
    await waitFor(() => screen.getByText(/No model calculation available/));
    unmount();
    mockGet.mockRejectedValue(new Error("engine down"));
    render(<SizingPreviewBlock />);
    await waitFor(() => screen.getByText(/No model calculation available/));
  });

  it("S6: missing values render a dash, never NaN", async () => {
    mockGet.mockResolvedValue(preview({ target_weight: null, cash_per_slot: undefined }));
    render(<SizingPreviewBlock />);
    await waitFor(() => screen.getByTestId("sizing-preview-limit"));
    const text = screen.getByTestId("sizing-preview").textContent ?? "";
    expect(text).not.toContain("NaN");
    expect(text).toContain("—");
  });

  it("S7: no advice wording", async () => {
    mockGet.mockResolvedValue(preview());
    render(<SizingPreviewBlock />);
    await waitFor(() => screen.getByTestId("sizing-preview-limit"));
    const text = screen.getByTestId("sizing-preview").textContent ?? "";
    for (const re of [/recommend/i, /should/i, /safe/i, /risky/i, /advice/i]) {
      expect(text).not.toMatch(re);
    }
  });
});
