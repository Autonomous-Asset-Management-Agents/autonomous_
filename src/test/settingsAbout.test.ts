// The Settings page must carry an "About" card whose facts come from the canonical NOTICE file:
// the product intent, the name, the company, and the founders/developers. Source-contract (Settings
// pulls in the engine/store/desktop bridge, so a full render is heavy; the card is static copy).
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url)); // src/test
const settings = readFileSync(
  path.join(here, "..", "console", "desktop", "pages", "Settings.tsx"),
  "utf8",
);

describe("Settings: About card (facts from NOTICE)", () => {
  it("has an About section for autonomous_", () => {
    expect(settings).toMatch(/About autonomous_/);
  });
  it("names the corporate entity", () => {
    expect(settings).toContain("Autonomous Asset Management Agents UG");
  });
  it("names both founders / developers", () => {
    expect(settings).toContain("Andreas Apeldorn");
    expect(settings).toContain("Georg Apeldorn");
  });
  it("states the intent and licence", () => {
    expect(settings).toMatch(/paper mode/i);
    expect(settings).toMatch(/Apache License 2\.0/);
  });
  it("no longer shows the old AAAgents brand on this page", () => {
    expect(settings).not.toContain("AAAgents");
  });
});
