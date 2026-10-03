import { describe, expect, it } from "vitest";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

/**
 * #3760 — Verdrahtungs-Wächter für den Maker-Wecker.
 *
 * `scripts/tests/test_maker_gate.py` prüft die Entscheidung. Dieser Test prüft, dass
 * der Workflow diese Entscheidung überhaupt benutzt und die Eigenschaften hat, ohne
 * die sie wirkungslos wäre — dieselbe Rolle wie `workflowPipefail3687.test.ts`.
 */

const repoRoot = join(__dirname, "..", "..");
const read = (p: string) => readFileSync(join(repoRoot, p), "utf-8");

const wf = read(".github/workflows/maker.yml");
const gate = read("scripts/maker_gate.py");
const lines = wf.split(/\r?\n/);

describe("#3760 Maker-Wecker: Verdrahtung", () => {
  it("wird NICHT durch ein Issue-Label ausgeloest (#3919)", () => {
    // Owner-Korrektur 29.09.2026: Das Label `epic` ist KEIN Auftrag. Es bestimmt nur,
    // dass ein Issue im Portfolio-Board auftaucht. Der Auftrag ist das Ziehen der Karte
    // nach "Analyzing (Plan)" - eine menschliche Handlung (ADR-025).
    //
    // Der Label-Pfad war ein zweiter Auftragsweg: Ein `epic`-Label konnte die Zerlegung
    // starten, OHNE dass je eine Karte gezogen wurde. Zurueckgebaut, nachdem der
    // SOLL-Pfad am 01.10. ueber beide Haelften belegt war - Lauf 36862622000 zerlegte
    // FAB-1 nach dem Kartenzug in 12 Sub-Issues, und #3817 wurde ueber PR #3871 gemergt.
    expect(wf).not.toMatch(/types:\s*\[labeled\]/);
    expect(wf).not.toContain("github.event.label");
    expect(wf).not.toContain("github.event_name == 'issues'");
  });

  it("der Zeitplan liest das Board - die einzige verbliebene Kante", () => {
    // Actions kennt keinen Trigger fuer Projects-v2-Verschiebungen. Deshalb fragt der
    // Cron das Board ab, statt auf ein Ereignis zu warten.
    expect(wf).toMatch(/- cron:/);
    expect(gate).toContain("pick_next_epic");
  });

  it("entscheidet nicht im YAML, sondern über scripts/maker_gate.py", () => {
    // Eine Bedingung in einer Workflow-Datei ist nicht testbar — und genau diese
    // Bedingung entscheidet über Doppelstarts.
    expect(wf).toContain("scripts/maker_gate.py");
  });

  it("serialisiert global und schneidet laufende Läufe nicht ab", () => {
    // #3763: EINE Gruppe für alles. Eine Gruppe je Issue würde einen Cron-Lauf und ein
    // Issue-Ereignis zum selben Epic nebeneinander zulassen. GitHub hält je Gruppe
    // höchstens einen wartenden Lauf — der */5-Cron kann sich damit nicht aufstauen.
    expect(wf).toMatch(/concurrency:\s*\n\s*group:\s*maker\s*\n/);
    expect(wf).toContain("cancel-in-progress: false");
  });

  it("der Cron fragt das Board ab — die Beauftragung ist die Kartenverschiebung", () => {
    expect(wf).toMatch(/schedule:\s*\n\s*- cron:\s*"\*\/5 \* \* \* \*"/);
    // #3919: `github.event_name == 'schedule'` ist entfallen — es gibt kein anderes
    // Ereignis mehr, gegen das man prüfen müsste. Der Zeitplan IST der Auslöser.
    expect(wf).not.toContain("github.event_name");
  });

  it("die Arbeitsschritte nehmen die Issue-Nummer vom Tor, nicht vom Ereignis", () => {
    // Im Cron-Lauf gibt es kein `github.event.issue`. Wer dort darauf zugreift, arbeitet
    // auf einer leeren Nummer — der Fehler sähe aus wie „es ist nichts passiert".
    const gateIdx = wf.indexOf("id: gate");
    expect(gateIdx).toBeGreaterThan(-1);
    const afterGate = wf.slice(gateIdx);
    expect(afterGate).not.toContain("github.event.issue.number");
    expect(afterGate).toContain("steps.gate.outputs.issue");
  });

  it("das Board wird mit einem Token gelesen, das Projects v2 darf", () => {
    // Das Standard-GITHUB_TOKEN kann ProjectV2 nicht lesen. Ohne eigenes Token meldet
    // der Lauf „nichts zu tun", statt den Rechtefehler zu zeigen.
    expect(wf).toContain("secrets.MAKER_PROJECT_TOKEN");
    expect(gate).toContain("read:project");
  });

  it("die Board-Kennungen stehen an genau einer Stelle", () => {
    expect(gate).toContain('PROJECT_ID = "PVT_kwDOD0sTW84BUQUf"');
    expect(gate).toContain('ANALYZING_OPTION_ID = "10a3fc3a"');
    for (const l of ["maker:claimed", "maker:failed", "plan-approved", "plan-review-requested"]) {
      expect(gate, `BLOCKING_LABELS kennt '${l}' nicht`).toContain(`"${l}"`);
    }
  });

  it("der Cron zieht nichts aus Funnel / Backlog", () => {
    // Die Grenze aus dem freigegebenen Plan: nachholen ja, selbst beauftragen nein.
    // Nur die Analyzing-Option darf als Auswahlkriterium auftauchen.
    expect(gate).not.toContain("f75ad846");
  });

  it("die Zeitgrenze des Jobs stimmt mit der Lease-Grenze überein", () => {
    // Läuft die Lease früher ab als der Job, übernimmt ein zweiter Lauf, während der
    // erste noch arbeitet. Diese Kopplung ist die eigentliche Gefahr, nicht die Zahl.
    const envValue = wf.match(/MAKER_TIMEOUT_MINUTES:\s*"(\d+)"/)?.[1];
    const jobTimeout = wf.match(/runs-on:\s*\[self-hosted[^\]]*\][\s\S]*?timeout-minutes:\s*(\d+)/)?.[1];
    expect(envValue, "MAKER_TIMEOUT_MINUTES fehlt im Workflow").toBeDefined();
    expect(jobTimeout, "timeout-minutes des Maker-Jobs fehlt").toBeDefined();
    expect(jobTimeout).toBe(envValue);
  });

  it("läuft vollständig auf dem lokalen Runner — kein Cloud-Anteil", () => {
    // Owner-Entscheidung 29.09.2026: zunächst nur lokal.
    const runners = [...wf.matchAll(/^\s*runs-on:\s*(.+)$/gm)].map((m) => m[1].trim());
    expect(runners).toEqual(["[self-hosted, windows, claude]"]);
  });

  it("es gibt keine Label-Vorfilterung mehr, die driften koennte (#3919)", () => {
    // Frueher kodierte `if: github.event.label.name == 'epic'` dieselbe Menge wie
    // WAKE_LABELS ein zweites Mal - zwei Orte, die auseinanderlaufen konnten. Mit dem
    // Label-Pfad faellt auch die Doppelung weg.
    expect(gate).not.toContain("WAKE_LABELS");
    expect(gate).not.toContain("def should_start(");
  });

  it("legt die Labels an, bevor es sie benutzt (#3779)", () => {
    // `gh issue edit --add-label` scheitert an einem unbekannten Label und meldet nur
    // "not found". Der Workflow soll den Zustand herstellen, den er braucht.
    const create = wf.indexOf("gh label create");
    const use = wf.indexOf('--add-label "maker:claimed"');
    expect(create, "kein `gh label create` im Workflow").toBeGreaterThan(-1);
    expect(use).toBeGreaterThan(create);
    for (const l of ["maker:claimed", "maker:failed"]) {
      expect(wf).toContain(`gh label create "${l}"`);
    }
    // SEC-01: kein || true, sondern idempotentes --force
    const labelStep = wf.slice(create, use);
    expect(labelStep).not.toContain("|| true");
    expect(labelStep).toContain("--force");
  });

  it("setzt die Lease VOR der Arbeit", () => {
    const claim = wf.indexOf("Lease setzen");
    const work = wf.indexOf("claude -p");
    expect(claim).toBeGreaterThan(-1);
    expect(work).toBeGreaterThan(claim);
  });

  it("hinterlaesst nach erfolgreicher Arbeit eine Sperre (#3783)", () => {
    // Sonst zerlegt der Cron dasselbe Epic in jedem Takt erneut. Die Sperre muss NACH
    // dem Kommentar gesetzt werden - sonst gilt ein abgebrochener Lauf als erledigt.
    const comment = wf.lastIndexOf("gh issue comment \"$ISSUE\"");
    // #3810: nicht mehr ueber `gh issue edit` - das braucht `read:org` fuer seine
    // eigene Ausgabe und scheiterte in Lauf 36702118807, obwohl das Label sass.
    const mark = wf.indexOf("labels[]=maker:proposed");
    expect(mark, "keine Sperre nach dem Vorschlag").toBeGreaterThan(-1);
    expect(gate).toContain('PROPOSED_LABEL = "maker:proposed"');
    expect(gate).toContain("PROPOSED_LABEL,");
    expect(comment).toBeGreaterThan(-1);
  });

  it("räumt die Lease auch bei Abbruch auf, nicht nur bei Fehlschlag", () => {
    const cleanup = lines.findIndex((l) => l.includes("Lease aufräumen"));
    expect(cleanup).toBeGreaterThan(-1);
    const block = lines.slice(cleanup, cleanup + 4).join("\n");
    expect(block).toContain("if: always()");
    expect(wf).toContain("--remove-label \"maker:claimed\"");
    expect(wf).toContain("maker:failed");
  });

  it("jede Pipeline behält ihren Exit-Code (#3687)", () => {
    const teeLines = lines.map((l, i) => ({ l, i })).filter(({ l }) => /\|\s*tee\b/.test(l));
    for (const { i } of teeLines) {
      const above = lines.slice(Math.max(0, i - 10), i).join("\n");
      expect(above, `Zeile ${i + 1} leitet in tee um, ohne pipefail`).toMatch(/set\s+-[A-Za-z]*\s*(-o\s+)?pipefail/);
    }
    // Und die Schritte, die wirklich etwas entscheiden, setzen es ohnehin.
    expect(wf).toContain("set -euo pipefail");
  });

  it("der Prompt liegt, wo der Workflow ihn sucht", () => {
    // Fehlt die Datei, liest `cat` ins Leere und `claude -p` bekommt nur den Epic-Text —
    // ohne Auftrag, ohne Format, ohne Grenzen. Der Lauf sähe erfolgreich aus.
    expect(existsSync(join(repoRoot, "docs/prompts/MAKER_EPIC_DECOMPOSE.md"))).toBe(true);
    expect(wf).toContain("docs/prompts/MAKER_EPIC_DECOMPOSE.md");
  });

  it("verspricht keinen Trockenlauf mehr, seit er Artefakte anlegt (#3785)", () => {
    // `dry_run=true` im Protokoll, während der Workflow Issues und PRs erzeugt, wäre
    // eine falsche Zusage an genau der Stelle, an der jemand nachsieht.
    expect(gate).not.toContain("dry_run: bool");
    expect(wf).not.toContain("(Stufe 1, Trockenlauf)");
    expect(wf).not.toContain("erzeugt nichts");
  });

  it("benennt die Shell ausdruecklich — sonst landet sie bei WSL (#3768)", () => {
    // `shell: bash` loest der Runner ueber PATH auf. Auf Windows liegt
    // C:\windows\system32/bash.exe vorn — der WSL-Starter. Ohne Distribution
    // gibt es dort kein /bin/bash, und JEDER Schritt bricht ab (Lauf 36545638636).
    expect(wf).not.toMatch(/^\s*shell:\s*bash\s*$/m);
    expect(wf).toContain(String.raw`Git\bin\bash.exe`);
  });

  it("enthaelt keine Steuerzeichen", () => {
    // Beim Einbau der Shell-Zeile wurde aus einem Backslash-b ein Backspace (0x08) — die Datei war
    // damit kein gueltiges YAML mehr. Ein Zeichen, das man im Editor nicht sieht.
    const control = [...wf].filter((c) => c.charCodeAt(0) < 9 || (c.charCodeAt(0) > 13 && c.charCodeAt(0) < 32));
    expect(control, `Steuerzeichen gefunden: ${control.map((c) => c.charCodeAt(0)).join(",")}`).toHaveLength(0);
  });

  it("fasst keine Governance-Dateien an", () => {
    // Der Prüfling darf seinen Prüfer nicht schreiben (CLAUDE.md §9). In Stufe 1 genügte
    // dafür, dass der Prompt das Thema gar nicht erwähnt. Ab Stufe 2 erzeugt der Maker
    // Dateien, also muss die Grenze benannt UND geprüft sein — die Prüfung sitzt in
    // maker_plan.py, weil ein Prompt eine Bitte ist und diese Regel eine Grenze.
    expect(wf).not.toMatch(/scripts\/ai_judge\.py/);
    const prompt = read("docs/prompts/MAKER_EPIC_DECOMPOSE.md");
    // #3797: Der Prompt verbietet nicht mehr das Erwaehnen, sondern das Aendern -
    // sonst waere jede CI-Ratsche unplanbar. Der Schutz sitzt strukturell
    // (test_maker_apply.py::TestSchreibgrenzen) und am Code-PR (Governance Guard).
    expect(prompt).toMatch(/Governance-Dateien nennen ja, ändern nein/);
    expect(prompt).toMatch(/governance-bypass/);
    const plan = read("scripts/maker_plan.py");
    expect(plan).toContain('GOVERNANCE_PFADE = (".github/workflows", "scripts/ai_judge.py")');
  });
});
