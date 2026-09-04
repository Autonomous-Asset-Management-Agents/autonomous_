/**
 * UXC-1 S3 (#3156, Leitplanke 6) — defaults review in the live consent: deviations
 * from the shipped defaults are listed neutrally; the operator ACTIVELY chooses
 * "keep" or "reset"; both decisions land on the Art-14 chain BEFORE live enablement
 * (keep → deviation-ack only; reset → backend-composed ack + settings event, then
 * setup.json persists the defaults) — Enable stays locked until the choice.
 */
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { useStore } from "@/console/store/useStore";

const setupState: Record<string, unknown> = {};
const saveSetupMock = vi.fn().mockResolvedValue(undefined);

vi.mock("@/lib/desktopBridge", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/desktopBridge")>();
  return {
    ...actual,
    isDesktop: () => true,
    claimBeta: vi.fn(),
    validateAlpaca: vi.fn(),
    saveSecret: vi.fn(),
    getKeychainStatus: vi
      .fn()
      .mockResolvedValue({ ALPACA_LIVE_API_KEY: true, ALPACA_LIVE_SECRET_KEY: true }),
    restartEngine: vi.fn().mockResolvedValue(undefined),
    getSetupState: vi.fn().mockImplementation(async () => ({ ...setupState })),
    saveSetupState: (...a: unknown[]) => saveSetupMock(...a),
  };
});

const callOrder: string[] = [];
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchEntitlementStatus: vi.fn().mockResolvedValue(null),
    fetchHealth: vi.fn().mockResolvedValue({ status: "healthy", paper_trading: true }),
    liveEnable: vi.fn().mockImplementation(async () => {
      callOrder.push("liveEnable");
    }),
    liveDisable: vi.fn().mockResolvedValue(undefined),
    updateHitlPolicy: vi.fn().mockResolvedValue(undefined),
    postSwitchReconcile: vi.fn().mockResolvedValue(undefined),
    postSettingsDeviationAck: vi.fn().mockImplementation(async () => {
      callOrder.push("deviationAck");
      return { success: true };
    }),
  };
});

import { LiveTradingSwitchCard } from "@/console/desktop/LiveTradingSwitchCard";
import { LiveConsentModal } from "@/console/desktop/LiveConsentModal";
import { postSettingsDeviationAck, liveEnable } from "@/lib/api";

function Harness() {
  return (
    <>
      <LiveTradingSwitchCard />
      <LiveConsentModal />
    </>
  );
}

async function openConsent() {
  fireEvent.click(await screen.findByRole("button", { name: /^live$/i }));
  await screen.findByRole("button", { name: /enable live trading/i });
}

async function ackAndWaiver() {
  fireEvent.click(screen.getByLabelText("ack-live"));
  fireEvent.click(screen.getByLabelText("ack-advance-approval"));
}

describe("live consent defaults review (UXC-1 S3)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    callOrder.length = 0;
    for (const k of Object.keys(setupState)) delete setupState[k];
    useStore.setState({
      allowLive: true,
      paperTrading: true,
      engineServing: false,
      requestLiveConsent: false,
      requestSwitchToPaper: false,
      switchReconciling: false,
      switchConfirmFailed: false,
      switchError: null,
      modeSwitch: null,
    });
  });

  it("D1: a deviation is listed neutrally with current and default value", async () => {
    setupState.VOL_TARGET_DAILY_VOL = "0.02";
    render(<Harness />);
    await openConsent();
    const block = await screen.findByTestId("defaults-review");
    expect(block.textContent).toContain("Daily vol target");
    expect(block.textContent).toContain("0.02");
    expect(block.textContent).toContain("0.015");
  });

  it("D2: without deviations there is no review block and Enable is not extra-locked", async () => {
    render(<Harness />);
    await openConsent();
    expect(screen.queryByTestId("defaults-review")).toBeNull();
  });

  it("D3: Enable stays locked until keep/reset is chosen; no preselection", async () => {
    setupState.VOL_TARGET_DAILY_VOL = "0.02";
    render(<Harness />);
    await openConsent();
    await ackAndWaiver();
    const enable = screen.getByRole("button", {
      name: /enable live trading/i,
    }) as HTMLButtonElement;
    expect(enable.disabled).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: /keep my settings/i }));
    expect(
      (screen.getByRole("button", { name: /enable live trading/i }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);
  });

  it("D4: keep — the deviation-ack is recorded BEFORE liveEnable, nothing persisted", async () => {
    setupState.VOL_TARGET_DAILY_VOL = "0.02";
    render(<Harness />);
    await openConsent();
    await ackAndWaiver();
    fireEvent.click(screen.getByRole("button", { name: /keep my settings/i }));
    fireEvent.click(screen.getByRole("button", { name: /enable live trading/i }));

    await waitFor(() => expect(liveEnable).toHaveBeenCalled());
    expect(postSettingsDeviationAck).toHaveBeenCalledWith(
      expect.objectContaining({
        decision: "keep",
        deviations: [
          expect.objectContaining({ key: "VOL_TARGET_DAILY_VOL", current: "0.02" }),
        ],
      }),
    );
    expect(callOrder.indexOf("deviationAck")).toBeLessThan(callOrder.indexOf("liveEnable"));
    expect(saveSetupMock).not.toHaveBeenCalled();
  });

  it("D5: reset — ack with decision=reset, then defaults persisted, all BEFORE liveEnable", async () => {
    setupState.VOL_TARGET_DAILY_VOL = "0.02";
    setupState.FULL_UNIVERSE_MAX_POSITIONS = "15";
    saveSetupMock.mockImplementation(async () => {
      callOrder.push("saveSetup");
    });
    render(<Harness />);
    await openConsent();
    await ackAndWaiver();
    fireEvent.click(screen.getByRole("button", { name: /reset to defaults/i }));
    fireEvent.click(screen.getByRole("button", { name: /enable live trading/i }));

    await waitFor(() => expect(liveEnable).toHaveBeenCalled());
    expect(postSettingsDeviationAck).toHaveBeenCalledWith(
      expect.objectContaining({ decision: "reset" }),
    );
    expect(saveSetupMock).toHaveBeenCalledWith(
      expect.objectContaining({
        VOL_TARGET_DAILY_VOL: "0.015",
        FULL_UNIVERSE_MAX_POSITIONS: "10",
      }),
    );
    expect(callOrder.indexOf("deviationAck")).toBeLessThan(callOrder.indexOf("saveSetup"));
    expect(callOrder.indexOf("saveSetup")).toBeLessThan(callOrder.indexOf("liveEnable"));
  });

  it("D6: neutrality — the review block carries no recommendation or profile wording", async () => {
    setupState.VOL_TARGET_DAILY_VOL = "0.02";
    render(<Harness />);
    await openConsent();
    const block = await screen.findByTestId("defaults-review");
    for (const re of [/profil/i, /empfohlen/i, /recommend/i, /aggressiv/i, /safer/i, /risky/i]) {
      expect(block.textContent ?? "").not.toMatch(re);
    }
  });
});
