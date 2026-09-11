import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";

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

  it("S1b: the collapsed header advertises the parameter count and a content hint (discoverability)", async () => {
    render(<AdvancedTradingSection />);
    const toggle = await waitFor(() => screen.getByRole("button", { name: /advanced/i }));
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    // A parameter-count badge + a hint of what's inside — no longer a bare "Show".
    expect(toggle.textContent).toMatch(/\d+ parameters/);
    expect(toggle.textContent?.toLowerCase()).toContain("sizing");
  });

  it("S2: expanded, exactly the nine planned groups render", async () => {
    render(<AdvancedTradingSection />);
    fireEvent.click(await waitFor(() => screen.getByRole("button", { name: /advanced/i })));
    for (const g of ADVANCED_GROUPS) {
      expect(await waitFor(() => screen.getByText(g.title))).toBeTruthy();
    }
    expect(ADVANCED_GROUPS).toHaveLength(9);
  });

  it("S7: the Displacement group exposes the switch + the (global) consensus-retention slider", async () => {
    render(<AdvancedTradingSection />);
    fireEvent.click(await waitFor(() => screen.getByRole("button", { name: /advanced/i })));
    await waitFor(() => screen.getByText("Displacement"));
    expect(screen.getByText("Displacement exits")).toBeTruthy();
    // the consensus-retention slider is always shown (it also governs rotation/overflow)
    expect(screen.getByLabelText("Consensus retention")).toBeTruthy();
  });

  it("S3: no non-scope field is editable; the hard stop shows as a read-only guardrail", async () => {
    render(<AdvancedTradingSection />);
    fireEvent.click(await waitFor(() => screen.getByRole("button", { name: /advanced/i })));
    await waitFor(() => screen.getByLabelText("Buy threshold"));

    // #3220/#3221: COMPLIANCE_MAX_DAILY_TRADES ist seit dem Owner-Entscheid 05.09.
    // ein einstellbares Betriebslimit innerhalb des ratifizierten Ceilings (50) -
    // keine Leitplanke mehr, also hier bewusst nicht mehr gelistet.
    for (const banned of [
      "TRAILING_STOP_PCT",
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

  it("S6: dependent rotation fields are HIDDEN while rotation exits are off (no inert knobs)", async () => {
    mockGet.mockResolvedValue(payload({ ROTATION_EXIT_ENABLED: "false" }));
    render(<AdvancedTradingSection />);
    fireEvent.click(await waitFor(() => screen.getByRole("button", { name: /advanced/i })));
    // the group and its parent toggle still render
    await waitFor(() => screen.getByText("Rotation"));
    expect(screen.getByText("Rotation exits")).toBeTruthy();
    // the three dependents are gone entirely (not merely disabled)
    expect(screen.queryByText("Max exits per cycle")).toBeNull();
    expect(screen.queryByText("Max exits per session")).toBeNull();
    expect(screen.queryByText("Ranking panel max age")).toBeNull();
    expect(screen.queryByLabelText("Max exits per cycle")).toBeNull();
  });

  it("S6b: the dependent rotation fields reappear when rotation exits are on", async () => {
    mockGet.mockResolvedValue(payload({ ROTATION_EXIT_ENABLED: "true" }));
    render(<AdvancedTradingSection />);
    fireEvent.click(await waitFor(() => screen.getByRole("button", { name: /advanced/i })));
    await waitFor(() => screen.getByLabelText("Max exits per cycle"));
    expect(screen.getByLabelText("Max exits per session")).toBeTruthy();
    expect(screen.getByLabelText("Ranking panel max age")).toBeTruthy();
  });

  it("S6c: toggling rotation off live hides the dependents at once (pending-aware)", async () => {
    mockGet.mockResolvedValue(payload({ ROTATION_EXIT_ENABLED: "true" }));
    render(<AdvancedTradingSection />);
    fireEvent.click(await waitFor(() => screen.getByRole("button", { name: /advanced/i })));
    await waitFor(() => screen.getByLabelText("Max exits per cycle"));
    // flip the parent toggle to Off (segmented control → click the "Off" segment)
    const rotationToggle = screen.getByRole("group", { name: "Rotation exits" });
    fireEvent.click(within(rotationToggle).getByRole("button", { name: "Off" }));
    await waitFor(() => expect(screen.queryByLabelText("Max exits per cycle")).toBeNull());
  });
});
