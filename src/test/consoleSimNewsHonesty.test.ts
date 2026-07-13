// SIM-1 T3 (#1486): the simulation honestly labels its news/sentiment as simulated (no paid feed).
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const dir = path.dirname(fileURLToPath(import.meta.url));
const readSrc = (p: string) => readFileSync(path.join(dir, "..", p), "utf8");
const readRepo = (p: string) => readFileSync(path.join(dir, "..", "..", p), "utf8");

describe("SIM-1 T3: honest simulated-news labelling", () => {
  it("the Simulation page labels the backtest news/sentiment as simulated, no paid feed", () => {
    const src = readSrc("console/desktop/pages/Simulation.tsx");
    expect(src).toMatch(/news &amp; sentiment in this backtest are/i);
    expect(src).toMatch(/simulated/);
    expect(src).toMatch(/no external paid data source/i);
  });

  it("the FAQ documents the simulation's honest limits (simulated news, no paid data)", () => {
    const faq = readRepo("docs/oss/FAQ.md");
    expect(faq).toMatch(/news & sentiment are/i);
    expect(faq).toMatch(/simulated/);
    expect(faq).toMatch(/no paid data source/i);
  });
});
