// #4459 (Epic #4450): the landing (aaagents.de, hosting target "demo") must be indexable and
// describe the product as a software tool. index.html is shared by landing and console, so the
// console's noindex lives in firebase.json headers (firebaseRobotsHeaders.test.ts), not here.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect } from "vitest";

// Same rule as the #4457 wording guard (websiteCopy.test.ts).
const FORBIDDEN = /asset[ -]management|profitab\w*|\bsafe\b|\bsicher\b|rendite\w*/gi;

describe("index.html SEO (#4459)", () => {
  // vitest runs from the repo root; import.meta.url is not file-schemed under the transform.
  const raw = readFileSync(resolve(process.cwd(), "index.html"), "utf8");
  const html = raw.replace(/<!--[\s\S]*?-->/g, "");

  const meta = (attr: "name" | "property", key: string): string => {
    const tag = html.match(new RegExp(`<meta\\s+${attr}="${key}"\\s+content="([^"]*)"`, "i"));
    expect(tag, `<meta ${attr}="${key}"> missing`).toBeTruthy();
    return tag![1];
  };

  it("carries no robots noindex outside comments", () => {
    expect(html).not.toMatch(/noindex/i);
  });

  it("JSON-LD parses and contains a SoftwareApplication (FinanceApplication, no investment advice)", () => {
    const block = html.match(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/i);
    expect(block, "JSON-LD block missing").toBeTruthy();
    const data = JSON.parse(block![1]);
    const nodes: Array<Record<string, unknown>> = data["@graph"] ?? [data];
    const org = nodes.find((n) => n["@type"] === "Organization");
    expect(org, "Organization node missing").toBeTruthy();
    expect((org!.address as Record<string, unknown>)?.addressCountry).toBe("DE");
    const app = nodes.find((n) => n["@type"] === "SoftwareApplication");
    expect(app, "SoftwareApplication node missing").toBeTruthy();
    expect(app!.applicationCategory).toBe("FinanceApplication");
    expect(app!.isAccessibleForFree).toBe(true);
    expect(String(app!.license)).toMatch(/apache-2\.0|apache\.org\/licenses\/LICENSE-2\.0/i);
    expect(String(app!.description)).toMatch(/no investment advice/i);
    expect(app!.codeRepository).toBe("https://github.com/Autonomous-Asset-Management-Agents/autonomous_");
    expect(app!.downloadUrl).toBe("https://github.com/Autonomous-Asset-Management-Agents/autonomous_/releases");
    expect(app!.softwareHelp).toBe("https://autonomous-trading.de/llms.txt");
  });

  it("links /llms.txt in <head> and provides a <noscript> summary for non-JS crawlers", () => {
    expect(html).toMatch(/<link\s+rel="llms-txt"\s+href="https:\/\/autonomous-trading\.de\/llms\.txt"\s*\/?>/i);
    const noscript = html.match(/<noscript>([\s\S]*?)<\/noscript>/i)?.[1] ?? "";
    expect(noscript).toMatch(/autonomous_ trading \(AAAgents\)/);
    expect(noscript).toMatch(/execution framework/i);
    expect(noscript).toMatch(/\b14 agents\b/);
    expect(noscript).toMatch(/\b8 of them cast a directional vote\b/);
    expect(noscript).not.toMatch(/alpaca/i);
    expect(noscript).toMatch(/brokerage account/i);
    expect(noscript).toMatch(/paper mode/i);
    expect(noscript).toMatch(/hard risk limits/i);
    expect(noscript).toMatch(/kill-switch/i);
    expect(noscript).toMatch(/no investment advice/i);
    expect(noscript).toMatch(/https:\/\/autonomous-trading\.de\/llms\.txt/);
    expect(noscript).toMatch(/https:\/\/github\.com\/Autonomous-Asset-Management-Agents\/autonomous_/);
  });

  it("title, description, og:* and twitter:* name an execution framework without forbidden terms", () => {
    const title = html.match(/<title>([^<]*)<\/title>/i)?.[1] ?? "";
    const texts = [
      title,
      meta("name", "description"),
      meta("property", "og:title"),
      meta("property", "og:description"),
      meta("property", "og:image:alt"),
      meta("name", "twitter:title"),
      meta("name", "twitter:description"),
    ];
    expect(texts.flatMap((t) => t.match(FORBIDDEN) ?? [])).toEqual([]);
    expect(texts.join(" ")).not.toMatch(/autonomous (ai )?trading/i);
    expect(meta("name", "description")).toMatch(/execution framework/i);
    expect(meta("property", "og:description")).toMatch(/execution framework/i);
    expect(meta("name", "twitter:description")).toMatch(/execution framework/i);
  });
});
