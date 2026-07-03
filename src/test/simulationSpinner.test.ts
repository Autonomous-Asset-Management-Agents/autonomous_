// The Simulation page must show a progress/spinner indicator while a backtest is running, so a long
// run reads as "working" and not frozen. Source-contract (the running state needs an engine + poll).
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url)); // src/test
const src = readFileSync(
  path.join(here, "..", "console", "desktop", "pages", "Simulation.tsx"),
  "utf8",
);

describe("Simulation: running progress indicator", () => {
  it("renders a spinning wheel while running (chart area + button)", () => {
    const spinners = src.match(/animate-spin/g) ?? [];
    expect(spinners.length).toBeGreaterThanOrEqual(2);
  });
  it("the spinner is gated on the running state", () => {
    expect(src).toMatch(/running \?[\s\S]{0,400}animate-spin/);
  });
});
