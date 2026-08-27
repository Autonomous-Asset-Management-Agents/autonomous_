// GTM-1 T4 (#1467): the website copy must match the shipped engine (LIVE-1).
// LIVE-1 T1 dropped "shadow mode" (honest paper trading via PAPER_TRADING); T4/T1 made live a
// deliberate, WORM-verified runtime confirmation (/api/live/enable) — NOT a code-level change.
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const dir = path.dirname(fileURLToPath(import.meta.url));
const read = (f: string) =>
  readFileSync(path.join(dir, "..", "components", "views", f), "utf8");

describe("GTM-1 T4: live landing copy reflects the shipped engine", () => {
  for (const file of ["LandingViewE.tsx", "LandingViewD.tsx"]) {
    it(`${file}: no outdated shadow-mode / code-level-change copy`, () => {
      const src = read(file);
      expect(src).not.toMatch(/shadow mode/i);
      expect(src).not.toMatch(/code-level change/i);
      expect(src).not.toMatch(/code-flag for live/i);
      // live = a deliberate, WORM-verified confirmation (LIVE-1 T1/T4)
      expect(src).toMatch(/WORM-verified/i);

      // #1880 Landing page truthfulness assertions
      expect(src).not.toMatch(/twelve-classifier/i);
      expect(src).not.toMatch(/twelve senators/i);
      expect(src).not.toMatch(/five hundred analysts/i);
      expect(src).not.toMatch(/performance · live/i);
      expect(src).not.toMatch(/audit chain · live/i);
      expect(src).toMatch(/nine voting agents/i);
    });
  }
});
