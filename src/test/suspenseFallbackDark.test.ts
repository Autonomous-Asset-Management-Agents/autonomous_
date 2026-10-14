// #2804 (second pass): the WHITE startup flash of the desktop app was App's route-level
// <Suspense> fallback. The desktop shell loads from 127.0.0.1 → hostnameDefault() resolves the
// landing A/B variant "landing-b" → the fallback painted a full-viewport `bg-white` div while
// the lazy /console chunk loaded (seconds on cold machines). Verified via an instrumented
// Electron harness: DOM stack at show+150ms was `DIV.min-h-screen bg-white`, page brightness
// 255 until the console chunk mounted. Console routes must get a BLACK fallback regardless of
// the marketing variant; the public landing keeps its variant-matched fallback.
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { suspenseFallbackClass } from "@/lib/suspenseFallback";

describe("suspense fallback background (#2804)", () => {
  it("console routes are ALWAYS black, whatever the landing variant says", () => {
    expect(suspenseFallbackClass("landing-b", "/console")).toBe("bg-black");
    expect(suspenseFallbackClass("stitch-v1", "/console")).toBe("bg-black");
    expect(suspenseFallbackClass("v1", "/console")).toBe("bg-black");
    expect(suspenseFallbackClass("landing-b", "/console/app")).toBe("bg-black");
  });

  it("the public landing keeps its variant-matched fallback", () => {
    expect(suspenseFallbackClass("landing-b", "/")).toBe("bg-white");
    expect(suspenseFallbackClass("stitch-v1", "/")).toBe("bg-[#F3F4F6]");
    expect(suspenseFallbackClass("v1", "/")).toBe("bg-black");
  });

  it("App.tsx actually uses the helper (tripwire against a silent revert)", () => {
    const app = readFileSync(resolve(process.cwd(), "src/App.tsx"), "utf8");
    expect(app).toContain("suspenseFallbackClass(");
    expect(app, "the old variant-only fallbackBg ternary must be gone").not.toMatch(
      /fallbackBg\s*=\s*variant\s*===/
    );
  });
});
