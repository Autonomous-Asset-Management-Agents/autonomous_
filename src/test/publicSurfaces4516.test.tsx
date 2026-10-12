// #4516 (Epic #4450): public leftover surfaces name only verifiable facts — test routes are
// DEV-only, llms.txt and the FAQ state the documented agent count (14 agents, 8 directional
// voters; docs/2_agentic_operations/AGENT_CATALOG.md), every sitemap URL has a route, and the
// og:image meta tags match the real image.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, it, expect, vi } from "vitest";
import { FORBIDDEN, withoutLinkTargets } from "./wordingGuard";

// Routing is tested as pure route→element mapping (same harness as consoleRouteSwap.test.tsx).
vi.mock("@/components/PrivateRoute", () => ({
  PrivateRoute: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock("firebase/auth", () => ({
  signOut: vi.fn(),
  getAuth: vi.fn(),
  onAuthStateChanged: vi.fn(() => vi.fn()),
  GoogleAuthProvider: vi.fn(),
}));
vi.mock("@/lib/firebase", () => ({ trackVariantImpression: vi.fn() }));
vi.mock("@/hooks/useDesignVariant", () => ({
  useDesignVariant: () => ({ variant: "v1" }),
  DesignVariant: {},
}));
vi.mock("@/lib/editor/useEditMode", () => ({
  useEditMode: () => ({ active: false, user: null }),
}));
vi.mock("@/pages/NotFound", () => ({ default: () => <div>NOTFOUND-MARKER</div> }));
vi.mock("@/components/views/LandingViewE", () => ({ default: () => <div>LANDING-E-MARKER</div> }));
vi.mock("@/pages/PricingPreview", () => ({ default: () => <div>DEV-ROUTE-MARKER</div> }));
vi.mock("@/pages/PricingMaster", () => ({ default: () => <div>DEV-ROUTE-MARKER</div> }));
vi.mock("@/console/desktop/LiveConsentPreview", () => ({ default: () => <div>DEV-ROUTE-MARKER</div> }));
vi.mock("@/console/desktop/pages/OverviewPreview", () => ({ default: () => <div>DEV-ROUTE-MARKER</div> }));
vi.mock("@/console/desktop/pages/SettingsPreview", () => ({ default: () => <div>DEV-ROUTE-MARKER</div> }));
vi.mock("@/components/BenchmarkEquityChart", () => ({ BenchmarkEquityChart: () => <div /> }));

import { AppContent } from "../App";
import Performance from "../pages/Performance";

// vitest runs from the repo root; import.meta.url is not file-schemed under the transform.
const read = (...p: string[]) => readFileSync(resolve(process.cwd(), ...p), "utf8");

const DEV_ROUTES = ["/pricing-preview", "/test-pricing", "/live-consent-preview", "/overview-preview", "/settings-preview"];

describe("llms.txt and FAQ name the documented agent count (#4516)", () => {
  const OTHER_COUNT = /\bnine\b|\b9 specialized\b|\b9 (voting )?agents\b/i;

  it("llms.txt names 14 agents, 8 of them voting, and no other count", () => {
    const t = read("public", "llms.txt");
    expect(t).toMatch(/\b14 agents\b/);
    expect(t).toMatch(/\b8 of them cast a directional vote\b/);
    expect(t).not.toMatch(OTHER_COUNT);
    expect(t).toMatch(/Alpaca/);
    expect(t).toMatch(/installer/i);
  });

  it("FAQ names 14/8, the installer as available, and links no private repo", () => {
    const t = read("docs", "oss", "FAQ.md");
    expect(t).toMatch(/\b14 agents\b/);
    expect(t).toMatch(/\b8 of them cast a directional vote\b/);
    expect(t).not.toMatch(OTHER_COUNT);
    expect(t).toMatch(/installer is available/i);
    expect(t).not.toMatch(/is coming/i);
    expect(t).not.toMatch(/github\.com\/Autonomous-Asset-Management-Agents\/Dev-Enviroment/);
  });
});

describe("sitemap.xml matches the routes in App.tsx (#4516)", () => {
  const app = read("src", "App.tsx");
  // One segment per <Route …> (some span several lines, e.g. "/"), up to the next <Route or </Routes>.
  const routeDecls = app
    .slice(app.indexOf("<Routes>"), app.indexOf("</Routes>"))
    .split(/(?=<Route\s)/)
    .filter((s) => /^<Route\s/.test(s));
  const routes = routeDecls.map((s) => s.match(/path="([^"]+)"/)![1]);
  const sitemap = read("public", "sitemap.xml");
  const locs = [...sitemap.matchAll(/<loc>https:\/\/aaagents\.de([^<]*)<\/loc>/g)].map((m) => m[1] || "/");

  it("parses routes and sitemap entries (self-check)", () => {
    expect(routes).toEqual(expect.arrayContaining(["/", "/support", "*"]));
    expect(locs).toContain("/");
  });

  it("every <loc> path has a route that is not the catch-all", () => {
    expect(locs.filter((p) => !routes.includes(p) || p === "*")).toEqual([]);
  });

  it("every public route is in the sitemap", () => {
    const NOT_PUBLIC = /^\/login$|^\/auth\/|^\/console(\/|$)|^\/dashboard$|^\/offline$|^\*$/;
    // /preview stays registered for E2E and the header link, but is noindex (firebase.json).
    const NOINDEX = ["/preview"];
    const publicRoutes = routeDecls
      .filter((s) => !/PrivateRoute/.test(s))
      .map((s) => s.match(/path="([^"]+)"/)![1])
      .filter((p) => !NOT_PUBLIC.test(p) && !DEV_ROUTES.includes(p) && !NOINDEX.includes(p));
    expect(publicRoutes.length).toBeGreaterThan(5);
    expect(publicRoutes.filter((p) => !locs.includes(p))).toEqual([]);
  });
});

describe("index.html og:image and description (#4516)", () => {
  const html = read("index.html");
  const meta = (key: string) => html.match(new RegExp(`<meta\\s+property="${key}"\\s+content="([^"]*)"`, "i"))?.[1];

  it("og:image:width/height equal the IHDR size of public/og-image.png", () => {
    const png = readFileSync(resolve(process.cwd(), "public", "og-image.png"));
    expect(meta("og:image:width")).toBe(String(png.readUInt32BE(16)));
    expect(meta("og:image:height")).toBe(String(png.readUInt32BE(20)));
  });

  it("og:description does not point to DISCLAIMER.md", () => {
    expect(meta("og:description")).toBeTruthy();
    expect(meta("og:description")).not.toMatch(/DISCLAIMER\.md/);
  });
});

describe("wording guard over the public text surfaces (#4516)", () => {
  it.each([
    ["public/llms.txt", ["public", "llms.txt"]],
    ["docs/oss/FAQ.md", ["docs", "oss", "FAQ.md"]],
    ["index.html", ["index.html"]],
  ])("%s has no forbidden term (link targets excluded)", (_name, path) => {
    expect(withoutLinkTargets(read(...path)).match(FORBIDDEN) ?? []).toEqual([]);
  });
});

describe("test routes are DEV-only, /preview stays (#4516)", () => {
  afterEach(() => vi.unstubAllEnvs());

  const at = (path: string) =>
    render(
      <MemoryRouter initialEntries={[path]}>
        <AppContent />
      </MemoryRouter>,
    );

  it.each(DEV_ROUTES)("production build: %s renders NotFound", async (path) => {
    vi.stubEnv("DEV", false);
    at(path);
    expect(await screen.findByText("NOTFOUND-MARKER")).toBeTruthy();
    expect(screen.queryByText("DEV-ROUTE-MARKER")).toBeNull();
  });

  it.each(DEV_ROUTES)("dev server: %s renders its preview", async (path) => {
    vi.stubEnv("DEV", true);
    at(path);
    expect(await screen.findByText("DEV-ROUTE-MARKER")).toBeTruthy();
    expect(screen.queryByText("NOTFOUND-MARKER")).toBeNull();
  });

  it.each([false, true])("/preview renders LandingViewE (DEV=%s)", async (dev) => {
    vi.stubEnv("DEV", dev);
    at("/preview");
    expect(await screen.findByText("LANDING-E-MARKER")).toBeTruthy();
  });
});

describe("/performance links no dead target (#4516)", () => {
  it("has no link to /#waitlist", () => {
    const { container } = render(
      <MemoryRouter>
        <Performance />
      </MemoryRouter>,
    );
    expect(container.querySelector('a[href="/#waitlist"]')).toBeNull();
    expect(screen.queryByText(/join waitlist/i)).toBeNull();
  });
});
