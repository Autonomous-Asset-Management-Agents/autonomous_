import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

/**
 * #2077 Increment 3 — Settings → System "Update" card. Shows the installed version and,
 * when the shell has found a newer one, the latest version + a Download link. In the
 * up-to-date state it offers "Check for updates"; when an update is already surfaced the
 * check just ran, so that button is intentionally absent. Desktop-only.
 */
const getUpdateStatus = vi.fn();
const checkForUpdatesNow = vi.fn();
const getAppVersion = vi.fn();
let desktop = true;

vi.mock("@/lib/desktopBridge", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/desktopBridge")>()),
  isDesktop: () => desktop,
  getAppVersion: () => getAppVersion(),
  getUpdateStatus: () => getUpdateStatus(),
  checkForUpdatesNow: () => checkForUpdatesNow(),
}));

import { UpdateCard } from "../console/desktop/UpdateCard";

const AVAILABLE = {
  updateAvailable: true,
  latestVersion: "0.1.16",
  downloadUrl: "https://aaagents.de/download",
  changelog: "Stop-loss + compliance exit fix",
  checkedAt: "2026-07-14T20:00:00.000Z",
};
const UP_TO_DATE = {
  updateAvailable: false,
  latestVersion: null,
  downloadUrl: null,
  changelog: null,
  checkedAt: "2026-07-14T20:00:00.000Z",
};

describe("Settings → System · Update card", () => {
  beforeEach(() => {
    desktop = true;
    getAppVersion.mockResolvedValue("0.1.15");
    getUpdateStatus.mockResolvedValue(UP_TO_DATE);
    checkForUpdatesNow.mockResolvedValue(UP_TO_DATE);
  });
  afterEach(() => vi.clearAllMocks());

  it("update available → shows latest version + Download, and NO 'Check for updates'", async () => {
    getUpdateStatus.mockResolvedValue(AVAILABLE);
    render(<UpdateCard />);
    expect(await screen.findByText(/Update available/i)).toBeTruthy();
    expect(screen.getByText("0.1.16")).toBeTruthy();
    const dl = screen.getByRole("link", { name: /download/i }) as HTMLAnchorElement;
    expect(dl.getAttribute("href")).toBe("https://aaagents.de/download");
    expect(screen.queryByRole("button", { name: /check for updates/i })).toBeNull();
  });

  it("up to date → shows 'Up to date' + 'Check for updates', no Download", async () => {
    render(<UpdateCard />);
    expect(await screen.findByText(/Up to date/i)).toBeTruthy();
    expect(screen.getByRole("button", { name: /check for updates/i })).toBeTruthy();
    expect(screen.queryByRole("link", { name: /download/i })).toBeNull();
  });

  it("'Check for updates' calls the bridge and surfaces a freshly found update", async () => {
    render(<UpdateCard />);
    const btn = await screen.findByRole("button", { name: /check for updates/i });
    checkForUpdatesNow.mockResolvedValue(AVAILABLE);
    fireEvent.click(btn);
    await waitFor(() => expect(checkForUpdatesNow).toHaveBeenCalled());
    expect(await screen.findByText(/Update available/i)).toBeTruthy();
    expect(screen.getByText("0.1.16")).toBeTruthy();
  });

  it("renders nothing in the browser (not desktop)", () => {
    desktop = false;
    const { container } = render(<UpdateCard />);
    expect(container.firstChild).toBeNull();
  });
});
