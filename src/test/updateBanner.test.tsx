import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

/**
 * #2077 Increment 3 — non-blocking update banner at the top of the console. Shows only when
 * the shell found a newer version and the user hasn't dismissed THAT version. Dismiss is
 * per-version (localStorage), so a later version surfaces again. Desktop-only.
 */
const getUpdateStatus = vi.fn();
const getAppVersion = vi.fn();
let desktop = true;

vi.mock("@/lib/desktopBridge", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/desktopBridge")>()),
  isDesktop: () => desktop,
  getAppVersion: () => getAppVersion(),
  getUpdateStatus: () => getUpdateStatus(),
}));

import { UpdateBanner } from "../console/desktop/UpdateBanner";

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
  checkedAt: null,
};

describe("Update banner", () => {
  beforeEach(() => {
    desktop = true;
    localStorage.clear();
    getAppVersion.mockResolvedValue("0.1.15");
    getUpdateStatus.mockResolvedValue(UP_TO_DATE);
  });
  afterEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it("shows the banner with version + Download when an update is available", async () => {
    getUpdateStatus.mockResolvedValue(AVAILABLE);
    render(<UpdateBanner />);
    expect(await screen.findByText(/0\.1\.16/)).toBeTruthy();
    const dl = screen.getByRole("link", { name: /download/i }) as HTMLAnchorElement;
    expect(dl.getAttribute("href")).toBe("https://aaagents.de/download");
  });

  it("mac line (#2853): the DMG url renders as a Download link that opens externally, never an Install control", async () => {
    // On macOS the shell's check hands out the DMG url from the desktop-mac-v* release's
    // version.json. The banner stays notify-only: target=_blank routes the click through the
    // shell's setWindowOpenHandler → shell.openExternal, nothing is downloaded or installed in-app.
    getUpdateStatus.mockResolvedValue({
      ...AVAILABLE,
      latestVersion: "0.4.14",
      downloadUrl:
        "https://github.com/Autonomous-Asset-Management-Agents/autonomous_/releases/download/desktop-mac-v0.4.14-beta/AAAgents-arm64.dmg",
    });
    render(<UpdateBanner />);
    const dl = (await screen.findByRole("link", { name: /download/i })) as HTMLAnchorElement;
    expect(dl.getAttribute("href")).toMatch(/\/desktop-mac-v0\.4\.14-beta\/AAAgents-arm64\.dmg$/);
    expect(dl.getAttribute("target")).toBe("_blank");
    expect(screen.queryByRole("button", { name: /install/i })).toBeNull();
    expect(screen.queryByRole("link", { name: /install/i })).toBeNull();
  });

  it("renders nothing when up to date", async () => {
    const { container } = render(<UpdateBanner />);
    await waitFor(() => expect(getUpdateStatus).toHaveBeenCalled());
    expect(container.firstChild).toBeNull();
  });

  it("dismiss hides the banner and persists per version", async () => {
    getUpdateStatus.mockResolvedValue(AVAILABLE);
    render(<UpdateBanner />);
    const x = await screen.findByRole("button", { name: /dismiss/i });
    fireEvent.click(x);
    await waitFor(() => expect(screen.queryByText(/0\.1\.16/)).toBeNull());
    expect(localStorage.getItem("aaa.updateDismissed.0.1.16")).toBe("1");
  });

  it("stays hidden for an already-dismissed version", async () => {
    localStorage.setItem("aaa.updateDismissed.0.1.16", "1");
    getUpdateStatus.mockResolvedValue(AVAILABLE);
    const { container } = render(<UpdateBanner />);
    await waitFor(() => expect(getUpdateStatus).toHaveBeenCalled());
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing in the browser (not desktop)", async () => {
    desktop = false;
    getUpdateStatus.mockResolvedValue(AVAILABLE);
    const { container } = render(<UpdateBanner />);
    expect(container.firstChild).toBeNull();
  });
});
