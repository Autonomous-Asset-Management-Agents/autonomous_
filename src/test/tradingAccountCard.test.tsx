import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { useStore } from "@/console/store/useStore";

/**
 * Trading-account consolidation (#60) — the paper + live key slots (formerly the standalone
 * "Broker API keys" / "Live Alpaca keys" cards) now live INSIDE the Trading Account card,
 * renamed "Paper Trading Keys" / "Live Trading Keys". Junior (allow_live=false) can manage
 * paper keys but the live-keys slot is locked (Senior); Senior can manage both and gets an
 * easy Paper|Live toggle whose → Live path is a WORM-audited real-money confirm.
 */
vi.mock("@/lib/desktopBridge", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/desktopBridge")>();
  return {
    ...actual,
    isDesktop: () => true,
    claimBeta: vi.fn(),
    validateAlpaca: vi.fn(),
    saveSecret: vi.fn(),
    getKeychainStatus: vi.fn().mockResolvedValue({ ALPACA_LIVE_API_KEY: true, ALPACA_LIVE_SECRET_KEY: true }),
    startEngine: vi.fn(),
    stopEngine: vi.fn(),
  };
});
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchHealth: vi.fn().mockResolvedValue({ paper_trading: true }),
    fetchEntitlementStatus: vi.fn().mockResolvedValue(null),
    liveEnable: vi.fn().mockResolvedValue(undefined),
    liveDisable: vi.fn().mockResolvedValue(undefined),
  };
});

import { pressEnableLive, MODAL_ENABLE } from "./mainEngineSwitch";
import { TradingKeysCard } from "@/console/desktop/TradingKeysCard";
import { LiveConsentModal } from "@/console/desktop/LiveConsentModal";
import { liveEnable } from "@/lib/api";
// #2465: switch logic moved to the global LiveConsentModal — render both (#5178: Trading keys area → signal → modal).
function Harness() {
  return (
    <>
      <TradingKeysCard />
      <LiveConsentModal />
    </>
  );
}

describe("Trading Account card — consolidated key slots (#60)", () => {
  beforeEach(() => useStore.setState({ allowLive: null, requestLiveConsent: false, requestSwitchToPaper: false, switchReconciling: false, switchConfirmFailed: false, switchError: null, modeSwitch: null, paperTrading: null }));

  // #2863: the key slots moved to the System-tab TradingKeysCard — assert them there.
  it("Junior (allow_live=false): paper key fields visible, live keys locked (Senior)", () => {
    useStore.setState({ allowLive: false });
    render(<TradingKeysCard />);
    // #5176: the areas are named after the account (Paper account 1 … Live).
    expect(screen.getByRole("group", { name: "Paper account 1" })).toBeTruthy();
    expect(screen.getByRole("group", { name: "Live" })).toBeTruthy();
    // paper slot's fields are shown directly; the live slot is locked (no form) — no accordion
    expect(screen.getByLabelText("alpaca-paper-key-id")).toBeTruthy();
    expect(screen.queryByLabelText("alpaca-live-key-id")).toBeNull();
    expect(screen.getByText(/unlocks with senior/i)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Manage" })).toBeNull();
  });

  it("Senior (allow_live=true): both paper and live key fields visible", () => {
    useStore.setState({ allowLive: true });
    render(<TradingKeysCard />);
    // #5176: the areas are named after the account (Paper account 1 … Live).
    expect(screen.getByRole("group", { name: "Paper account 1" })).toBeTruthy();
    expect(screen.getByRole("group", { name: "Live" })).toBeTruthy();
    // both slots' fields are shown directly (no Manage/Close accordion)
    expect(screen.getByLabelText("alpaca-paper-key-id")).toBeTruthy();
    expect(screen.getByLabelText("alpaca-live-key-id")).toBeTruthy();
    expect(screen.queryByText(/unlocks with senior/i)).toBeNull();
    expect(screen.queryByRole("button", { name: "Manage" })).toBeNull();
  });

  it("Senior: the Live toggle opens a real-money confirm, then arms live (WORM)", async () => {
    useStore.setState({ allowLive: true });
    render(<Harness />);
    await pressEnableLive();
    expect(screen.getByText(/worm compliance audit trail/i)).toBeTruthy();
    fireEvent.click(screen.getByLabelText("ack-live"));
    fireEvent.click(screen.getByLabelText("ack-advance-approval"));
    fireEvent.click(screen.getByRole("button", { name: MODAL_ENABLE }));
    await waitFor(() => expect(liveEnable).toHaveBeenCalled());
  });

  it("the main-engine badge is a StatusDot (green dot + white 'Main engine'), not a .pill chip (#5178)", async () => {
    useStore.setState({ allowLive: false });
    render(<Harness />);
    const badge = await screen.findByText("Main engine");
    expect(badge.closest(".pill")).toBeNull();
    // StatusDot standard: a green dot precedes the white label.
    expect(badge.previousElementSibling?.className ?? "").toContain("bg-[#00c27a]");
  });
});
