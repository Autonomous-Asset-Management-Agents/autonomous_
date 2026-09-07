import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";

/**
 * UpgradeCta (sidebar) — the top-left Junior (BASIC) CTA now OPENS the tier
 * {@link UpgradeModal} instead of claiming silently. The Senior claim (and the
 * #1915 "never fail silently" feedback) live INSIDE the modal now — see
 * upgradeModal.test.tsx.
 */
vi.mock("@/lib/desktopBridge", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/desktopBridge")>();
  return { ...actual, claimBeta: vi.fn() };
});

import { UpgradeCta } from "@/console/desktop/Sidebar";

describe("UpgradeCta (sidebar)", () => {
  it("clicking UPGRADE opens the tier modal (Senior / Developer / Institutional)", () => {
    render(<UpgradeCta refetch={vi.fn()} />);
    // The modal is not mounted until the CTA is clicked.
    expect(screen.queryByText(/upgrade your plan/i)).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /^upgrade$/i }));

    expect(screen.getByText(/upgrade your plan/i)).toBeTruthy();
    expect(screen.getByText("Senior")).toBeTruthy();
    expect(screen.getByText("Developer")).toBeTruthy();
    expect(screen.getByText("Institutional")).toBeTruthy();
  });
});
