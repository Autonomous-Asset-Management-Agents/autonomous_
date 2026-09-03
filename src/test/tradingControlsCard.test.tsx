import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { TradingControlsCard } from "../console/desktop/TradingControlsCard";

/**
 * "Trading controls" card (Settings → Trading). Two INDIVIDUAL, neutral engine
 * controls the user sets themselves — no profiles, no advice. Values written 1:1 to
 * setup.json under the exact engine flag names (#2863 Option A).
 */
const setBridge = (impl: Record<string, unknown> | undefined) => {
  (window as unknown as { aaagents?: unknown }).aaagents = impl;
};

const bridge = (setupState: Record<string, unknown> = {}) => ({
  isDesktop: true,
  getSetupState: vi.fn().mockResolvedValue(setupState),
  saveSetupState: vi.fn().mockResolvedValue(undefined),
});

const saveMock = () =>
  (window as unknown as { aaagents: { saveSetupState: ReturnType<typeof vi.fn> } })
    .aaagents.saveSetupState;

// UXC-1 S8 (#3177): the iOS Switch became an OnOffSegment — a role="group" carrying the
// same aria-label, with explicit On/Off buttons (aria-pressed marks the active state).
const sizingSegment = () =>
  within(screen.getByRole("group", { name: "Volatility sizing" }));
const sizingState = (label: "On" | "Off") =>
  sizingSegment().getByRole("button", { name: label }).getAttribute("aria-pressed");

describe("Trading controls card", () => {
  beforeEach(() => setBridge(bridge()));
  afterEach(() => {
    setBridge(undefined);
    vi.clearAllMocks();
  });

  it("renders the two neutral controls with shipped defaults (sizing on → target shown)", async () => {
    render(<TradingControlsCard />);
    expect(screen.getByText("Trading controls")).toBeTruthy();
    expect(sizingState("On")).toBe("true"); // default ON
    expect((screen.getByLabelText("Daily vol target") as HTMLInputElement).value).toBe("0.015");
  });

  it("loads existing setup.json values (sizing off → target field hidden)", async () => {
    setBridge(bridge({ VOL_TARGETING_SIZING_ENABLED: "false", VOL_TARGET_DAILY_VOL: "0.010" }));
    render(<TradingControlsCard />);
    await waitFor(() => {
      expect(sizingState("Off")).toBe("true");
    });
    expect(screen.queryByLabelText("Daily vol target")).toBeNull();
  });

  it("toggling Volatility sizing writes the flag to setup.json", async () => {
    render(<TradingControlsCard />);
    await waitFor(() => screen.getByRole("group", { name: "Volatility sizing" }));
    fireEvent.click(sizingSegment().getByRole("button", { name: "Off" })); // ON -> OFF
    expect(saveMock()).toHaveBeenCalledWith({ VOL_TARGETING_SIZING_ENABLED: "false" });
    // and the target field disappears while off
    expect(screen.queryByLabelText("Daily vol target")).toBeNull();
  });

  it("a valid Daily vol target is persisted; an invalid one shows an error and writes nothing", async () => {
    render(<TradingControlsCard />);
    const input = await waitFor(() => screen.getByLabelText("Daily vol target"));

    fireEvent.change(input, { target: { value: "0.008" } });
    expect(saveMock()).toHaveBeenCalledWith({ VOL_TARGET_DAILY_VOL: "0.008" });

    saveMock().mockClear();
    fireEvent.change(input, { target: { value: "abc" } });
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(saveMock()).not.toHaveBeenCalled();

    saveMock().mockClear();
    fireEvent.change(input, { target: { value: "9" } }); // out of range
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(saveMock()).not.toHaveBeenCalled();
  });

  it("the Daily vol target slider carries the sane bounds and persists a dragged value", async () => {
    render(<TradingControlsCard />);
    const slider = (await waitFor(() =>
      screen.getByLabelText("Daily vol target slider"),
    )) as HTMLInputElement;
    expect(slider.getAttribute("type")).toBe("range");
    // Bounds are the vol-scaler active range (below/above these the scaler is fully pinned).
    expect(slider.min).toBe("0.007");
    expect(slider.max).toBe("0.075");
    fireEvent.change(slider, { target: { value: "0.02" } });
    expect(saveMock()).toHaveBeenCalledWith({ VOL_TARGET_DAILY_VOL: "0.02" });
  });

  it("Default settings resets both controls to the shipped defaults", async () => {
    setBridge(bridge({ VOL_TARGETING_SIZING_ENABLED: "false", VOL_TARGET_DAILY_VOL: "0.006" }));
    render(<TradingControlsCard />);
    await waitFor(() => {
      expect(sizingState("Off")).toBe("true");
    });
    fireEvent.click(screen.getByRole("button", { name: /default settings/i }));
    expect(saveMock()).toHaveBeenCalledWith({
      VOL_TARGETING_SIZING_ENABLED: "true",
      VOL_TARGET_DAILY_VOL: "0.015",
    });
    // reset restored the ON state (target visible again)
    await waitFor(() => screen.getByLabelText("Daily vol target"));
  });

  it("neutrality: no profile names, no recommendation wording, no measured figures", async () => {
    render(<TradingControlsCard />);
    const html = document.body.textContent || "";
    for (const banned of [
      /profil/i, /empfohlen/i, /aggressiv/i, /kapitalerhalt/i, /sharpe/i, /drawdown/i, /%/,
    ]) {
      expect(banned.test(html)).toBe(false);
    }
  });
});
