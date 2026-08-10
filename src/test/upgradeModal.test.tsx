import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";

/**
 * UpgradeModal (GTM-1) — the tier ladder shown from the sidebar UPGRADE CTA:
 * Junior (current) → Senior (free-beta live unlock, claimable in-app) / Developer
 * (OSS/GitHub) / Institutional (contact sales). Only Senior is claimable in-app;
 * the claim must NEVER fail silently (#1915) — a non-"claimed" result surfaces a
 * visible note inside the modal.
 */
const claimBeta = vi.fn();
vi.mock("@/lib/desktopBridge", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/desktopBridge")>();
  return { ...actual, claimBeta: (...a: unknown[]) => claimBeta(...a) };
});

import { useStore } from "@/console/store/useStore";
import { UpgradeModal } from "@/console/desktop/UpgradeModal";

const claimSenior = () => fireEvent.click(screen.getByRole("button", { name: /claim senior/i }));

describe("UpgradeModal — tier ladder + Senior claim (#1915)", () => {
  beforeEach(() => {
    claimBeta.mockReset();
    useStore.setState({ tier: null }); // default = Junior view unless a test sets PRO
  });

  it("shows the current Junior plan + the Senior / Developer / Institutional tiers", () => {
    render(<UpgradeModal onClose={vi.fn()} refetch={vi.fn()} />);
    expect(screen.getByText(/you're on junior/i)).toBeTruthy();
    expect(screen.getByText("Senior")).toBeTruthy();
    expect(screen.getByText("Developer")).toBeTruthy();
    expect(screen.getByText("Institutional")).toBeTruthy();
  });

  it("Institutional CTA → sales@aaagents.de; Developer CTA → GitHub", () => {
    render(<UpgradeModal onClose={vi.fn()} refetch={vi.fn()} />);
    expect(screen.getByRole("link", { name: /contact sales/i }).getAttribute("href")).toMatch(
      /^mailto:sales@aaagents\.de/,
    );
    expect(screen.getByRole("link", { name: /view on github/i }).getAttribute("href")).toMatch(
      /github\.com\/Autonomous-Asset-Management-Agents/,
    );
  });

  it("CLAIM SENIOR: on a successful claim it refetches + closes", async () => {
    claimBeta.mockResolvedValue({ status: "claimed", tier: "PRO" });
    const refetch = vi.fn().mockResolvedValue(undefined);
    const onClose = vi.fn();
    render(<UpgradeModal onClose={onClose} refetch={refetch} />);
    claimSenior();
    await waitFor(() => expect(refetch).toHaveBeenCalled());
    expect(onClose).toHaveBeenCalled();
  });

  it("never fails silently: absent bridge (browser) → visible note", async () => {
    claimBeta.mockResolvedValue({ status: "error", error: "desktop-only" });
    render(<UpgradeModal onClose={vi.fn()} refetch={vi.fn()} />);
    claimSenior();
    expect(await screen.findByText(/available in the desktop app/i)).toBeTruthy();
  });

  it("never fails silently: free-beta cap reached → visible note", async () => {
    claimBeta.mockResolvedValue({ status: "cap-reached" });
    render(<UpgradeModal onClose={vi.fn()} refetch={vi.fn()} />);
    claimSenior();
    expect(await screen.findByText(/beta full/i)).toBeTruthy();
  });

  it("never fails silently: claim error → visible note", async () => {
    claimBeta.mockResolvedValue({ status: "error", error: "bundled license has no token" });
    render(<UpgradeModal onClose={vi.fn()} refetch={vi.fn()} />);
    claimSenior();
    expect(await screen.findByText(/upgrade failed/i)).toBeTruthy();
  });

  it("tier-aware: for Senior (PRO) the Senior card is the current plan (no claim CTA), Institutional/Developer stay reachable", () => {
    useStore.setState({ tier: "PRO" });
    render(<UpgradeModal onClose={vi.fn()} refetch={vi.fn()} />);
    expect(screen.getByText(/you're on senior/i)).toBeTruthy();
    expect(screen.getByText(/your current plan/i)).toBeTruthy();
    // No in-app Senior claim once you already are Senior…
    expect(screen.queryByRole("button", { name: /claim senior/i })).toBeNull();
    // …but the other editions remain reachable.
    expect(screen.getByRole("link", { name: /contact sales/i })).toBeTruthy();
    expect(screen.getByRole("link", { name: /view on github/i })).toBeTruthy();
  });
});
