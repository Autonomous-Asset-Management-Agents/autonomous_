import { describe, it, expect } from "vitest";
import {
  macDesktopReleaseTag,
  macDownloadUrl,
  MAC_DOWNLOAD_URL,
  platformDownload,
  STABLE_DESKTOP_VERSION_MAC,
  WINDOWS_DOWNLOAD_URL,
} from "../lib/appVersion";
import { semverGt } from "../lib/desktopBridge";

// #2739/S2 — platform-aware download links + fail-open semver guard.

describe("mac download links (additive, Windows untouched)", () => {
  it("mac tag uses the separate desktop-mac-v* namespace", () => {
    expect(macDesktopReleaseTag("0.4.0")).toBe("desktop-mac-v0.4.0-beta");
  });
  it("mac download url points at the fixed arm64 .dmg asset", () => {
    expect(macDownloadUrl("0.4.0")).toBe(
      "https://github.com/Autonomous-Asset-Management-Agents/autonomous_/releases/download/desktop-mac-v0.4.0-beta/AAAgents-arm64.dmg",
    );
  });
  it("mac stable version falls back to 0.0.0 (no mac release yet) and stays off the Windows tag", () => {
    // build-time define resolves from desktop/stable-release.json.mac_version
    expect(MAC_DOWNLOAD_URL).toContain("desktop-mac-v");
    expect(MAC_DOWNLOAD_URL).toContain("AAAgents-arm64.dmg");
    expect(STABLE_DESKTOP_VERSION_MAC).toMatch(/^\d+\.\d+\.\d+$/);
  });
  it("Windows download link is unchanged (still the .exe on the desktop-v* namespace)", () => {
    expect(WINDOWS_DOWNLOAD_URL).toContain("autonomous_setup.exe");
    expect(WINDOWS_DOWNLOAD_URL).toContain("/desktop-v");
    expect(WINDOWS_DOWNLOAD_URL).not.toContain("desktop-mac-v");
  });
});

describe("platformDownload — OS-aware installer pick (S10)", () => {
  it("macOS UA → the Apple-Silicon .dmg", () => {
    const d = platformDownload("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5)");
    expect(d.os).toBe("mac");
    expect(d.url).toBe(MAC_DOWNLOAD_URL);
    expect(d.label).toBe("Download for Mac");
  });
  it("Windows UA → the .exe (default behaviour preserved)", () => {
    const d = platformDownload("Mozilla/5.0 (Windows NT 10.0; Win64; x64)");
    expect(d.os).toBe("windows");
    expect(d.url).toBe(WINDOWS_DOWNLOAD_URL);
  });
  it("iOS is NOT treated as mac (no .dmg on iPhone/iPad)", () => {
    expect(platformDownload("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)").os).toBe(
      "windows",
    );
  });
  it("unknown/empty UA defaults to Windows (bot/SSR safe)", () => {
    expect(platformDownload("").os).toBe("windows");
  });
});
describe("semverGt — fail-open update guard", () => {
  it("newer latest → true (real update)", () => {
    expect(semverGt("0.4.1", "0.4.0")).toBe(true);
    expect(semverGt("1.0.0", "0.9.9")).toBe(true);
  });
  it("older or equal latest → false (suppress the banner)", () => {
    expect(semverGt("0.3.6", "0.4.0")).toBe(false); // the exact reported A1 bug
    expect(semverGt("0.4.0", "0.4.0")).toBe(false);
  });
  it("installed unknown/null → null (FAIL-OPEN, keep the shell's flag)", () => {
    expect(semverGt("0.4.0", null)).toBeNull();
    expect(semverGt("0.4.0", undefined)).toBeNull();
  });
  it("unparseable suffixes → null (fail-open, never silently hide an update)", () => {
    expect(semverGt("0.4.0", "0.4.0-mac-demo")).toBe(false); // core 0.4.0 == 0.4.0 → not an update
    expect(semverGt("garbage", "0.4.0")).toBeNull();
    expect(semverGt("v0.4.1", "0.4.0")).toBe(true); // tolerates a leading v
  });
});
