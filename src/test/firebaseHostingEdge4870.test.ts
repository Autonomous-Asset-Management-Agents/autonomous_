// #4870 (#4581 S2, ADR-026 Weg D): the public landing is served through the `edge` function.
// Firebase Hosting serves an existing file before any rewrite, so the build renames
// dist/index.html to dist/app.html (functions/package-generated.mjs) and `/` and every SPA path
// fall through to the rewrite. The console target keeps its own build with index.html.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect } from "vitest";

type Rewrite = { source: string; destination?: string; function?: { functionId: string; region: string } };
type Target = { target: string; rewrites?: Rewrite[]; headers?: Array<{ source: string; headers: { key: string; value: string }[] }> };

const hosting: Target[] = JSON.parse(readFileSync(resolve(process.cwd(), "firebase.json"), "utf8")).hosting;
const target = (name: string) => hosting.find((t) => t.target === name)!;

describe("firebase.json rewrites for the edge function (#4870)", () => {
  const rewrites = target("demo").rewrites ?? [];

  it("keeps the waitlist API first", () => {
    expect(rewrites[0]).toEqual({ source: "/api/appliance/**", function: { functionId: "appliance", region: "europe-west1" } });
  });

  it("sends /download/** to edge in europe-west3", () => {
    expect(rewrites.find((r) => r.source === "/download/**")?.function).toEqual({ functionId: "edge", region: "europe-west3" });
  });

  it("sends every other path to edge, as the last rule", () => {
    const last = rewrites[rewrites.length - 1];
    expect(last).toEqual({ source: "**", function: { functionId: "edge", region: "europe-west3" } });
  });

  it("no demo rewrite points at a static index.html any more", () => {
    expect(rewrites.filter((r) => r.destination)).toEqual([]);
  });

  it("the console target is unchanged: its own build, ** → /index.html", () => {
    expect(target("console").rewrites).toEqual([{ source: "**", destination: "/index.html" }]);
  });

  it("the static fallback /app.html is not cached", () => {
    const h = target("demo").headers?.find((x) => x.source === "/app.html");
    expect(h?.headers.find((x) => x.key === "Cache-Control")?.value).toBe("no-cache, no-store, must-revalidate");
  });
});
