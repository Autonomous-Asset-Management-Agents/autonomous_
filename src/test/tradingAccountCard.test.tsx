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

import { LiveTradingSwitchCard } from "@/console/desktop/LiveTradingSwitchCard";
import { liveEnable } from "@/lib/api";

describe("Trading Account card — consolidated key slots (#60)", () => {
  beforeEach(() => useStore.setState({ allowLive: null }));

  it("Junior (allow_live=false): paper keys manageable, live keys locked (Senior)", () => {
    useStore.setState({ allowLive: false });
    render(<LiveTradingSwitchCard />);
    expect(screen.getByText(/paper trading keys/i)).toBeTruthy();
    expect(screen.getByText(/live trading keys/i)).toBeTruthy();
    // paper slot is manageable, live slot is locked (no second Manage)
    expect(screen.getAllByRole("button", { name: "Manage" })).toHaveLength(1);
    expect(screen.getByText(/unlocks with senior/i)).toBeTruthy();
  });

  it("Senior (allow_live=true): both paper and live keys manageable", () => {
    useStore.setState({ allowLive: true });
    render(<LiveTradingSwitchCard />);
    expect(screen.getByText(/paper trading keys/i)).toBeTruthy();
    expect(screen.getByText(/live trading keys/i)).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "Manage" })).toHaveLength(2);
    expect(screen.queryByText(/unlocks with senior/i)).toBeNull();
  });

  it("Senior: the Live toggle opens a real-money confirm, then arms live (WORM)", async () => {
    useStore.setState({ allowLive: true });
    render(<LiveTradingSwitchCard />);
    fireEvent.click(await screen.findByRole("button", { name: /^live$/i }));
    expect(screen.getByText(/audit notice/i)).toBeTruthy();
    fireEvent.click(screen.getByLabelText("ack-live"));
    fireEvent.click(screen.getByLabelText("ack-advance-approval"));
    fireEvent.click(screen.getByRole("button", { name: /enable live trading/i }));
    await waitFor(() => expect(liveEnable).toHaveBeenCalled());
  });

  it("the mode badge is a StatusDot (green dot + white 'Paper'), not a .pill chip", async () => {
    useStore.setState({ allowLive: false });
    render(<LiveTradingSwitchCard />);
    const badge = await screen.findByText("Paper");
    expect(badge.closest(".pill")).toBeNull();
    // StatusDot standard: a green dot precedes the white label.
    expect(badge.previousElementSibling?.className ?? "").toContain("bg-[#00c27a]");
  });
});
