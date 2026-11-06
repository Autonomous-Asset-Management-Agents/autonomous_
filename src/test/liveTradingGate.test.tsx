import { render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { useStore } from "@/console/store/useStore";

/**
 * #1915 — live trading is Senior-only. The Paper/Live switch must gate on the resolved entitlement
 * (`allow_live`): Junior (BASIC, allow_live=false) sees a locked "Senior" live area, NOT a working
 * live switch. #5178: the switch moved from the former LiveTradingSwitchCard into the account areas
 * of TradingKeysCard; the gate moved with it (switchStatus.ts::liveSwitchGate). The Upgrade path
 * stays in the sidebar (UpgradeCta).
 */
vi.mock("@/lib/desktopBridge", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/desktopBridge")>();
  return {
    ...actual,
    isDesktop: () => true,
    getKeychainStatus: vi.fn().mockResolvedValue({ ALPACA_LIVE_API_KEY: true, ALPACA_LIVE_SECRET_KEY: true }),
  };
});
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchHealth: vi.fn().mockResolvedValue({ paper_trading: true }),
    fetchEntitlementStatus: vi.fn().mockResolvedValue(null),
  };
});

import { TradingKeysCard } from "@/console/desktop/TradingKeysCard";

describe("Trading keys — live trading is a Senior gate (#1915, #5178)", () => {
  beforeEach(() => useStore.setState({ paperTrading: null, modeSwitch: null, switchReconciling: false, systemHalted: false }));

  it("Junior (allow_live=false): the live area is locked (Senior), NO live switch", async () => {
    useStore.setState({ allowLive: false });
    render(<TradingKeysCard />);
    expect(await screen.findByText(/unlocks with senior/i)).toBeTruthy();
    await waitFor(() => expect(useStore.getState().paperTrading).toBe(true));
    expect(screen.queryByRole("button", { name: "ENABLE LIVE TRADING" })).toBeNull();
  });

  it("Senior (allow_live=true): the live area carries the live switch, no lock", async () => {
    useStore.setState({ allowLive: true });
    render(<TradingKeysCard />);
    const pill = await screen.findByRole("button", { name: "ENABLE LIVE TRADING" });
    await waitFor(() => expect(pill).not.toBeDisabled());
    expect(screen.queryByText(/unlocks with senior/i)).toBeNull();
  });
});
