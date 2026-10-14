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

  it("contains no term from the epic's forbidden list (link targets excluded)", () => {
    expect(withoutLinkTargets(text()).match(FORBIDDEN) ?? []).toEqual([]);
  });

  it("the guard still sees forbidden terms in link text (rule self-check)", () => {
    const sample = "- [Safe asset management](https://github.com/Autonomous-Asset-Management-Agents/x)";
    expect(withoutLinkTargets(sample).match(FORBIDDEN)).toEqual(["Safe", "asset management"]);
  });
});
