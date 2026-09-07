import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { TradingControlsCard } from "../console/desktop/TradingControlsCard";

/**
 * #3132 — Portfolio-shape controls (Maximum positions, Minimum holding period).
 *
 * They differ from the two sizing controls on purpose: those persist on every change,
 * these do NOT. They decide how capital is spread and how long it stays committed, so
 * applying them goes through the WORM-audited confirm and restarts the engine — the same
 * handling as the live switch.
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

const shapeApi = vi.fn();
vi.mock("@/lib/api", () => ({
  savePortfolioShape: (...a: unknown[]) => shapeApi(...a),
  // S3: the shared SettingsApplyModal reconciles in live mode via these two.
  fetchHealth: vi.fn().mockResolvedValue({ status: "healthy", paper_trading: false }),
  postSwitchReconcile: vi.fn().mockResolvedValue(undefined),
}));

const slider = (name: string) => screen.getByLabelText(name) as HTMLInputElement;

describe("#3132 portfolio-shape controls", () => {
  beforeEach(() => {
    setBridge(bridge());
    shapeApi.mockResolvedValue({
      success: true,
      applied: { positions: 20, hold_days: 10, vol_target: 0.015 },
      changes: [{ key: "FULL_UNIVERSE_MAX_POSITIONS", from: "10", to: "20" }],
      detail: "recorded",
    });
  });
  afterEach(() => {
    setBridge(undefined);
    vi.clearAllMocks();
  });

  it("shows both controls at today's defaults", async () => {
    render(<TradingControlsCard />);
    expect(slider("Maximum positions").value).toBe("10");
    expect(slider("Minimum holding period").value).toBe("20");
  });

  it("offers only the mechanically valid range", async () => {
    render(<TradingControlsCard />);
    const pos = slider("Maximum positions slider");
    // 20 is the ceiling because 1/N must stay >= MIN_POSITION_PERCENT (0.05):
    // at N=25 a slot is 4 % and the book could not fill.
    expect(pos.min).toBe("10");
    expect(pos.max).toBe("20");
    const hold = slider("Minimum holding period slider");
    expect(hold.min).toBe("5");
    expect(hold.max).toBe("20");
  });

  it("loads persisted values from setup.json", async () => {
    setBridge(
      bridge({ FULL_UNIVERSE_MAX_POSITIONS: "18", SMART_EXIT_MIN_HOLD_DAYS: "7.0" }),
    );
    render(<TradingControlsCard />);
    await waitFor(() => expect(slider("Maximum positions").value).toBe("18"));
    expect(slider("Minimum holding period").value).toBe("7");
  });

  it("moving a slider persists NOTHING on its own", async () => {
    render(<TradingControlsCard />);
    fireEvent.change(slider("Maximum positions slider"), { target: { value: "20" } });
    expect(saveMock()).not.toHaveBeenCalled();
    expect(shapeApi).not.toHaveBeenCalled();
    expect(restartMock).not.toHaveBeenCalled();
  });

  it("surfaces that the change is pending, with no confirm offered while unchanged", async () => {
    render(<TradingControlsCard />);
    expect(screen.queryByRole("button", { name: /apply changes/i })).toBeNull();
    fireEvent.change(slider("Maximum positions slider"), { target: { value: "20" } });
    expect(screen.getByText(/not applied yet/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: /apply changes/i })).toBeTruthy();
  });

  it("confirming records FIRST, then persists, then restarts", async () => {
    render(<TradingControlsCard />);
    fireEvent.change(slider("Maximum positions slider"), { target: { value: "20" } });
    fireEvent.click(screen.getByRole("button", { name: /apply changes/i }));
    fireEvent.click(screen.getByRole("button", { name: /change and restart/i }));

    await waitFor(() => expect(restartMock).toHaveBeenCalled());
    expect(shapeApi).toHaveBeenCalled();
    // Order matters: a change may never be persisted before it is on the chain.
    const recordedAt = shapeApi.mock.invocationCallOrder[0];
    const persistedAt = saveMock().mock.invocationCallOrder[0];
    const restartedAt = restartMock.mock.invocationCallOrder[0];
    expect(recordedAt).toBeLessThan(persistedAt);
    expect(persistedAt).toBeLessThan(restartedAt);
  });

  it("persists what the ENGINE applied, not what the slider showed", async () => {
    // The server clamps; the console must store the corrected value, never its own guess.
    shapeApi.mockResolvedValue({
      success: true,
      applied: { positions: 20, hold_days: 5, vol_target: 0.015 },
      changes: [{ key: "FULL_UNIVERSE_MAX_POSITIONS", from: "10", to: "20" }],
      detail: "recorded",
    });
    render(<TradingControlsCard />);
    fireEvent.change(slider("Minimum holding period slider"), { target: { value: "9" } });
    fireEvent.click(screen.getByRole("button", { name: /apply changes/i }));
    fireEvent.click(screen.getByRole("button", { name: /change and restart/i }));

    await waitFor(() => expect(saveMock()).toHaveBeenCalled());
    const payload = saveMock().mock.calls.at(-1)?.[0] as Record<string, string>;
    expect(payload.SMART_EXIT_MIN_HOLD_DAYS).toBe("5.0");
    expect(payload.FULL_UNIVERSE_MAX_POSITIONS).toBe("20");
  });

  it("a failed audit write persists nothing and does not restart", async () => {
    shapeApi.mockRejectedValue(new Error("chain not writable"));
    render(<TradingControlsCard />);
    fireEvent.change(slider("Maximum positions slider"), { target: { value: "20" } });
    fireEvent.click(screen.getByRole("button", { name: /apply changes/i }));
    fireEvent.click(screen.getByRole("button", { name: /change and restart/i }));

    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(saveMock()).not.toHaveBeenCalled();
    expect(restartMock).not.toHaveBeenCalled();
  });

  it("cancelling changes nothing", async () => {
    render(<TradingControlsCard />);
    fireEvent.change(slider("Maximum positions slider"), { target: { value: "20" } });
    fireEvent.click(screen.getByRole("button", { name: /apply changes/i }));
    fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }));
    expect(shapeApi).not.toHaveBeenCalled();
    expect(saveMock()).not.toHaveBeenCalled();
    expect(restartMock).not.toHaveBeenCalled();
  });

  it("neutrality: describes the mechanism, never recommends", async () => {
    render(<TradingControlsCard />);
    const text = document.body.textContent ?? "";
    for (const word of [
      "recommended",
      "conservative",
      "aggressive",
      "balanced",
      "optimal",
      "best",
      "should",
      "profile",
    ]) {
      expect(text.toLowerCase()).not.toContain(word);
    }
  });
});


// UXC-1 S3 (#3156, A4-Fix): a portfolio-shape apply in LIVE mode must carry the
// goLive directive — before S3 the restart booted silently back to PAPER.
import { useStore as _useStoreS3 } from "@/console/store/useStore";

test("S3/A4: shape apply in live mode requires re-consent and restarts with goLive", async () => {
  const { act: actS3 } = await import("@testing-library/react");
  actS3(() => _useStoreS3.setState({ paperTrading: false }));
  setBridge(bridge());
  shapeApi.mockResolvedValue({
    success: true,
    applied: { positions: 12, hold_days: 20, vol_target: 0.015 },
    changes: [{ key: "FULL_UNIVERSE_MAX_POSITIONS", from: "10", to: "12" }],
    detail: "recorded",
  });
  try {
    render(<TradingControlsCard />);
    const slider = await screen.findByLabelText("Maximum positions slider");
    fireEvent.change(slider, { target: { value: "12" } });
    fireEvent.click(screen.getByRole("button", { name: /apply changes/i }));

    // live re-consent required before the confirm unlocks
    const confirm = (await screen.findByRole("button", {
      name: /change and restart/i,
    })) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    fireEvent.click(screen.getByRole("checkbox", { name: /live/i }));
    fireEvent.click(screen.getByRole("button", { name: /change and restart/i }));

    await waitFor(() => expect(restartMock).toHaveBeenCalled());
    const [, opts] = restartMock.mock.calls.at(-1)!;
    expect(opts).toEqual({ goLive: true });
  } finally {
    actS3(() => _useStoreS3.setState({ paperTrading: true }));
  }
});
