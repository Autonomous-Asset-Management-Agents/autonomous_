import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

/**
 * UXC-1 S5 (#3158) — "Advanced" section: collapsed-by-default expert area inside the
 * Trading-controls card (owner decision 02.09.: no separate risk-limits card).
 * Groups/fields render data-driven from advancedTradingFields.ts; values come from
 * the S2 GET (engine-effective), edits stay pending until the S3 WORM apply.
 */

vi.mock("../lib/api", () => ({
  getTradingSettings: vi.fn(),
}));

import { getTradingSettings } from "../lib/api";
import { AdvancedTradingSection } from "../console/desktop/AdvancedTradingSection";
import { ADVANCED_GROUPS } from "../console/desktop/advancedTradingFields";
import { payload, setBridge } from "./advancedTradingFixtures";

const mockGet = getTradingSettings as unknown as ReturnType<typeof vi.fn>;

describe("Advanced trading section (UXC-1 S5)", () => {
  beforeEach(() => {
    setBridge();
    mockGet.mockResolvedValue(payload());
  });
  afterEach(() => {
    (window as unknown as { aaagents?: unknown }).aaagents = undefined;
    vi.clearAllMocks();
  });

  it("S1: collapsed by default — expert fields only after an explicit expand", async () => {
    render(<AdvancedTradingSection />);
    const toggle = await waitFor(() => screen.getByRole("button", { name: /advanced/i }));
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByLabelText("Buy threshold")).toBeNull();

    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    await waitFor(() => screen.getByLabelText("Buy threshold"));
  });

  it("S2: expanded, exactly the seven planned groups render", async () => {
    render(<AdvancedTradingSection />);
    fireEvent.click(await waitFor(() => screen.getByRole("button", { name: /advanced/i })));
    for (const g of ADVANCED_GROUPS) {
      expect(await waitFor(() => screen.getByText(g.title))).toBeTruthy();
    }
    expect(ADVANCED_GROUPS).toHaveLength(7);
  });

  it("S3: no non-scope field is editable; the hard stop shows as a read-only guardrail", async () => {
    render(<AdvancedTradingSection />);
    fireEvent.click(await waitFor(() => screen.getByRole("button", { name: /advanced/i })));
    await waitFor(() => screen.getByLabelText("Buy threshold"));

    for (const banned of [
      "TRAILING_STOP_PCT",
      "COMPLIANCE_MAX_DAILY_TRADES",
      "HARD_STOP_LOSS_PCT",
    ]) {
      expect(document.querySelector(`[data-key="${banned}"] input`)).toBeNull();
      expect(document.querySelector(`input[aria-label="${banned}"]`)).toBeNull();
    }
    const guard = screen.getByTestId("guardrail-hard-stop");
    expect(guard.textContent).toContain("8.0");
    expect(guard.querySelector("input")).toBeNull();
  });

  it("S4: values shown are the ENGINE-effective ones from the GET, not UI state", async () => {
    mockGet.mockResolvedValue(payload({ STOP_LOSS_PCT: "5.5" }));
    render(<AdvancedTradingSection />);
    fireEvent.click(await waitFor(() => screen.getByRole("button", { name: /advanced/i })));
    const stop = (await waitFor(() => screen.getByLabelText("Stop loss"))) as HTMLInputElement;
    expect(stop.value).toBe("5.5");
    // engine deviation vs shipped default carries a Changed badge
    await waitFor(() => screen.getByText("Changed"));
  });
});
