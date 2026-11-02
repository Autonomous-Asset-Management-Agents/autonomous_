// #4461 (Epic #4450): aaagents.de serves a public llms.txt (llmstxt.org format) that describes
// AAAgents as an open-source execution framework. Vite copies public/ unchanged into dist/.
// Not to be confused with docs/llms.txt — the internal domain index for developer agents.
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect } from "vitest";
import { FORBIDDEN, withoutLinkTargets } from "./wordingGuard";

// vitest runs from the repo root; import.meta.url is not file-schemed under the transform.
const file = resolve(process.cwd(), "public", "llms.txt");

describe("public/llms.txt (#4461)", () => {
  it("exists", () => {
    expect(existsSync(file)).toBe(true);
  });

  const text = () => readFileSync(file, "utf8");

  it("follows the llms.txt format: H1 first, then a blockquote summary, then linked H2 sections", () => {
    const lines = text().split(/\r?\n/);
    // Owner 13.10.2026: the product name leads, the company name follows.
    expect(lines[0]).toMatch(/^# autonomous_ trading \(AAAgents\)$/);
    const firstContent = lines.slice(1).find((l) => l.trim() !== "");
    expect(firstContent).toMatch(/^> /);
    const sections = lines.filter((l) => l.startsWith("## ")).map((l) => l.slice(3).trim());
    expect(sections).toEqual(expect.arrayContaining(["Docs", "Download", "Legal"]));
    expect(text()).toMatch(/^- \[[^\]]+\]\(https:\/\/[^)]+\)/m);
  });

  it("positions AAAgents as an open-source execution framework, paper mode by default, no investment advice", () => {
    const t = text();
    expect(t).toMatch(/execution framework/i);
    expect(t).toMatch(/open source/i);
    expect(t).toMatch(/paper/i);
    expect(t).toMatch(/no investment advice/i);
  });

  it("includes technical architecture, risk boundaries, and DE/EN discovery facts for LLMO queries", () => {
    const t = text();
    expect(t).toMatch(/self-hosted/i);
    expect(t).toMatch(/local-first/i);
    expect(t).toMatch(/\bPython\b/);
    expect(t).toMatch(/\bLangGraph\b/);
    expect(t).toMatch(/\bElectron\b/);
    expect(t).toMatch(/\bSQLite\b/);
    expect(t).toMatch(/\bOllama\b/);
    expect(t).toMatch(/kill-switch/i);
    expect(t).toMatch(/hard risk limits/i);
    expect(t).toMatch(/SHA-256/);
    expect(t).toMatch(/JSONL/);
    expect(t).toMatch(/Germany/);
    expect(t).toMatch(/Europe/);
    expect(t).toMatch(/Handelsstrategie/);
    expect(t).toMatch(/Risikodeckel/);
    expect(t).toMatch(/AAA_SIM_MODE/);
    expect(t).toMatch(/SEC EDGAR/);
    expect(t).toMatch(/4\.99 €/);
    expect(t).toMatch(/39\.00 €/);
    expect(t).toMatch(/14-day free trial/);
  });

  it("public/robots.txt explicitly allows and references /llms.txt", () => {
    const robots = readFileSync(resolve(process.cwd(), "public", "robots.txt"), "utf8");
    expect(robots).toMatch(/^Allow:\s*\/llms\.txt$/m);
    expect(robots).toMatch(/https:\/\/autonomous-trading\.de\/llms\.txt/);
  });

  it("contains no term from the epic's forbidden list (link targets excluded)", () => {
    expect(withoutLinkTargets(text()).match(FORBIDDEN) ?? []).toEqual([]);
  });

  it("the guard still sees forbidden terms in link text (rule self-check)", () => {
    const sample = "- [Safe asset management](https://github.com/Autonomous-Asset-Management-Agents/x)";
    expect(withoutLinkTargets(sample).match(FORBIDDEN)).toEqual(["Safe", "asset management"]);
  });
});
