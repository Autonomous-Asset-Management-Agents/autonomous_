import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { TradingControlsCard } from "../console/desktop/TradingControlsCard";

/**
 * "Trading controls" card (Settings → Trading). Two INDIVIDUAL, neutral engine
 * controls the user sets themselves — no profiles, no advice.
 *
 * #4447: Volatility sizing and Daily vol target are no longer written on every change.
 * They go through the confirmed registry flow (SettingsApplyModal: record on the WORM
 * chain via POST /api/trading-settings → persist to setup.json → restart), like every
 * other trading setting. Bounds come from the registry meta (GET /api/trading-settings).
 */
const setBridge = (impl: Record<string, unknown> | undefined) => {
  (window as unknown as { aaagents?: unknown }).aaagents = impl;
};

const restartMock = vi.fn().mockResolvedValue(undefined);
const bridge = (setupState: Record<string, unknown> = {}) => ({
  isDesktop: true,
  getSetupState: vi.fn().mockResolvedValue(setupState),
  saveSetupState: vi.fn().mockResolvedValue(undefined),
  restartEngine: restartMock,
});

const saveMock = () =>
  (window as unknown as { aaagents: { saveSetupState: ReturnType<typeof vi.fn> } })
    .aaagents.saveSetupState;

const postSettings = vi.fn();
const getSettings = vi.fn();
vi.mock("@/lib/api", () => ({
  postTradingSettings: (...a: unknown[]) => postSettings(...a),
  getTradingSettings: (...a: unknown[]) => getSettings(...a),
  savePortfolioShape: vi.fn(),
  fetchHealth: vi.fn().mockResolvedValue({ status: "healthy", paper_trading: true }),
  postSwitchReconcile: vi.fn().mockResolvedValue(undefined),
}));

const META = {
  agents: [],
  implied_vol_forecast_enabled: true,
  settings: {},
  guardrails: {},
  meta: [
    { key: "VOL_TARGETING_SIZING_ENABLED", kind: "bool", default: "true" },
    { key: "VOL_TARGET_DAILY_VOL", kind: "float", default: "0.0150", min: 0.007, max: 0.075 },
  ],
};

// UXC-1 S8 (#3177): the iOS Switch became an OnOffSegment — a role="group" carrying the
// same aria-label, with explicit On/Off buttons (aria-pressed marks the active state).
const sizingSegment = () =>
  within(screen.getByRole("group", { name: "Volatility sizing" }));
const sizingState = (label: "On" | "Off") =>
  sizingSegment().getByRole("button", { name: label }).getAttribute("aria-pressed");

const applyVol = () => screen.getByRole("button", { name: /apply volatility settings/i });

