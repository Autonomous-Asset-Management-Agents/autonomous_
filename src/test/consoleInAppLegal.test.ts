// GTM-1 T2 (#1465): the in-app legal surface. The logged-in Console must reach the legal pages
// in-app (offline-safe, via the BUNDLED /legal/* routes — Option B of the epic Dual Design, NOT
// external aaagents.de links) and show a risk/AI-transparency notice at the trading/live point.
// Source-contract test (the existing consoleSidebar/consoleSettings render tests guard rendering).
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const dir = path.dirname(fileURLToPath(import.meta.url));
const read = (p: string) => readFileSync(path.join(dir, "..", p), "utf8");

describe("GTM-1 T2: in-app legal surface (offline-safe, bundled)", () => {
  it("Sidebar links to the bundled in-app /legal/* pages — not an external host", () => {
    const src = read("console/desktop/Sidebar.tsx");
    expect(src).toMatch(/href=["']\/legal\/imprint["']/);
    expect(src).toMatch(/href=["']\/legal\/privacy["']/);
    expect(src).toMatch(/href=["']\/legal\/risk-disclosure["']/);
    // offline-safe: in-app routes, never an external aaagents.de legal link
    expect(src).not.toMatch(/aaagents\.de\/legal/);
  });

  it("Settings shows a risk/AI-transparency notice linking to the in-app risk disclosure", () => {
    const src = read("console/desktop/pages/Settings.tsx");
    expect(src).toMatch(/not investment advice/i);
    expect(src).toMatch(/href=["']\/legal\/risk-disclosure["']/);
  });
});
