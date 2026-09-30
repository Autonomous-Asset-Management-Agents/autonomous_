// C-D — on a fresh install the empty console must read as "setting up", not "broken":
// a reassurance banner when there's no data yet, and tooltips that explain the "—" placeholders.
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const dir = path.dirname(fileURLToPath(import.meta.url));
const read = (p: string) => readFileSync(path.join(dir, "..", p), "utf8");

describe("C-D: console empty states read as 'setting up', not 'broken'", () => {
  it("Overview shows a fresh-state reassurance when there is no portfolio data yet", () => {
    const o = read("console/desktop/pages/Overview.tsx");
    expect(o).toMatch(/currentEquity == null &&/);
    expect(o).toMatch(/Setting up/);
    expect(o).toMatch(/not an error/); // reassures that blank "—" values are expected, not broken
  });

  it("the Sidebar status rows explain the '—' placeholder via a tooltip", () => {
    const s = read("console/desktop/Sidebar.tsx");
    expect(s).toMatch(/title\?: string/); // StatRow accepts a tooltip
    expect(s).toMatch(/No specialist agents are active yet/);
    expect(s).toMatch(/Waiting for the engine/);
  });
});
