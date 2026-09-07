import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

/**
 * UpgradeModal — paid paywall branch (GTM-2 #1809 T3). When PAYWALL_ENABLED is on
 * (store.paywallEnabled === true) a Junior desktop is offered the PAID Senior path
 * (checkout + offline key activation), NOT the free beta claim. The free "CLAIM SENIOR"
 * button and its claimBeta call must be gone; a license-key panel must be reachable.
 */
const claimBeta = vi.fn();
vi.mock("@/lib/desktopBridge", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/desktopBridge")>();
  return { ...actual, claimBeta: (...a: unknown[]) => claimBeta(...a) };
});

import { useStore } from "@/console/store/useStore";
import { UpgradeModal } from "@/console/desktop/UpgradeModal";

describe("UpgradeModal — paywall ON (paid Senior)", () => {
  beforeEach(() => {
    claimBeta.mockReset();
    useStore.setState({ tier: null, paywallEnabled: true });
  });

  it("Junior sees the paid path — no free CLAIM SENIOR, no claimBeta", () => {
    render(<UpgradeModal onClose={vi.fn()} refetch={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /claim senior/i })).toBeNull();
    expect(screen.getByRole("button", { name: /get senior/i })).toBeTruthy();
    expect(claimBeta).not.toHaveBeenCalled();
  });

  it('"I have a license key" reveals the offline activation panel', () => {
    render(<UpgradeModal onClose={vi.fn()} refetch={vi.fn()} />);
    expect(screen.queryByPlaceholderText(/license key/i)).toBeNull();
    fireEvent.click(screen.getByText(/i (already )?have a license key/i));
    expect(screen.getByPlaceholderText(/license key/i)).toBeTruthy();
  });

  it("Get Senior opens the checkout URL when provisioned", () => {
    useStore.setState({ paywallEnabled: true, checkoutUrl: "https://x.lemonsqueezy.com/buy/abc" });
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    render(<UpgradeModal onClose={vi.fn()} refetch={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /get senior/i }));
    expect(open).toHaveBeenCalledWith("https://x.lemonsqueezy.com/buy/abc", "_blank", expect.any(String));
    open.mockRestore();
  });

  it("shows the Finance-configured price on the Senior card", () => {
    useStore.setState({ paywallEnabled: true, priceDisplay: "9,99 €" });
    render(<UpgradeModal onClose={vi.fn()} refetch={vi.fn()} />);
    expect(screen.getByText(/9,99\s*€/)).toBeTruthy();
  });

  it("Get Senior falls back to the key panel when checkout is not provisioned", () => {
    useStore.setState({ paywallEnabled: true, checkoutUrl: null });
    render(<UpgradeModal onClose={vi.fn()} refetch={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /get senior/i }));
    expect(screen.getByPlaceholderText(/license key/i)).toBeTruthy();
  });
});

describe("UpgradeModal — paywall OFF keeps the free beta claim", () => {
  beforeEach(() => {
    claimBeta.mockReset();
    useStore.setState({ tier: null, paywallEnabled: false });
  });

  it("Junior still sees the free CLAIM SENIOR button", () => {
    render(<UpgradeModal onClose={vi.fn()} refetch={vi.fn()} />);
    expect(screen.getByRole("button", { name: /claim senior/i })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /get senior/i })).toBeNull();
  });
});
