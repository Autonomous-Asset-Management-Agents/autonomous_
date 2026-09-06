import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

// Brand-green single-source-of-truth guard. The app's ONE green is the `autonomous_`
// logo underscore (#00c27a — favicon.svg + TitleBar.tsx). #1983's styling wave had
// scattered 5+ near-duplicate greens (#5be584 positive text, #30d158 live dot / sparkline,
// #7ce7b3 mint, #b6f1c5 bull button, #1d8d3f vote bar, rgba(48,209,88,…) tints), so the
// "green" drifted per surface. They were unified onto the console.css `--brand-green` token
// / the master literal. This guard fails if any off-brand green creeps back into the console.
// (Intentionally still allowed: the master #00c27a, its brighter #00d687 button-hover shade,
// and the engine-status ampel #28c840 / #febc2e, which are functional status colors.)

const consoleDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "console");

// Banned off-brand greens: the old hex shades + the old rgb(48,209,88) tint triple.
const BANNED: RegExp[] = [
  /#5be584/i,
  /#30d158/i,
  /#7ce7b3/i,
  /#b6f1c5/i,
  /#1d8d3f/i,
  /#00d886/i,
  /rgba\(\s*48\s*,\s*209\s*,\s*88/i,
];

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const p = path.join(dir, name);
    if (statSync(p).isDirectory()) out.push(...walk(p));
    else if (/\.(tsx?|css)$/.test(name)) out.push(p);
  }
  return out;
}

const files = walk(consoleDir);

describe("brand green is one unified tone (#00c27a, the logo underscore)", () => {
  it("console.css defines the --brand-green token = #00c27a", () => {
    const css = readFileSync(path.join(consoleDir, "console.css"), "utf8");
    expect(css).toMatch(/--brand-green:\s*#00c27a/);
    expect(css).toMatch(/--brand-green-rgb:\s*0,\s*194,\s*122/);
  });

  it("no off-brand green literals remain anywhere in the console", () => {
    const offenders: string[] = [];
    for (const f of files) {
      const src = readFileSync(f, "utf8");
      for (const re of BANNED) {
        if (re.test(src)) offenders.push(`${path.relative(consoleDir, f)} :: ${re}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});
