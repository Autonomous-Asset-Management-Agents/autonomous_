import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

/**
 * #1915 click-does-nothing fix: the Upgrade CTA must NEVER fail silently. A claim
 * that isn't a successful unlock (absent desktop bridge in the browser, a claim
 * error, or the free-beta cap) has to surface a visible note — previously the
 * onClick swallowed every status except "claimed"/"cap-reached", so the button
 * appeared to do nothing. Button styling (#1983) is unchanged.
 */
const claimBeta = vi.fn();
// Keep every real bridge export (isDesktop, getEnginePort, … are used by
// Sidebar's transitive imports); override only claimBeta.
vi.mock("@/lib/desktopBridge", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/desktopBridge")>();
  return { ...actual, claimBeta: (...a: unknown[]) => claimBeta(...a) };
});

import { UpgradeCta } from "@/console/desktop/Sidebar";

const clickUpgrade = () => fireEvent.click(screen.getByRole("button", { name: /upgrade/i }));

describe("UpgradeCta — never fails silently (#1915)", () => {
  beforeEach(() => claimBeta.mockReset());

  it("shows a note when the bridge is absent (browser / no desktop shell)", async () => {
    claimBeta.mockResolvedValue({ status: "error", error: "desktop-only" });
    render(<UpgradeCta refetch={vi.fn()} />);
    clickUpgrade();
    expect(await screen.findByText(/available in the desktop app/i)).toBeTruthy();
  });

  it("shows a note when the free-beta cap is reached", async () => {
    claimBeta.mockResolvedValue({ status: "cap-reached" });
    render(<UpgradeCta refetch={vi.fn()} />);
    clickUpgrade();
    expect(await screen.findByText(/beta full/i)).toBeTruthy();
  });

  it("shows a note on a claim error", async () => {
    claimBeta.mockResolvedValue({ status: "error", error: "bundled license has no token" });
    render(<UpgradeCta refetch={vi.fn()} />);
    clickUpgrade();
    expect(await screen.findByText(/upgrade failed/i)).toBeTruthy();
  });

  it("on success it refetches and shows no error note", async () => {
    claimBeta.mockResolvedValue({ status: "claimed", tier: "PRO" });
    const refetch = vi.fn().mockResolvedValue(undefined);
    render(<UpgradeCta refetch={refetch} />);
    clickUpgrade();
    await waitFor(() => expect(refetch).toHaveBeenCalled());
    expect(
      screen.queryByText(/available in the desktop app|beta full|upgrade failed/i),
    ).toBeNull();
  });
});
