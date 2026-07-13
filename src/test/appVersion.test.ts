import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { APP_VERSION, DESKTOP_RELEASE_TAG, WINDOWS_DOWNLOAD_URL } from "@/lib/appVersion";

// #1340 / #1941: the web build's app version is a single source of truth, injected
// by Vite's `define` from desktop/package.json. Downstream surfaces (the Windows
// download link here, the legal notice later) derive from it — no hand-edited
// literals to drift (which is what forced the #1899 manual href bump).
const here = path.dirname(fileURLToPath(import.meta.url)); // src/test
const desktopPkg = JSON.parse(
  readFileSync(path.join(here, "..", "..", "desktop", "package.json"), "utf8"),
) as { version: string };

describe("appVersion (SSOT — #1340)", () => {
  it("APP_VERSION is injected from desktop/package.json", () => {
    expect(APP_VERSION).toBe(desktopPkg.version);
  });

  it("the Windows download URL is DERIVED from APP_VERSION (no hardcoded version tag)", () => {
    expect(DESKTOP_RELEASE_TAG).toBe(`desktop-v${APP_VERSION}-beta`);
    expect(WINDOWS_DOWNLOAD_URL).toBe(
      "https://github.com/Autonomous-Asset-Management-Agents/autonomous_/releases/download/" +
        `desktop-v${APP_VERSION}-beta/autonomous_setup.exe`,
    );
  });

  it("PricingMaster uses the derived URL, not a hardcoded release tag", () => {
    const src = readFileSync(path.join(here, "..", "pages", "PricingMaster.tsx"), "utf8");
    expect(src).toMatch(/WINDOWS_DOWNLOAD_URL/);
    // no hardcoded `desktop-vX.Y.Z-beta` tag left inline
    expect(src).not.toMatch(/desktop-v\d+\.\d+\.\d+-beta/);
  });
});
