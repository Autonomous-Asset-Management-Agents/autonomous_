// Focus unit for the fail-open semver guard behind getUpdateStatus() / checkForUpdatesNow()
// (#2739/S2). Regression for a shipped ReferenceError: guardUpdate() called an undefined
// `getVersion()` instead of the module's own getAppVersion(). Vite does not type-check, so
// the renderer built fine and both helpers REJECTED on exactly the branch they exist for
// (shell reports updateAvailable: true) — the update banner/card could never show an update.
// Tested against a stubbed `window.aaagents` (jsdom is the global vitest env), NOT via an
// UpdateCard/UpdateBanner render — those mock this module away and would never see the throw.
import { describe, it, expect, afterEach, vi } from "vitest";
import { getUpdateStatus, checkForUpdatesNow } from "@/lib/desktopBridge";
import type { UpdateStatus } from "@/lib/desktopBridge";

type WinWithBridge = { aaagents?: Record<string, unknown> };
const setBridge = (impl: Record<string, unknown> | undefined) => {
  (window as unknown as WinWithBridge).aaagents = impl;
};

const AVAILABLE: UpdateStatus = {
  updateAvailable: true,
  latestVersion: "9.9.9",
  downloadUrl: "https://aaagents.de/download",
  changelog: "Stop-loss + compliance exit fix",
  checkedAt: "2026-09-24T08:00:00.000Z",
};

describe("getUpdateStatus() / checkForUpdatesNow() — fail-open semver guard over the shell status", () => {
  afterEach(() => setBridge(undefined));

  it("getUpdateStatus(): latest 9.9.9 > installed 0.5.1 → resolves updateAvailable: true, does NOT throw", async () => {
    const shellGetUpdateStatus = vi.fn().mockResolvedValue(AVAILABLE);
    const shellGetVersion = vi.fn().mockResolvedValue("0.5.1");
    setBridge({ isDesktop: true, getUpdateStatus: shellGetUpdateStatus, getVersion: shellGetVersion });

    await expect(getUpdateStatus()).resolves.toEqual(AVAILABLE);
    expect(shellGetUpdateStatus).toHaveBeenCalledOnce();
    // The installed version comes from the bridge's getVersion (via getAppVersion), nothing else.
    expect(shellGetVersion).toHaveBeenCalledOnce();
  });

  it("checkForUpdatesNow(): same guard, same path → resolves updateAvailable: true, does NOT throw", async () => {
    const shellCheck = vi.fn().mockResolvedValue(AVAILABLE);
    setBridge({ isDesktop: true, checkForUpdatesNow: shellCheck, getVersion: async () => "0.5.1" });

    await expect(checkForUpdatesNow()).resolves.toEqual(AVAILABLE);
    expect(shellCheck).toHaveBeenCalledOnce();
  });

  it("shell reports an update for latest <= installed → corrected to updateAvailable: false", async () => {
    // GitHub `releases/latest` can resolve to an OLDER non-prerelease (the #2739 A1 bug).
    setBridge({
      isDesktop: true,
      getUpdateStatus: async () => ({ ...AVAILABLE, latestVersion: "0.3.6" }),
      getVersion: async () => "0.4.0",
    });

    await expect(getUpdateStatus()).resolves.toMatchObject({ updateAvailable: false, latestVersion: "0.3.6" });
  });

  it("FAIL-OPEN: installed version absent (bridge without getVersion) → shell's flag left untouched", async () => {
    setBridge({ isDesktop: true, getUpdateStatus: async () => AVAILABLE });

    await expect(getUpdateStatus()).resolves.toEqual(AVAILABLE);
  });

  it("FAIL-OPEN: unparseable latestVersion → shell's flag left untouched (never silently hide an update)", async () => {
    const garbage = { ...AVAILABLE, latestVersion: "garbage" };
    setBridge({ isDesktop: true, getUpdateStatus: async () => garbage, getVersion: async () => "0.5.1" });

    await expect(getUpdateStatus()).resolves.toEqual(garbage);
  });

  it("shell reports no update → returned as-is, installed version is not even asked for", async () => {
    const shellGetVersion = vi.fn().mockResolvedValue("0.5.1");
    const upToDate: UpdateStatus = { ...AVAILABLE, updateAvailable: false, latestVersion: null };
    setBridge({ isDesktop: true, getUpdateStatus: async () => upToDate, getVersion: shellGetVersion });

    await expect(getUpdateStatus()).resolves.toEqual(upToDate);
    expect(shellGetVersion).not.toHaveBeenCalled();
  });
});
