import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { useStore } from "@/console/store/useStore";

/**
 * #1915 — live trading is Senior-only. The LiveTradingSwitchCard must gate on the
 * resolved entitlement (`allow_live`): Junior (BASIC, allow_live=false) sees a locked
 * "Senior feature" state with an Upgrade path, NOT a working live switch. Before this
 * fix the card only checked isDesktop, so Junior was offered live trading (and on a
 * non-LOCAL/dev engine it even worked — exposing Senior's only differentiator).
 */
const claimBeta = vi.fn();
vi.mock("@/lib/desktopBridge", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/desktopBridge")>();
  return { ...actual, isDesktop: () => true, claimBeta: (...a: unknown[]) => claimBeta(...a) };
});
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    fetchHealth: vi.fn().mockResolvedValue({ paper_trading: true }),
    fetchEntitlementStatus: vi.fn().mockResolvedValue(null),
  };
});

import { LiveTradingSwitchCard } from "@/console/desktop/LiveTradingSwitchCard";

describe("LiveTradingSwitchCard — live trading is a Senior gate (#1915)", () => {
  beforeEach(() => claimBeta.mockReset());

  it("Junior (allow_live=false): shows the Senior lock + Upgrade, NOT a Live toggle", async () => {
    useStore.setState({ allowLive: false });
    render(<LiveTradingSwitchCard />);
    expect(await screen.findByText(/senior feature/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: /upgrade to senior/i })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^live$/i })).toBeNull();
  });

  it("Senior (allow_live=true): shows the Paper|Live toggle, no lock", async () => {
    useStore.setState({ allowLive: true });
    render(<LiveTradingSwitchCard />);
    expect(await screen.findByRole("button", { name: /^live$/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /^paper$/i })).toBeTruthy();
    expect(screen.queryByText(/senior feature/i)).toBeNull();
  });
});
