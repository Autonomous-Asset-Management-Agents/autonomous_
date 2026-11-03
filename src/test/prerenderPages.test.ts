// Prerender for crawlers without JavaScript (GPTBot, ClaudeBot, PerplexityBot, CCBot): they only
// see the empty SPA shell. scripts/prerender-pages.mjs renders every sitemap page, the `edge`
// function puts the markup into #root (functions/test/edge.test.mjs), and the browser hides it
// until React mounts. These tests pin the script's pure parts and the wiring between the pieces.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { MIN_TEXT_CHARS, checkSnapshot, routesFromSitemap } from "../../scripts/prerender-pages.mjs";

const read = (rel: string) => readFileSync(rel, "utf8");

describe("prerender — pages and checks", () => {
  it("takes every page of the public sitemap, the root included", () => {
    const routes = routesFromSitemap(read("public/sitemap.xml"));
    expect(routes).toContain("/");
    expect(routes).toContain("/legal/imprint");
    expect(routes.every((r: string) => r.startsWith("/"))).toBe(true);
    expect(new Set(routes).size).toBe(routes.length);
  });

  it("keeps the markup without scripts", () => {
    const text = "x".repeat(MIN_TEXT_CHARS);
    const markup = checkSnapshot("/", { html: `<h1>${text}</h1><script>alert(1)</script>`, text });
    expect(markup).toBe(`<h1>${text}</h1>`);
  });

  it("fails a page that rendered too little text", () => {
    expect(() => checkSnapshot("/appliance", { html: "<div></div>", text: "  Loading…  " })).toThrow(/\/appliance.*characters/);
  });
});

describe("prerender — wiring", () => {
  it("index.html keeps the empty #root and hides prerendered markup", () => {
    const html = read("index.html");
    expect(html).toContain('<div id="root"></div>');
    expect(html).toContain("<style>#root[data-prerendered]{visibility:hidden}</style>");
  });

  it("main.tsx empties and reveals the root before React mounts", () => {
    const main = read("src/main.tsx");
    const clear = main.indexOf('root.removeAttribute("data-prerendered")');
    expect(main).toContain("root.replaceChildren()");
    expect(clear).toBeGreaterThan(-1);
    expect(clear).toBeLessThan(main.indexOf("createRoot(root).render"));
  });

  it("the deploy prerenders after the build and before the edge function is packaged", () => {
    const wf = read(".github/workflows/deploy-demo.yml");
    const build = wf.indexOf("run: npm run build");
    const chromium = wf.indexOf("run: npx playwright install --with-deps chromium");
    const prerender = wf.indexOf("run: node scripts/prerender-pages.mjs");
    const pack = wf.indexOf("run: npm run package:edge");
    expect(build).toBeGreaterThan(-1);
    expect(chromium).toBeGreaterThan(build);
    expect(prerender).toBeGreaterThan(chromium);
    expect(pack).toBeGreaterThan(prerender);
    expect(wf).toContain('- "scripts/prerender-pages.mjs"');
  });

  it("Hosting does not upload the prerender output (dot-directories are ignored)", () => {
    const hosting = JSON.parse(read("firebase.json")).hosting.find((h: { target: string }) => h.target === "demo");
    expect(hosting.ignore).toContain("**/.*");
  });
});
