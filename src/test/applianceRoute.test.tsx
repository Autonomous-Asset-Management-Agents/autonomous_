// Appliance route — GO-LIVE state (tower launch PR): the page is publicly routed and
// deliberately linked from the landing. This inverts the pre-launch gating test (the
// #2794 dev-preview pattern) that previously forbade any /appliance link in the tree.
import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { resolve, join } from "node:path";

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (/\.(tsx|ts)$/.test(name)) out.push(p);
  }
  return out;
}

describe("appliance route (go-live: public + linked)", () => {
  it("route registered unconditionally (no DEV gate)", () => {
    const app = readFileSync(resolve(process.cwd(), "src/App.tsx"), "utf8");
    expect(app).toMatch(/<Route path="\/appliance" element=/);
    expect(app).not.toMatch(/import\.meta\.env\.DEV[\s\S]{0,200}path="\/appliance"/);
  });

  it("the landing links to /appliance (ticker + editions card)", () => {
    const landing = readFileSync(
      resolve(process.cwd(), "src/components/views/LandingViewE.tsx"),
      "utf8",
    );
    const links = landing.match(/href="\/appliance"/g) ?? [];
    expect(links.length, "ticker + editions card both deep-link the tower page").toBeGreaterThanOrEqual(2);
  });

  it("no console surface links /appliance (marketing-only entry points)", () => {
    const offenders: string[] = [];
    for (const f of walk(resolve(process.cwd(), "src/console"))) {
      const src = readFileSync(f, "utf8");
      if (/(to|href)=["']\/appliance/.test(src)) offenders.push(f);
    }
    expect(offenders).toEqual([]);
  });
});
