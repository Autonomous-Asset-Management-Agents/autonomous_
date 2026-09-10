import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import {
  APP_VERSION,
  STABLE_DESKTOP_VERSION,
  DESKTOP_RELEASE_TAG,
  WINDOWS_DOWNLOAD_URL,
  desktopReleaseTag,
  windowsDownloadUrl,
  resolveStableVersion,
} from "@/lib/appVersion";

// #1340 / #1941: the web build's app version is a single source of truth, injected by Vite's
// `define`. #2270 splits that into TWO numbers: APP_VERSION (the installed/running build, from
// desktop/package.json → the Settings "Version" row) and STABLE_DESKTOP_VERSION (the last PROD
// release, from desktop/stable-release.json → the public download link). A test/rc build bumps
// package.json but NOT stable-release.json, so the website link does not move.
const here = path.dirname(fileURLToPath(import.meta.url)); // src/test
const readJson = (rel: string) =>
  JSON.parse(readFileSync(path.join(here, "..", "..", rel), "utf8")) as { version: string };
const desktopPkg = readJson(path.join("desktop", "package.json"));
const stableRelease = readJson(path.join("desktop", "stable-release.json"));

describe("appVersion — installed build vs stable release (SSOT — #1340 / #2270)", () => {
  it("APP_VERSION is injected from desktop/package.json (the installed/running build)", () => {
    expect(APP_VERSION).toBe(desktopPkg.version);
  });

  it("STABLE_DESKTOP_VERSION is injected from desktop/stable-release.json (last PROD release)", () => {
    expect(STABLE_DESKTOP_VERSION).toBe(stableRelease.version);
  });

  it("the Windows download URL is DERIVED from STABLE_DESKTOP_VERSION, not APP_VERSION (#2270)", () => {
    expect(DESKTOP_RELEASE_TAG).toBe(`desktop-v${STABLE_DESKTOP_VERSION}-beta`);
    expect(WINDOWS_DOWNLOAD_URL).toBe(
      "https://github.com/Autonomous-Asset-Management-Agents/autonomous_/releases/download/" +
        `desktop-v${STABLE_DESKTOP_VERSION}-beta/autonomous_setup.exe`,
    );
  });
});

// #2270 INC-1: prove the download link follows the STABLE version and NOT the (possibly newer)
// installed app version. The build-time defines are static within a single suite run, so the
// derivation is exposed as a pure fn to exercise it with distinct values (stable=0.1.20 while the
// rc build = 0.2.1).
describe("appVersion — prod-only download link decoupling (#2270)", () => {
  it("windowsDownloadUrl follows the STABLE version even when the app version is ahead", () => {
    // stable (last PROD) = 0.1.20, installed rc build = 0.2.1 → link must point at 0.1.20.
    const url = windowsDownloadUrl("0.1.20");
    expect(url).toContain("desktop-v0.1.20-beta");
    expect(url).not.toContain("0.2.1");
    expect(desktopReleaseTag("0.1.20")).toBe("desktop-v0.1.20-beta");
  });

  it("resolveStableVersion falls back to the app version when the stable define is absent", () => {
    expect(resolveStableVersion("0.1.20", "0.2.1")).toBe("0.1.20");
    expect(resolveStableVersion(undefined, "0.2.1")).toBe("0.2.1");
  });
});

describe("appVersion — no hardcoded release tag downstream (#1340)", () => {
  it("PricingMaster uses the derived URL, not a hardcoded release tag", () => {
    const src = readFileSync(path.join(here, "..", "pages", "PricingMaster.tsx"), "utf8");
    expect(src).toMatch(/WINDOWS_DOWNLOAD_URL|platformDownload/);
    // no hardcoded `desktop-vX.Y.Z-beta` tag left inline
    expect(src).not.toMatch(/desktop-v\d+\.\d+\.\d+-beta/);
  });
});
