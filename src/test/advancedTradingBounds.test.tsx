import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

/** UXC-1 S5 (#3158) — client-mirrored bounds/cross-field rules (server stays authoritative). */

vi.mock("../lib/api", () => ({
  getTradingSettings: vi.fn(),
}));

import { getTradingSettings } from "../lib/api";
import { AdvancedTradingSection } from "../console/desktop/AdvancedTradingSection";
import { payload, setBridge } from "./advancedTradingFixtures";

const mockGet = getTradingSettings as unknown as ReturnType<typeof vi.fn>;

async function expand() {
  fireEvent.click(await waitFor(() => screen.getByRole("button", { name: /advanced/i })));
  await waitFor(() => screen.getByLabelText("Buy threshold"));
}

describe("Advanced trading bounds (UXC-1 S5)", () => {
  beforeEach(() => {
    setBridge();
    mockGet.mockResolvedValue(payload());
  });
  afterEach(() => {
    (window as unknown as { aaagents?: unknown }).aaagents = undefined;
    vi.clearAllMocks();
  });

  it("B1: threshold sliders carry the tight bands 0.55-0.75 / 0.25-0.45", async () => {
    render(<AdvancedTradingSection />);
    await expand();
    const buy = screen.getByLabelText("Buy threshold") as HTMLInputElement;
    expect(buy.min).toBe("0.55");
    expect(buy.max).toBe("0.75");
    const sell = screen.getByLabelText("Sell threshold") as HTMLInputElement;
    expect(sell.min).toBe("0.25");
    expect(sell.max).toBe("0.45");
  });

  it("B2: the broker stop distance slider ends strictly below the 8.0 hard stop", async () => {
    render(<AdvancedTradingSection />);
    await expand();
    const stop = screen.getByLabelText("Broker stop distance") as HTMLInputElement;
    expect(Number(stop.max)).toBeLessThan(8.0);
  });

  it("B2b: #4999 the ends of a slider read in the unit, its range stays the stored one", async () => {
    render(<AdvancedTradingSection />);
    await expand();
    const row = (key: string) => document.querySelector(`[data-key="${key}"]`) as HTMLElement;
    const ends = (key: string) =>
      Array.from(row(key).querySelectorAll('[data-copy="bound"]')).map((e) => e.textContent);
    expect(ends("MAX_POSITION_PERCENT")).toEqual(["5%", "40%"]);
    expect(ends("COMPLIANCE_MAX_ORDER_VALUE")).toEqual(["$1,000", "$100,000"]);
    expect(ends("LOSS_CUT_PCT")).toEqual(["-7.9%", "-1%"]);
    expect(ends("STOP_LOSS_PCT")).toEqual(["-1%", "-7.5%"]);
    expect(ends("GLOBAL_BUY_COOLDOWN_MINUTES")).toEqual(["0 min", "240 min"]);
    expect(ends("SIGNAL_BUY_THRESHOLD")).toEqual(["0.55", "0.75"]);
    const max = screen.getByLabelText("Maximum position size") as HTMLInputElement;
    expect(max.min).toBe("0.05");
    expect(max.max).toBe("0.4");
  });

  it("B3: rotation counters start at 1 — the <=0 footguns are not offered", async () => {
    render(<AdvancedTradingSection />);
    await expand();
    expect((screen.getByLabelText("Max exits per cycle") as HTMLInputElement).min).toBe("1");
    expect((screen.getByLabelText("Max exits per session") as HTMLInputElement).min).toBe("1");
  });

  it("B4: the trailing rule set offers exactly midterm and legacy", async () => {
    render(<AdvancedTradingSection />);
    await expand();
    const select = screen.getByLabelText("Trailing rule set") as HTMLSelectElement;
    expect(Array.from(select.options).map((o) => o.value)).toEqual(["midterm", "legacy"]);
  });

  it("B5: sizing cross-field violation disables Apply with a factual message", async () => {
    const onApply = vi.fn();
    render(<AdvancedTradingSection onApply={onApply} />);
    await expand();
    // MAX_POSITION_PERCENT above the sizing cap (0.30) violates MIN < MAX <= CAP
    fireEvent.change(screen.getByLabelText("Maximum position size"), {
      target: { value: "0.35" },
    });
    const apply = await waitFor(
      () => screen.getByRole("button", { name: /apply changes/i }) as HTMLButtonElement,
    );
    expect(apply.disabled).toBe(true);
    expect(screen.getByRole("alert").textContent).toMatch(/sizing cap/i);
    fireEvent.click(apply);
    expect(onApply).not.toHaveBeenCalled();
  });

  it("B6: MIN_POSITION_PERCENT above the equal-weight slot of the configured book disables Apply", async () => {
    // slot = 0.05
    mockGet.mockResolvedValue({ ...payload({ CLEAN_WEIGHT_SIZING: "off" }), effective_positions: 20 });
    render(<AdvancedTradingSection />);
    await expand();
    fireEvent.change(screen.getByLabelText("Minimum position size"), {
      target: { value: "0.08" },
    });
    const apply = await waitFor(
      () => screen.getByRole("button", { name: /apply changes/i }) as HTMLButtonElement,
    );
    expect(apply.disabled).toBe(true);
  });

  it("B6b: #4987 a LOCKED minimum above the slot does not block Apply — it cannot be lowered here, the server clamps it", async () => {
    mockGet.mockResolvedValue({
      ...payload({ CLEAN_WEIGHT_SIZING: "b", MIN_POSITION_PERCENT: "0.08" }),
      effective_positions: 20,
    });
    render(<AdvancedTradingSection />);
    await expand();
    await waitFor(() => screen.getByLabelText("Sizing method"));
    expect(screen.queryByRole("alert")).toBeNull();
    const apply = screen.getByRole("button", { name: /apply changes/i }) as HTMLButtonElement;
    expect(apply.disabled).toBe(false);
  });

  it("B7: ROUND_TABLE_TOP_K_EVAL at or below the configured position count disables Apply", async () => {
    mockGet.mockResolvedValue({ ...payload(), effective_positions: 20 });
    render(<AdvancedTradingSection />);
    await expand();
    fireEvent.change(screen.getByLabelText("Round-table candidates"), {
      target: { value: "15" },
    });
    const apply = await waitFor(
      () => screen.getByRole("button", { name: /apply changes/i }) as HTMLButtonElement,
    );
    expect(apply.disabled).toBe(true);
  });

  it("#5007: without a stored position count the slot rule uses the engine's 20", async () => {
    mockGet.mockResolvedValue({ ...payload({ CLEAN_WEIGHT_SIZING: "off" }), effective_positions: 20 });
    render(<AdvancedTradingSection />);
    await expand();
    fireEvent.change(screen.getByLabelText("Minimum position size"), {
      target: { value: "0.08" },
    });
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("1/20"));
  });

  it("#5007: the engine's count wins over a stale setup.json value", async () => {
    setBridge({ FULL_UNIVERSE_MAX_POSITIONS: "20" });
    mockGet.mockResolvedValue({ ...payload({ CLEAN_WEIGHT_SIZING: "off" }), effective_positions: 10 });
    render(<AdvancedTradingSection />);
    await expand();
    fireEvent.change(screen.getByLabelText("Minimum position size"), {
      target: { value: "0.08" },
    });
    await new Promise((r) => setTimeout(r));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("#5007: without an engine count the slot rules drop out, with a visible note", async () => {
    setBridge({ FULL_UNIVERSE_MAX_POSITIONS: "20" });
    mockGet.mockResolvedValue({
      ...payload({ CLEAN_WEIGHT_SIZING: "off", ROUND_TABLE_TOP_K_EVAL: "5" }),
      effective_positions: undefined,
    });
    render(<AdvancedTradingSection />);
    await expand();
    fireEvent.change(screen.getByLabelText("Minimum position size"), {
      target: { value: "0.08" },
    });
    await waitFor(() => expect(screen.getByTestId("slot-check-skipped")).toBeTruthy());
    expect(screen.queryByRole("alert")).toBeNull();
    const apply = screen.getByRole("button", { name: /apply changes/i }) as HTMLButtonElement;
    expect(apply.disabled).toBe(false);
  });
});
