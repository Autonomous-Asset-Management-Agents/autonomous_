/**
 * UXC-1 S8 (#3177): design-guard — static sweep enforcing the "shape follows
 * criticality" control grammar (owner decision 03.09.2026) and the guide rules
 * it rests on (SYSTEM_DESIGN_GUIDE §246–252, §266–271, §284).
 *
 * Each test lists the files that still carry a forbidden pattern, so a failure
 * names the offender directly. Scope: src/console only — landing pages and the
 * sanctioned passive .live-dot are exempt.
 */
import { describe, it, expect } from "vitest";
import * as fs from "node:fs";
import * as path from "node:path";

const ROOT = path.resolve(__dirname, "../console");

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walk(p));
    else out.push(p);
  }
  return out;
}

const ALL_FILES = walk(ROOT).filter((f) => /\.(tsx|ts|css)$/.test(f));

function offenders(re: RegExp, ext: RegExp = /\.tsx$/): string[] {
  const hits: string[] = [];
  for (const f of ALL_FILES.filter((x) => ext.test(x))) {
    const lines = fs.readFileSync(f, "utf8").split("\n");
    lines.forEach((line, i) => {
      if (re.test(line)) hits.push(`${path.relative(ROOT, f)}:${i + 1}`);
    });
  }
  return hits;
}

describe("design guard — shape follows criticality (UXC-1 S8)", () => {
  it("G1: no black text on filled controls (§249: button text is always white)", () => {
    expect(offenders(/text-black/)).toEqual([]);
  });

  it("G2: no off-registry accent colors (only #ff5a52 interaction red, #00c27a/#00d687 green)", () => {
    expect(offenders(/#c0261f|#d62d25|#ff6b61|#11d089/i, /\.(tsx|css)$/)).toEqual([]);
  });

  it("G3: no translucent-green selected state (green tint + green text = the rejected glow look, §252)", () => {
    expect(
      offenders(
        /text-\[#00c27a\][^"'`]*bg-\[#00c27a\]\/|bg-\[#00c27a\]\/[^"'`]*text-\[#00c27a\]/,
      ),
    ).toEqual([]);
  });

  it("G4: no iOS-style Switch remains in the console (replaced by OnOffSegment)", () => {
    expect(offenders(/components\/ui\/switch/, /\.(tsx|ts)$/)).toEqual([]);
  });

  it("G5: no accent glow halo on interactive elements (§252; flat StatusDot is the marker)", () => {
    expect(offenders(/0 0 0 3px rgba\(0, ?194, ?122/)).toEqual([]);
  });

  it("G6: the legacy grey CAPS pill recipe is retired (replaced by the .btn family)", () => {
    expect(offenders(/rounded-full[^"'`]*text-white\/70 bg-white\/\[0\.08\]/)).toEqual([]);
  });

  it("G7: error surfaces use interaction red, not the data-red tint recipe (§266–271)", () => {
    expect(offenders(/\[#ff453a\]\/(10|25)/)).toEqual([]);
  });

  it("G8: the dead gradient glow classes are gone from the stylesheet", () => {
    expect(offenders(/btn-bull|btn-bear/, /console\.css$/)).toEqual([]);
  });
});
