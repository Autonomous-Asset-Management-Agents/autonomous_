import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

/**
 * #3785 — Verdrahtungs-Wächter für Maker Stufe 2.
 *
 * `scripts/tests/test_maker_plan.py` prüft die Grenzen, `test_maker_apply.py` die
 * Reihenfolge. Dieser Test prüft, dass der Workflow diese Prüfungen überhaupt benutzt.
 *
 * Der Unterschied zu Stufe 1 ist der Einsatz: Ein Trockenlauf, der falsch verdrahtet
 * ist, erzeugt einen überflüssigen Kommentar. Eine falsch verdrahtete Stufe 2 erzeugt
 * Issues, Branches und Pull Requests, die jemand von Hand wieder einsammelt.
 */

const repoRoot = join(__dirname, "..", "..");
const read = (p: string) => readFileSync(join(repoRoot, p), "utf-8");

const wf = read(".github/workflows/maker.yml");
const plan = read("scripts/maker_plan.py");
const apply = read("scripts/maker_apply.py");
const prompt = read("docs/prompts/MAKER_EPIC_DECOMPOSE.md");

describe("#3785 Maker Stufe 2: Verdrahtung", () => {
  it("prüft den Vorschlag über maker_apply.py, statt im YAML zu entscheiden", () => {
    // Eine Bedingung in einer Workflow-Datei ist nicht testbar — und genau diese
    // Bedingung entscheidet, ob zwölf oder dreißig Issues entstehen.
    expect(wf).toContain("python scripts/maker_apply.py");
    expect(wf).toContain("--epic");
  });

  it("darf schreiben, was es anlegt — und nicht mehr", () => {
    // contents für Branches, issues für Sub-Issues, pull-requests für die Plan-PRs.
    // In Stufe 1 genügte `contents: read`; ohne diese Erweiterung schlüge der Push fehl.
    expect(wf).toMatch(/permissions:\s*\n\s*issues: write\s*\n\s*contents: write\s*\n\s*pull-requests: write/);
  });

  it("erzeugt die Pull Requests mit einem Token, das Workflows auslöst", () => {
    // PRs aus dem Standard-GITHUB_TOKEN lösen KEINE Workflows aus: ai_judge_gate bliebe
    // an jedem Plan-PR stumm, aut9_flow unbeteiligt. Der PR sähe geprüft aus, ohne es
    // zu sein — der gefährlichste der möglichen Fehlschläge, weil er wie Erfolg aussieht.
    const step = wf.slice(wf.indexOf("Zerlegung anlegen"));
    expect(step).toContain("secrets.MAKER_PROJECT_TOKEN");
  });

  it("vertieft die Historie, bevor es einen Branch pusht", () => {
    // actions/checkout holt flach; ein Branch aus flacher Historie lässt sich nicht
    // pushen ("shallow update not allowed"). Der Fehler träte erst NACH dem Anlegen
    // des ersten Sub-Issues auf — also mitten in der Zerlegung.
    const step = wf.slice(wf.indexOf("Zerlegung anlegen"));
    expect(step).toMatch(/git fetch[^\n]*--unshallow/);
    expect(step.indexOf("--unshallow")).toBeLessThan(step.indexOf("python scripts/maker_apply.py"));
  });

  it("sperrt erst NACH dem letzten Pull Request", () => {
    // Vorher wäre ein abgebrochener Lauf als „zerlegt" markiert, und das Epic bliebe
    // mit halber Zerlegung liegen, ohne dass der Cron es noch einmal anfasst.
    const step = wf.slice(wf.indexOf("Zerlegung anlegen"));
    const arbeit = step.indexOf("python scripts/maker_apply.py");
    const sperre = step.indexOf("labels[]=maker:proposed");  // #3810
    expect(arbeit).toBeGreaterThan(-1);
    expect(sperre).toBeGreaterThan(arbeit);
  });

  it("die Abnahme auf Epic-Ebene entsteht vor dem ersten Sub-Issue", () => {
    // Epic-TDD: Wer erst baut und dann die Abnahme schreibt, misst gegen das Gebaute.
    const e2e = apply.indexOf("# 1. Abnahme zuerst");
    const subs = apply.indexOf("# 2. Je Sub-Issue");
    expect(e2e).toBeGreaterThan(-1);
    expect(subs).toBeGreaterThan(e2e);
  });

  it("die Obergrenze steht an einer Stelle und bricht ab, statt zu kürzen", () => {
    // Eine gekürzte Zerlegung sähe aus wie eine vollständige — niemand würde die
    // fehlenden Teile vermissen.
    expect(plan).toContain("MAX_SUB_ISSUES = 12");
    expect(prompt).toMatch(/Höchstens zwölf/);
    expect(plan).toMatch(/erlaubt sind \{max_sub_issues\}/);
  });

  it("Labels werden über die Label-Schnittstelle gesetzt, nicht über gh pr/issue edit (#3810)", () => {
    // `gh pr edit` schlägt für seine eigene Textausgabe `author.login` nach und braucht
    // dafür `read:org`. In Lauf 36702118807 gab es Exit 1, OBWOHL das Label gesetzt war —
    // die Zerlegung brach nach dem ersten von zwölf Sub-Issues ab. Im Workflow säße
    // derselbe Fehler noch einmal, und dort NACH der ganzen Arbeit.
    expect(apply).not.toMatch(/"pr",\s*"edit"/);
    expect(apply).toContain("/labels");
    expect(wf).not.toContain('--add-label "maker:proposed"');
    expect(wf).toContain('labels[]=maker:proposed');
  });

  it("jeder Plan-PR trägt das Review-Label — sonst sieht ihn der Prüfer nie", () => {
    // agy_plan_review.yml sucht genau danach. Ohne Label bleibt der Plan liegen, und
    // das zugehörige Sub-Issue kann nie `plan-approved` werden.
    expect(apply).toContain('"plan-review-requested"');
  });

  it("jeder Branch entsteht aus origin/main — keine gestapelten PRs", () => {
    // Gestapelte PRs lassen sich nicht einzeln beurteilen, und genau das ist der Zweck
    // von einem Plan-PR je Sub-Issue.
    expect(apply).toContain('["git", "checkout", "-B", branch, "origin/main"]');
  });

  it("der Prompt verlangt maschinenlesbares JSON und verbietet das Selbst-Anlegen", () => {
    // Prosa müsste geraten werden, und Raten ist der Schritt, nach dem niemand mehr
    // nachvollziehen kann, warum ein Sub-Issue entstanden ist.
    expect(prompt).toMatch(/ein JSON-Objekt/);
    expect(prompt).toMatch(/keine Issues, keine Branches, keine Pull Requests/);
    expect(plan).toContain("Kein JSON in der Antwort des Makers gefunden.");
  });

  it("die Abnahme ist beim Anlegen rot", () => {
    // Ein Platzhalter, der `pass` sagt, wäre grün und behauptete eine Prüfung, die nie
    // stattgefunden hat. `strict` sorgt dafür, dass der Marker auffällt, sobald die
    // Abnahme hält — pytest meldet XPASS dann als Fehlschlag.
    expect(apply).toContain("raise NotImplementedError(_OFFEN)");
    expect(apply).toContain("strict=True");
  });

  it("eine Governance-Berührung wird gemeldet, nicht abgelehnt (#3797)", () => {
    // Bis #3797 lehnte die Textprüfung jeden Plan ab, der `.github/workflows` auch nur
    // nannte — damit war jede CI-Ratsche unplanbar (getroffen: G-0 von ARC-E6). Der
    // Schutz sitzt strukturell (test_maker_apply.py::TestSchreibgrenzen) und am Code-PR
    // (Governance Guard verlangt `governance-bypass`), nicht an einer Zeichenkette.
    expect(plan).toContain("def governance_beruehrungen(");
    expect(plan).not.toMatch(/beruehrt eine Governance-Datei/);
    expect(apply).toContain("def _governance_hinweis(");
    expect(apply).toContain("governance-bypass");
  });

  it("ein angelegtes Sub-Issue wird sofort vermerkt, nicht erst nach dem PR (#3797)", () => {
    // Scheitert die Verknüpfung oder der Plan-PR, hing sonst ein Sub-Issue im
    // Repository, das der Abbruchkommentar nicht nennt.
    expect(apply).toContain("(Plan-PR fehlt noch)");
  });

  it("ein Teilabbruch meldet, was bereits entstanden ist", () => {
    // Ohne diese Liste müsste jemand das Repository danach absuchen.
    expect(apply).toContain("class Teilabbruch");
    expect(apply).toContain("Bereits entstanden:");
  });
});