describe("Trading controls card", () => {
  beforeEach(() => {
    setBridge(bridge());
    getSettings.mockResolvedValue(META);
    postSettings.mockImplementation(async (settings: Record<string, string>) => ({
      success: true,
      applied: settings,
      changes: Object.entries(settings).map(([key, to]) => ({ key, from: "x", to })),
      detail: "recorded",
    }));
  });
  afterEach(() => {
    setBridge(undefined);
    vi.clearAllMocks();
  });

  it("renders the two neutral controls with shipped defaults (sizing on → target shown)", async () => {
    render(<TradingControlsCard />);
    expect(screen.getByText("Trading controls")).toBeTruthy();
    expect(sizingState("On")).toBe("true"); // default ON
    expect((screen.getByLabelText("Daily vol target") as HTMLInputElement).value).toBe("1.5");
    expect(screen.getByTestId("vol-target-value").textContent).toBe("1.5%");
  });

  it("#4999: the entry stores the engine's fraction, the unit stands beside it", async () => {
    render(<TradingControlsCard />);
    const input = await waitFor(() => screen.getByLabelText("Daily vol target"));
    fireEvent.change(input, { target: { value: "2,5" } });
    expect(screen.getByTestId("vol-target-value").textContent).toBe("2.5%");
    expect((screen.getByLabelText("Daily vol target slider") as HTMLInputElement).value).toBe("0.025");
    fireEvent.click(applyVol());
    fireEvent.click(await screen.findByRole("button", { name: /change and restart/i }));
    await waitFor(() => expect(postSettings).toHaveBeenCalled());
    expect(postSettings.mock.calls[0][0]).toEqual({ VOL_TARGET_DAILY_VOL: "0.025" });
  });

  it("#4999: the slider fills the entry in percent", async () => {
    render(<TradingControlsCard />);
    await waitFor(() => screen.getByLabelText("Daily vol target"));
    fireEvent.change(screen.getByLabelText("Daily vol target slider"), { target: { value: "0.02" } });
    expect((screen.getByLabelText("Daily vol target") as HTMLInputElement).value).toBe("2");
  });

  it("#4999: the slider ends read in percent", async () => {
    render(<TradingControlsCard />);
    await waitFor(() => screen.getByLabelText("Daily vol target slider"));
    const ends = Array.from(document.querySelectorAll('[data-copy="vol-bound"]')).map((e) => e.textContent);
    expect(ends).toEqual(["0.7%", "7.5%"]);
  });

  it("loads existing setup.json values (sizing off → target field hidden)", async () => {
    setBridge(bridge({ VOL_TARGETING_SIZING_ENABLED: "false", VOL_TARGET_DAILY_VOL: "0.010" }));
    render(<TradingControlsCard />);
    await waitFor(() => {
      expect(sizingState("Off")).toBe("true");
    });
    expect(screen.queryByLabelText("Daily vol target")).toBeNull();
  });

  it("#5046: the jump targets of the sizing references exist, also with sizing off", async () => {
    setBridge(bridge({ VOL_TARGETING_SIZING_ENABLED: "false" }));
    render(<TradingControlsCard />);
    await waitFor(() => {
      expect(sizingState("Off")).toBe("true");
    });
    expect(document.getElementById("volatility-sizing")).not.toBeNull();
    expect(document.getElementById("max-positions")).not.toBeNull();
  });

  it("#5046: after a vol apply the section reads the engine again", async () => {
    render(<TradingControlsCard />);
    const input = await waitFor(() => screen.getByLabelText("Daily vol target"));
    await waitFor(() => expect(getSettings).toHaveBeenCalledTimes(2));
    fireEvent.change(input, { target: { value: "3" } });
    fireEvent.click(applyVol());
    fireEvent.click(await screen.findByRole("button", { name: /change and restart/i }));
    await waitFor(() => expect(getSettings).toHaveBeenCalledTimes(3));
  });

  it("#4447: changing a value writes NOTHING until it is applied", async () => {
    render(<TradingControlsCard />);
    const input = await waitFor(() => screen.getByLabelText("Daily vol target"));
    fireEvent.change(input, { target: { value: "3" } });
    fireEvent.change(screen.getByLabelText("Daily vol target slider"), {
      target: { value: "0.02" },
    });
    fireEvent.click(sizingSegment().getByRole("button", { name: "Off" }));
    expect(saveMock()).not.toHaveBeenCalled();
    expect(postSettings).not.toHaveBeenCalled();
    expect(restartMock).not.toHaveBeenCalled();
    expect(applyVol()).toBeTruthy();
  });

  it("#4447: apply shows before → after, records FIRST, then persists, then restarts", async () => {
    render(<TradingControlsCard />);
    const input = await waitFor(() => screen.getByLabelText("Daily vol target"));
    fireEvent.change(input, { target: { value: "3" } });
    fireEvent.click(applyVol());

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Daily vol target")).toBeTruthy();
    expect(within(dialog).getByText("1.5%")).toBeTruthy();
    expect(within(dialog).getByText("3%")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /change and restart/i }));
    await waitFor(() => expect(restartMock).toHaveBeenCalled());

    expect(postSettings).toHaveBeenCalledWith(
      { VOL_TARGET_DAILY_VOL: "0.03" },
      expect.any(String),
      expect.any(String),
    );
    expect(saveMock()).toHaveBeenCalledWith({ VOL_TARGET_DAILY_VOL: "0.03" });
    const recordedAt = postSettings.mock.invocationCallOrder[0];
    const persistedAt = saveMock().mock.invocationCallOrder[0];
    const restartedAt = restartMock.mock.invocationCallOrder[0];
    expect(recordedAt).toBeLessThan(persistedAt);
    expect(persistedAt).toBeLessThan(restartedAt);
  });

  it("#4447: switching Volatility sizing off runs the same confirmed flow", async () => {
    render(<TradingControlsCard />);
    await waitFor(() => screen.getByRole("group", { name: "Volatility sizing" }));
    fireEvent.click(sizingSegment().getByRole("button", { name: "Off" })); // ON -> OFF
    // the target field disappears while off
    expect(screen.queryByLabelText("Daily vol target")).toBeNull();
    fireEvent.click(applyVol());
    fireEvent.click(await screen.findByRole("button", { name: /change and restart/i }));
    await waitFor(() => expect(restartMock).toHaveBeenCalled());
    expect(postSettings).toHaveBeenCalledWith(
      { VOL_TARGETING_SIZING_ENABLED: "false" },
      expect.any(String),
      expect.any(String),
    );
    expect(saveMock()).toHaveBeenCalledWith({ VOL_TARGETING_SIZING_ENABLED: "false" });
  });

  it("#4447: cancelling the confirm changes nothing", async () => {
    render(<TradingControlsCard />);
    const input = await waitFor(() => screen.getByLabelText("Daily vol target"));
    fireEvent.change(input, { target: { value: "3" } });
    fireEvent.click(applyVol());
    fireEvent.click(await screen.findByRole("button", { name: /^cancel$/i }));
    expect(postSettings).not.toHaveBeenCalled();
    expect(saveMock()).not.toHaveBeenCalled();
    expect(restartMock).not.toHaveBeenCalled();
  });

  it("#4447: a decimal comma is accepted (4,5 % = 0.045)", async () => {
    render(<TradingControlsCard />);
    const input = await waitFor(() => screen.getByLabelText("Daily vol target"));
    fireEvent.change(input, { target: { value: "4,5" } });
    expect(screen.queryByRole("alert")).toBeNull();
    fireEvent.click(applyVol());
    fireEvent.click(await screen.findByRole("button", { name: /change and restart/i }));
    await waitFor(() => expect(postSettings).toHaveBeenCalled());
    expect(postSettings.mock.calls[0][0]).toEqual({ VOL_TARGET_DAILY_VOL: "0.045" });
  });

  it("#4447: an invalid value names the registry bounds and offers no apply", async () => {
    render(<TradingControlsCard />);
    const input = await waitFor(() => screen.getByLabelText("Daily vol target"));
    fireEvent.change(input, { target: { value: "9" } }); // out of range
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("0.7%");
    expect(alert.textContent).toContain("7.5%");
    expect(screen.queryByRole("button", { name: /apply volatility settings/i })).toBeNull();

    fireEvent.change(input, { target: { value: "abc" } });
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(saveMock()).not.toHaveBeenCalled();
  });

  it("#4447: the slider bounds come from the registry meta", async () => {
    getSettings.mockResolvedValue({
      ...META,
      meta: [
        { key: "VOL_TARGET_DAILY_VOL", kind: "float", default: "0.0150", min: 0.008, max: 0.06 },
      ],
    });
    render(<TradingControlsCard />);
    await waitFor(() =>
      expect((screen.getByLabelText("Daily vol target slider") as HTMLInputElement).max).toBe(
        "0.06",
      ),
    );
    expect((screen.getByLabelText("Daily vol target slider") as HTMLInputElement).min).toBe(
      "0.008",
    );
  });

  it("the slider shows the registry bounds 0.007–0.075", async () => {
    render(<TradingControlsCard />);
    const slider = (await waitFor(() =>
      screen.getByLabelText("Daily vol target slider"),
    )) as HTMLInputElement;
    expect(slider.getAttribute("type")).toBe("range");
    expect(slider.min).toBe("0.007");
    expect(slider.max).toBe("0.075");
  });

  it("Default settings sets the shipped defaults as PENDING — nothing is written", async () => {
    setBridge(bridge({ VOL_TARGETING_SIZING_ENABLED: "false", VOL_TARGET_DAILY_VOL: "0.010" }));
    render(<TradingControlsCard />);
    await waitFor(() => {
      expect(sizingState("Off")).toBe("true");
    });
    fireEvent.click(screen.getByRole("button", { name: /default settings/i }));
    expect(saveMock()).not.toHaveBeenCalled();
    // reset restored the ON state (target visible again) as a pending change
    await waitFor(() => screen.getByLabelText("Daily vol target"));
    expect((screen.getByLabelText("Daily vol target") as HTMLInputElement).value).toBe("1.5");
    expect(applyVol()).toBeTruthy();
  });

  it("#5007: without a stored count the card shows the engine's effective number", async () => {
    getSettings.mockResolvedValue({ ...META, effective_positions: 20 });
    render(<TradingControlsCard />);
    const pos = screen.getByLabelText("Maximum positions") as HTMLInputElement;
    await waitFor(() => expect(pos.value).toBe("20"));
    expect(screen.queryByRole("button", { name: /apply changes/i })).toBeNull();
  });

  it("#5007: Default settings still resets to the shipped 10, as a pending change", async () => {
    getSettings.mockResolvedValue({ ...META, effective_positions: 20 });
    render(<TradingControlsCard />);
    const pos = screen.getByLabelText("Maximum positions") as HTMLInputElement;
    await waitFor(() => expect(pos.value).toBe("20"));
    fireEvent.click(screen.getByRole("button", { name: /default settings/i }));
    expect(pos.value).toBe("10");
    expect(screen.getByRole("button", { name: /apply changes/i })).toBeTruthy();
    expect(saveMock()).not.toHaveBeenCalled();
  });

  it("#5007: a failed read says so and offers no structure change on a guessed number", async () => {
    getSettings.mockRejectedValue(new Error("engine down"));
    render(<TradingControlsCard />);
    await screen.findByTestId("positions-unavailable");
    expect((screen.getByLabelText("Maximum positions") as HTMLInputElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Minimum holding period"), { target: { value: "7" } });
    expect(screen.queryByRole("button", { name: /apply changes/i })).toBeNull();
  });

  it("neutrality: no profile names, no recommendation wording, no measured figures", async () => {
    render(<TradingControlsCard />);
    // #4999: "%" left this list; the vol target reads 1.5%, a setting and no measured figure.
    const html = document.body.textContent || "";
    for (const banned of [
      /profil/i, /empfohlen/i, /aggressiv/i, /kapitalerhalt/i, /sharpe/i, /drawdown/i,
    ]) {
      expect(banned.test(html)).toBe(false);
    }
  });
});
