// SIM-1 T2 (#1485): the Console honestly surfaces whether the backtest was survivorship-adjusted.
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const dir = path.dirname(fileURLToPath(import.meta.url));
const read = (p: string) => readFileSync(path.join(dir, "..", p), "utf8");

describe("SIM-1 T2: survivorship honesty in the Console", () => {
  it("the api SimulationResult carries the survivorship_adjusted flag", () => {
    expect(read("lib/api.ts")).toMatch(/survivorship_adjusted\??:\s*boolean/);
  });

  it("the Simulation page warns on survivorship bias and notes point-in-time adjustment", () => {
    const src = read("console/desktop/pages/Simulation.tsx");
    expect(src).toMatch(/result\.survivorship_adjusted/);
    expect(src).toMatch(/survivorship bias/i);
    expect(src).toMatch(/point-in-time/i);
  });
});
