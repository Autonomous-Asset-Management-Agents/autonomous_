// #4459: the landing became indexable, so the noindex for the console and the credential-entry
// paths moved from the shared index.html into X-Robots-Tag headers per hosting target.
// Severity High: a gap here makes /console or /login searchable.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect } from "vitest";

type Header = { key: string; value: string };
type Target = { target: string; headers?: Array<{ source: string; headers: Header[] }> };

const hosting: Target[] = JSON.parse(
  readFileSync(resolve(process.cwd(), "firebase.json"), "utf8"),
).hosting;

const robotsFor = (target: string, source: string): string | undefined =>
  hosting
    .find((t) => t.target === target)
    ?.headers?.find((h) => h.source === source)
    ?.headers.find((h) => h.key.toLowerCase() === "x-robots-tag")?.value;

describe("firebase.json X-Robots-Tag (#4459)", () => {
  it('hosting target "console" is noindex, nofollow on every path', () => {
    expect(robotsFor("console", "**")).toBe("noindex, nofollow");
  });

  for (const source of ["/login", "/auth/**", "/console", "/console/**"]) {
    it(`hosting target "demo": ${source} is noindex, nofollow`, () => {
      expect(robotsFor("demo", source)).toBe("noindex, nofollow");
    });
  }

  it('hosting target "demo" does not noindex the landing', () => {
    expect(robotsFor("demo", "**")).toBeUndefined();
    expect(robotsFor("demo", "/")).toBeUndefined();
  });
});

// #4467 (ADR-026 §6): the Google Analytics hosts left the landing's CSP.
// #4870 (#4581 S2, ADR-026 Weg D): the browser no longer talks to Umami at all — the `edge`
// function reports from the server — so the analytics host leaves connect-src as well.
const ANALYTICS_HOST = "https://analytics.aaagents.de";

const cspDirective = (target: string, directive: string): string[] => {
  const csp = hosting
    .find((t) => t.target === target)
    ?.headers?.find((h) => h.source === "**")
    ?.headers.find((h) => h.key.toLowerCase() === "content-security-policy")?.value;
  const entry = csp
    ?.split(";")
    .map((d) => d.trim().split(/\s+/))
    .find(([name]) => name === directive);
  return entry ? entry.slice(1) : [];
};

describe("firebase.json CSP for the landing analytics (#4467)", () => {
  it('#4870: hosting target "demo" no longer lets the browser reach the analytics host', () => {
    expect(cspDirective("demo", "connect-src")).not.toContain(ANALYTICS_HOST);
  });

  it('hosting target "demo" does not allow scripts from the analytics host', () => {
    expect(cspDirective("demo", "script-src")).not.toContain(ANALYTICS_HOST);
  });

  for (const directive of ["script-src", "connect-src"]) {
    it(`hosting target "demo": ${directive} has no Google Analytics / Tag Manager host`, () => {
      const hosts = cspDirective("demo", directive);
      expect(hosts.length).toBeGreaterThan(0);
      expect(hosts.filter((h) => /googletagmanager\.com|google-analytics\.com/.test(h))).toEqual([]);
    });
  }
});
