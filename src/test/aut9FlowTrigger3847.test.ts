import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

/**
 * #3847 — Verdrahtungs-Wächter für den Auslöser der Zustandsmaschine.
 *
 * `scripts/tests/test_aut9_flow.py` prüft die Entscheidung. Dieser Test prüft, dass der
 * Workflow sie überhaupt füttert: Fehlt `labeled` in den Ereignistypen oder `PR_LABEL` in
 * der Umgebung, feuert die Freigabe-Kante **nie** — und zwar still. Genau diese Bauart hat
 * sieben Tage gekostet (#3842).
 */

const repoRoot = join(__dirname, "..", "..");
const read = (p: string) => readFileSync(join(repoRoot, p), "utf-8");

const wf = read(".github/workflows/aut9-flow.yml");
const flow = read("scripts/aut9_flow.py");

describe("#3847 Auslöser der AUT-9-Zustandsmaschine", () => {
  it("lauscht auf das Setzen eines Labels", () => {
    // Owner 08.10.2026: `synchronize` dazu - neue Commits nach einer Nacharbeit fragen
    // das Review erneut an und setzen code-review-requested.
    expect(wf).toMatch(/types:\s*\[opened, reopened, labeled, synchronize\]/);
  });

  it("gibt den Autor des Reviews weiter (nur der Reviewer entfernt das Label)", () => {
    expect(wf).toMatch(/REVIEW_AUTHOR:\s*\$\{\{\s*github\.event\.review\.user\.login\s*\}\}/);
  });

  it("reicht den Label-Namen an das Skript weiter", () => {
    // Ohne diese Zeile liest main() einen leeren String, `plan-approved` trifft nie zu,
    // und der Lauf meldet trotzdem Erfolg.
    expect(wf).toContain("PR_LABEL: ${{ github.event.label.name }}");
    expect(flow).toContain('os.environ.get("PR_LABEL"');
  });

  it("die Freigabe schaltet, andere Labels nicht", () => {
    expect(flow).toContain('FREIGABE_LABEL = "plan-approved"');
    expect(flow).toMatch(/if event_name == "labeled":/);
  });

  it("ein Plan-PR wird am Dateisatz erkannt, nicht am Label", () => {
    // Das Label `plan-review-requested` setzt auto-label-plans.yml erst NACH dem Öffnen —
    // eine Erkennung daran hinge vom Wettlauf der Workflows ab.
    expect(flow).toContain("def ist_plan_pr(");
    expect(flow).toContain("api.list_pr_files(repo, pr_number)");
    expect(flow).not.toMatch(/ist_plan_pr\([^)]*plan-review-requested/);
  });

  it("die Erkennung läuft an der opened- UND an der Review-Kante (#3902)", () => {
    // #3847 kannte zwei Kanten: „PR geöffnet" und „Label gesetzt". Die dritte fehlte —
    // „jemand approved den PR". Für `review_submitted` blieb `plan_pr` auf False, und
    // der Übergang nach „Validating (Review)" lief durch, als wäre Code geprüft worden.
    //
    // Gemessen am 01.10. (Lauf 36867524211): `event=pull_request_review plan_pr=False`,
    // obwohl PR #3898 genau eine Datei `…/implementation_plan.md` enthielt. Folge: elf
    // von zwölf FAB-1-Sub-Issues standen in „Validating (Review)" ohne eine Zeile Code.
    //
    // Bei `labeled` bleibt es dabei: Die Freigabe ist dort selbst das Signal, ein
    // zusätzlicher API-Aufruf wäre Aufwand ohne Aussage.
    // Owner 08.10.2026: auch `synchronize` - an der Nacharbeits-Kante entscheidet der
    // Dateisatz, ob das Code-Label gesetzt wird. `labeled` bleibt draussen.
    expect(flow).toMatch(
      /ev in \("opened", "reopened", "review_submitted", "synchronize"\)\s*and pr_number\s*and repo/,
    );
    expect(flow).not.toMatch(/ev in \([^)]*"labeled"[^)]*\)\s*and pr_number\s*and repo/);
  });

  it("die beiden Kanten wirken entgegengesetzt (#3902)", () => {
    // An `opened` UNTERDRÜCKT ein Plan-PR den Übergang (der Plan ist ungeprüft);
    // an `review_submitted` KEHRT er ihn um — ein approvter Plan-PR ist eine
    // Planfreigabe, kein Code-Review.
    expect(flow).toMatch(/if plan_pr and ev in \("opened", "reopened"\):/);
    expect(flow).toContain("target = None");
    expect(flow).toMatch(/return "Implementing \(Code\)" if plan_pr else "Validating \(Review\)"/);
  });

  it("die Entscheidungszeile nennt Label und Plan-PR", () => {
    // Das Protokoll ist hier die Spur (Plan §3b, statt OTel) — also muss daraus
    // hervorgehen, WARUM geschaltet wurde oder nicht.
    expect(flow).toMatch(/label=\{label!r\}/);
    expect(flow).toMatch(/plan_pr=\{plan_pr\}/);
  });
});
