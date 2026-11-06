import { describe, expect, it } from "vitest";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

/**
 * #3792 — Verdrahtungs-Wächter für die Werkzeug-Tests.
 *
 * Die 12 gedrifteten Tests waren nicht das Problem, sondern sein Symptom: `scripts/tests/`
 * lief in keinem Workflow. Ein Test, den niemand ausführt, ist eine Behauptung.
 *
 * Dieser Wächter prüft deshalb nicht, ob die Tests grün sind — das tut die CI selbst —,
 * sondern ob die CI sie überhaupt anfasst.
 */

const repoRoot = join(__dirname, "..", "..");
const read = (p: string) => readFileSync(join(repoRoot, p), "utf-8");

const ci = read(".github/workflows/ci.yml");

describe("#3792 scripts/tests in der CI", () => {
  it("es gibt einen Job, der scripts/tests ausführt", () => {
    expect(ci).toContain("tooling-tests:");
    expect(ci).toMatch(/pytest scripts\/tests\//);
  });

  it("der Job wird von Änderungen an scripts/ ausgelöst", () => {
    // Ohne den Pfadfilter liefe er nie — und die Lücke wäre dieselbe wie vorher,
    // nur mit einem Job, der danebensteht.
    expect(ci).toMatch(/tooling:\s*\n\s*- 'scripts\/\*\*'/);
    expect(ci).toContain("tooling: ${{ steps.f.outputs.tooling }}");
    expect(ci).toContain("needs.filter.outputs.tooling == 'true'");
  });

  it("auch eine Änderung an den Workflows löst ihn aus", () => {
    // Wer ci.yml ändert, kann aut9_flow genauso brechen wie wer scripts/ ändert.
    expect(ci).toMatch(/tooling:[\s\S]{0,200}?- '\.github\/workflows\/\*\*'/);
  });

  it("der Job braucht kein PYTHONPATH — der conftest löst das", () => {
    // Stünde hier wieder eine Variable, wäre der conftest kaputt und niemand merkte es:
    // Der Lauf wäre grün, und ein Einzelaufruf der Datei weiterhin rot.
    expect(existsSync(join(repoRoot, "scripts/tests/conftest.py"))).toBe(true);
    const job = ci.slice(ci.indexOf("tooling-tests:"), ci.indexOf("frontend-build:"));
    // Die Zuweisung, nicht das Wort: der Kommentar im Job darf es nennen.
    expect(job).not.toMatch(/PYTHONPATH\s*[:=]/);
  });

  it("der Job zieht keine schweren Abhängigkeiten", () => {
    // Die Tests benutzen json und unittest.mock. Ein PyTorch-Image dafür wäre absurd,
    // und die Laufzeit entschiede darüber, ob jemand den Check ernst nimmt.
    const job = ci.slice(ci.indexOf("tooling-tests:"), ci.indexOf("frontend-build:"));
    expect(job).not.toContain("install-heavy-deps");
    expect(job).not.toMatch(/container:/);
    expect(job).toMatch(/pip install --quiet pytest pyyaml/);
  });

  it("die Grenzen des Makers werden dadurch mitgeprüft", () => {
    // Der eigentliche Gewinn: test_maker_plan.py beweist die Obergrenze von zwölf
    // Sub-Issues und das Governance-Verbot. Ohne diesen Job beweist es das nur lokal.
    expect(existsSync(join(repoRoot, "scripts/tests/test_maker_plan.py"))).toBe(true);
    expect(existsSync(join(repoRoot, "scripts/tests/test_maker_apply.py"))).toBe(true);
  });
});
