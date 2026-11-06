// #4869 (#4581 S1, ADR-026 Weg D): the browser runs no measurement code. The website bundle is
// built from src/, so no non-test source may call the Umami ingest endpoint or import the removed
// tracking client. Counting happens in the `edge` function from the request itself.
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const ROOT = resolve(process.cwd(), "src");

const sources = (dir: string): string[] =>
  readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) return name === "__tests__" || name === "test" ? [] : sources(p);
    return /\.(ts|tsx)$/.test(name) && !/\.(test|spec)\.tsx?$/.test(name) ? [p] : [];
  });

describe("no measurement code in the browser (#4869)", () => {
  it("the tracking client src/lib/analytics.ts is gone", () => {
    expect(existsSync(join(ROOT, "lib", "analytics.ts"))).toBe(false);
  });

  it("no website source posts to /api/send or imports the tracking client", () => {
    const offenders = sources(ROOT).filter((p) => {
      const text = readFileSync(p, "utf8");
      return text.includes("/api/send") || /from ["']@\/lib\/analytics["']/.test(text);
    });
    expect(offenders.map((p) => relative(ROOT, p))).toEqual([]);
  });

  it("the experiments module does not read window.location", () => {
    const text = readFileSync(join(ROOT, "lib", "experiments.ts"), "utf8");
    expect(text).not.toMatch(/window\.location|location\.search/);
  });
});
