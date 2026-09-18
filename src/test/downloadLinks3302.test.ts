// #3302 (10.09., owner): "die links für macos, oss und windows gehen auf eine 404". Measured
// against the live site and the real releases:
//
//   desktop-v0.4.11-beta/autonomous_setup.exe      404   ← what the site links to
//   desktop-v0.4.11/autonomous_setup.exe           200   ← what actually exists
//   desktop-mac-v0.4.11-beta/AAAgents-arm64.dmg    404
//   desktop-mac-v0.4.11/AAAgents-arm64.dmg         200
//   .../Dev-Enviroment/blob/main/docs/oss/README.md 404  ← the PRIVATE repo
//
// Two independent defects: appVersion.ts hardcoded a `-beta` suffix the production tags do not
// carry, and Support.tsx linked the private repo. 0.4.10 hid the first one because it happened to
// be published under BOTH tags; 0.4.11 was published as `desktop-v0.4.11` only, and the link died.
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import {
  desktopReleaseTag,
  macDesktopReleaseTag,
  windowsDownloadUrl,
  macDownloadUrl,
} from "@/lib/appVersion";

describe("download links (#3302) — the tag must be the one production publishes", () => {
  it("no -beta suffix: that tag does not exist for a prod release", () => {
    expect(desktopReleaseTag("0.4.11")).toBe("desktop-v0.4.11");
    expect(macDesktopReleaseTag("0.4.11")).toBe("desktop-mac-v0.4.11");
  });

  it("the built URLs are the ones that resolved 200 when measured", () => {
    expect(windowsDownloadUrl("0.4.11")).toBe(
      "https://github.com/Autonomous-Asset-Management-Agents/autonomous_/releases/download/desktop-v0.4.11/autonomous_setup.exe",
    );
    expect(macDownloadUrl("0.4.11")).toBe(
      "https://github.com/Autonomous-Asset-Management-Agents/autonomous_/releases/download/desktop-mac-v0.4.11/AAAgents-arm64.dmg",
    );
  });

  it("the dead 404 shapes can never come back", () => {
    for (const v of ["0.4.11", "0.5.0", "1.0.0"]) {
      expect(windowsDownloadUrl(v)).not.toContain("-beta");
      expect(macDownloadUrl(v)).not.toContain("-beta");
    }
  });
});

describe("public links (#3302) — never point visitors at the private repo", () => {
  const SRC_FILES = ["src/pages/Support.tsx", "src/lib/appVersion.ts"];

  it("no source that builds a visitor-facing link mentions the private repo", () => {
    for (const f of SRC_FILES) {
      const src = readFileSync(resolve(process.cwd(), f), "utf8");
      expect(src, `${f} links the PRIVATE repo — 404 for every visitor`).not.toContain(
        "Autonomous-Asset-Management-Agents/Dev-Enviroment",
      );
    }
  });

  it("Support.tsx points at the public repo instead", () => {
    const src = readFileSync(resolve(process.cwd(), "src/pages/Support.tsx"), "utf8");
    expect(src).toContain("Autonomous-Asset-Management-Agents/autonomous_");
  });
});
