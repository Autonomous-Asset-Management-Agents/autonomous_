/**
 * #2277 OTEL Switch-Completeness (I-3) — the console half.
 *
 * INC-1: one per-switch correlation id (the nonce) must thread through BOTH the WORM enable/disable
 * AND the engine restart, so UI→API→boot→revoke share a single switch_id (`restartEngine(switchId)`).
 * INC-3: after a switch, `reconcilePaper` posts the reconcile outcome to the engine
 * (`postSwitchReconcile`) so a failed reconcile is reconstructable from the diagnose-export — and it
 * is FAIL-OPEN (the label still settles even if the telemetry post rejects).
 */
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { useStore } from "@/console/store/useStore";

vi.mock("@/lib/desktopBridge", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/desktopBridge")>();
  return {
    ...actual,
    isDesktop: () => true,
    claimBeta: vi.fn(),
    validateAlpaca: vi.fn(),
    saveSecret: vi.fn(),
    getKeychainStatus: vi.fn().mockResolvedValue({ ALPACA_LIVE_API_KEY: true, ALPACA_LIVE_SECRET_KEY: true }),
    restartEngine: vi.fn().mockResolvedValue(undefined),
  };
});
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchEntitlementStatus: vi.fn().mockResolvedValue(null),
    fetchHealth: vi.fn(),
    liveEnable: vi.fn().mockResolvedValue(undefined),
    liveDisable: vi.fn().mockResolvedValue(undefined),
    postSwitchReconcile: vi.fn().mockResolvedValue(true),
  };
});

import { LiveTradingSwitchCard } from "@/console/desktop/LiveTradingSwitchCard";
import { LiveConsentModal } from "@/console/desktop/LiveConsentModal";
// #2465: switch logic moved to the global LiveConsentModal — render both (tile → signal → modal).
function Harness() {
  return (
    <>
      <LiveTradingSwitchCard />
      <LiveConsentModal />
    </>
  );
}
import { fetchHealth, liveEnable, liveDisable, postSwitchReconcile } from "@/lib/api";
import { restartEngine } from "@/lib/desktopBridge";

async function armLive() {
  fireEvent.click(await screen.findByRole("button", { name: /^live$/i }));
  fireEvent.click(screen.getByLabelText("ack-live"));
  fireEvent.click(screen.getByLabelText("ack-advance-approval"));
  fireEvent.click(screen.getByRole("button", { name: /enable live trading/i }));
}

describe("LiveTradingSwitchCard — switch_id correlation + reconcile telemetry (#2277)", () => {
  beforeEach(() => {
    vi.mocked(fetchHealth).mockReset();
    vi.mocked(liveEnable).mockReset().mockResolvedValue(undefined);
    vi.mocked(liveDisable).mockReset().mockResolvedValue(undefined);
    vi.mocked(restartEngine).mockReset().mockResolvedValue(undefined);
    vi.mocked(postSwitchReconcile).mockReset().mockResolvedValue(true);
    useStore.setState({ allowLive: true, paperTrading: null, engineServing: false, requestLiveConsent: false, requestSwitchToPaper: false, switchReconciling: false, switchConfirmFailed: false, switchError: null, modeSwitch: null });
  });

  it("INC-1: arming live threads ONE switch_id through liveEnable AND restartEngine", async () => {
    useStore.setState({ allowLive: true, paperTrading: true });
    vi.mocked(fetchHealth)
      .mockResolvedValueOnce({ paper_trading: true })
      .mockResolvedValue({ status: "healthy", paper_trading: false });

    render(<Harness />);
    await armLive();

    await waitFor(() => expect(liveEnable).toHaveBeenCalled());
    await waitFor(() => expect(restartEngine).toHaveBeenCalled());
    const enableNonce = vi.mocked(liveEnable).mock.calls[0][1];
    const restartArg = vi.mocked(restartEngine).mock.calls[0][0];
    expect(typeof enableNonce).toBe("string");
    expect(enableNonce.length).toBeGreaterThan(0);
    expect(restartArg).toBe(enableNonce); // the SAME id joins the WORM enable to the boot
  });

  it("INC-1: switching to paper threads the disable nonce into restartEngine", async () => {
    useStore.setState({ allowLive: true, paperTrading: false });
    vi.mocked(fetchHealth)
      .mockResolvedValueOnce({ paper_trading: false })
      .mockResolvedValue({ status: "healthy", paper_trading: true });

    render(<Harness />);
    fireEvent.click(await screen.findByRole("button", { name: /^paper$/i }));

    await waitFor(() => expect(liveDisable).toHaveBeenCalled());
    await waitFor(() => expect(restartEngine).toHaveBeenCalled());
    expect(vi.mocked(restartEngine).mock.calls[0][0]).toBe(
      vi.mocked(liveDisable).mock.calls[0][1],
    );
  });

  it("INC-3: reconcilePaper posts the reconcile outcome (health_reconcile) with the switch_id", async () => {
    useStore.setState({ allowLive: true, paperTrading: true });
    vi.mocked(fetchHealth)
      .mockResolvedValueOnce({ paper_trading: true })
      .mockResolvedValue({ status: "healthy", paper_trading: false });

    render(<Harness />);
    await armLive();

    await waitFor(() => expect(postSwitchReconcile).toHaveBeenCalled());
    const payload = vi.mocked(postSwitchReconcile).mock.calls[0][0];
    const enableNonce = vi.mocked(liveEnable).mock.calls[0][1];
    expect(payload.switch_id).toBe(enableNonce);
    expect(payload.attempts).toBeGreaterThanOrEqual(1);
    expect(payload.settled_status).toBe("healthy");
    expect(payload.paper_trading).toBe(false);
  });

  it("INC-3: a rejecting telemetry post is FAIL-OPEN — the label still settles to Live", async () => {
    useStore.setState({ allowLive: true, paperTrading: true });
    vi.mocked(postSwitchReconcile).mockRejectedValue(new Error("engine down"));
    vi.mocked(fetchHealth)
      .mockResolvedValueOnce({ paper_trading: true })
      .mockResolvedValue({ status: "healthy", paper_trading: false });

    render(<Harness />);
    await armLive();

    await waitFor(() =>
      expect(screen.getByText(/orders execute with real money/i)).toBeTruthy(),
    );
  });
});
