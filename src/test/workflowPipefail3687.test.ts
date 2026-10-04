import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

/**
 * #3687 — eine Sperre darf ihren Exit-Code nicht an den Protokollierer verlieren.
 *
 * `release-mac.yml` prüfte Tag gegen `package.json`, meldete korrekt NO-GO — und der
 * Schritt wurde trotzdem grün. Grund war die Zeile, die die Ausgabe in die
 * Schritt-Zusammenfassung schreibt:
 *
 *     node desktop/scripts/check-release-version.mjs "$TAG" | tee -a "$GITHUB_STEP_SUMMARY"
 *
 * GitHub führt `run:` auf Nicht-Windows als `bash -e {0}` aus: mit `errexit`, aber ohne
 * `pipefail`. Der Rückgabewert einer Pipeline ist dann der des LETZTEN Glieds — `tee`,
 * das immer gelingt. So wurde das defekte `desktop-mac-v0.5.1` veröffentlicht.
 *
 * Dieser Test prüft die Eigenschaft, nicht die eine Zeile: jede Pipeline in einem
 * `run:`-Block, deren erstes Glied scheitern kann, braucht `pipefail`.
 */

const workflowDir = join(__dirname, "..", "..", ".github", "workflows");

type Finding = { file: string; line: number; text: string };

/** Zeilen mit einer Pipe in eine externe Senke (`tee`), die den Status maskiert. */
function pipedGates(file: string): Finding[] {
  const lines = readFileSync(join(workflowDir, file), "utf-8").split(/\r?\n/);
  const out: Finding[] = [];
  lines.forEach((text, i) => {
    if (!/\|\s*tee\b/.test(text)) return;
    if (/^\s*#/.test(text.trim())) return;
    out.push({ file, line: i + 1, text: text.trim() });
  });
  return out;
}

/** Setzt der umgebende `run:`-Block pipefail, bevor die Pipeline kommt? */
function hasPipefailAbove(file: string, line: number): boolean {
  const lines = readFileSync(join(workflowDir, file), "utf-8").split(/\r?\n/);
  for (let i = line - 2; i >= 0; i--) {
    const l = lines[i];
    // Eine echte Anweisung, kein Kommentar: Beim ersten Entwurf genügte das blosse
    // Wort "pipefail" — und der Test bestand, weil der erklärende Kommentar darüber
    // es enthielt. Ein Wächter, der sich von seiner eigenen Begründung überzeugen
    // lässt, prüft nichts.
    if (/^\s*set\s+-[A-Za-z]*\s*(-o\s+)?pipefail/.test(l)) return true;
    // Blockgrenze: der Anfang dieses run-Blocks bzw. der vorige Schritt.
    if (/^\s*(run:|- name:|- uses:)/.test(l)) return false;
  }
  return false;
}

const workflows = readdirSync(workflowDir).filter((f) => f.endsWith(".yml") || f.endsWith(".yaml"));
const findings = workflows.flatMap(pipedGates);

describe("#3687 Workflow-Pipelines verlieren ihren Exit-Code nicht", () => {
  it("es gibt überhaupt Pipelines dieser Art (sonst prüft der Test nichts)", () => {
    // Verschwinden alle, verliert der Test seinen Gegenstand — dann soll er auffallen,
    // damit jemand entscheidet, ob er noch gebraucht wird.
    expect(findings.length).toBeGreaterThan(0);
  });

  it.each(findings)("$file:$line setzt pipefail", ({ file, line, text }) => {
    expect(
      hasPipefailAbove(file, line),
      `${file}:${line} leitet in 'tee' um, ohne 'set -o pipefail':\n  ${text}\n` +
        `Der Exit-Code des ersten Glieds geht verloren — ein fail-open Tor (vgl. desktop-mac-v0.5.1).`,
    ).toBe(true);
  });
});
