/**
 * #3364 — the keep/reset settings PROMPT was removed from the live consent modal
 * (owner decision: it sat below the fold and added go-live friction). Custom settings
 * simply stay in effect (keep). A SILENT "keep" snapshot of the non-default settings is
 * still written to the Art-14 chain on go-live, BEST-EFFORT — a failed snapshot must NOT
 * block the go-live (the authoritative record is liveEnable). No review UI, no reset path,
 * and Enable is not gated by any settings choice.
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
let ackShouldFail = false;
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
      if (ackShouldFail) throw new Error("ack failed");
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

const enableBtn = () =>
  screen.getByRole("button", { name: /enable live trading/i }) as HTMLButtonElement;

describe("live consent — settings prompt removed (#3364)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    callOrder.length = 0;
    ackShouldFail = false;
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

  it("R1: no settings-review block or keep/reset choice renders — even with deviations", async () => {
    setupState.VOL_TARGET_DAILY_VOL = "0.02";
    render(<Harness />);
    await openConsent();
    expect(screen.queryByTestId("defaults-review")).toBeNull();
    expect(screen.queryByRole("button", { name: /keep my settings/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /reset to defaults/i })).toBeNull();
  });

  it("R2: Enable is gated only by ack + waiver, not by any settings choice", async () => {
    setupState.VOL_TARGET_DAILY_VOL = "0.02";
    render(<Harness />);
    await openConsent();
    expect(enableBtn().disabled).toBe(true); // acks not ticked yet
    await ackAndWaiver();
    expect(enableBtn().disabled).toBe(false); // no settings choice required
  });

  it("R3: go-live writes a SILENT decision=keep snapshot BEFORE liveEnable; nothing persisted", async () => {
    setupState.VOL_TARGET_DAILY_VOL = "0.02";
    render(<Harness />);
    await openConsent();
    await ackAndWaiver();
    fireEvent.click(enableBtn());

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
    expect(saveSetupMock).not.toHaveBeenCalled(); // no reset path
  });

  it("R4: a failed snapshot does NOT block go-live (fail-open)", async () => {
    setupState.VOL_TARGET_DAILY_VOL = "0.02";
    ackShouldFail = true;
    render(<Harness />);
    await openConsent();
    await ackAndWaiver();
    fireEvent.click(enableBtn());
    await waitFor(() => expect(liveEnable).toHaveBeenCalled()); // proceeds despite the throw
  });

  it("R5: no deviations → no snapshot call, go-live proceeds", async () => {
    render(<Harness />);
    await openConsent();
    await ackAndWaiver();
    fireEvent.click(enableBtn());
    await waitFor(() => expect(liveEnable).toHaveBeenCalled());
    expect(postSettingsDeviationAck).not.toHaveBeenCalled();
  });
});
