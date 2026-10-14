// #3601: das Ausschluss-Tor des OSS-Snapshots prüft mit dem Python-Paket `pathspec`. Fehlt es,
// bricht das Tor fail-closed ab und es wird NICHTS veröffentlicht.
//
// #3616 erweiterte den Test auf jeden Workflow, der die Skripte AUSFÜHRT (oss-ci.yml war
// übersehen worden). #3641 schließt die dritte Variante derselben Naht: der Import-Smoke-Test
// in verify_oss_snapshot.sh importiert die `config.py` DES SNAPSHOTS, und die zieht seit #3553
// `settings` nach — also `dotenv`, `pydantic`, `pydantic_settings`, `pythonjsonlogger`. Diese
// Pakete tauchen in keinem Skript als Import auf; der Test muss den repo-eigenen Modulen folgen.
//
// Er liest alle beteiligten Dateien, statt Namen zu pinnen.
import { describe, it, expect } from "vitest";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";

const read = (p: string) => readFileSync(resolve(process.cwd(), p), "utf8");
const has = (p: string) => existsSync(resolve(process.cwd(), p));

/** Ruft dieser Workflow den Einstiegspunkt wirklich auf? Erwähnungen in Kommentaren nicht. */
const invokes = (content: string, ep: string) =>
  content.split("\n").some((line) => !line.trim().startsWith("#") && line.includes(ep));

/** Steht das Paket in einem `pip install`-Aufruf? Bewusst ohne Regex — in einem
 *  Template-Literal wird aus `[\s\S]` still die Zeichenklasse `[sS]`, und der
 *  Test prüft dann nichts mehr. */
const installsPackage = (content: string, want: string) =>
  content.split("pip install").slice(1).some((chunk) => chunk.slice(0, 400).includes(want));

const WORKFLOW_DIR = ".github/workflows";

/** Einstiegspunkte, die ein Workflow aufrufen kann, und die Dateien, deren Importe dabei
 *  wirklich geladen werden. `oss_make_snapshot.sh` selbst ist Shell — Python kommt über die
 *  Bereinigung herein. */
const ENTRYPOINTS: Record<string, string[]> = {
  "scripts/oss_make_snapshot.sh": ["scripts/oss_prune_snapshot.py"],
  "scripts/verify_oss_snapshot.sh": ["scripts/verify_oss_snapshot.sh"],
};

/** Python-Standardbibliothek — muss nicht installiert werden. */
const STDLIB = new Set([
  "sys", "os", "json", "re", "hashlib", "pathlib", "subprocess", "shutil", "typing",
  "logging", "threading", "datetime", "contextvars", "functools", "dataclasses", "enum",
  "math", "platform", "base64", "time", "collections", "itertools", "importlib", "warnings",
  "__future__",
]);

/** Modulname ≠ Paketname, wo beides auseinanderfällt. */
const PACKAGE_OF: Record<string, string> = {
  dotenv: "python-dotenv",
  pydantic_settings: "pydantic-settings",
  pythonjsonlogger: "python-json-logger",
};

/** Löst einen Modulpfad auf die Datei auf, die im Snapshot tatsächlich landet.
 *  Die `.oss`-Variante gewinnt: `config.oss.py` wird beim Snapshot zu `config.py`
 *  umbenannt und ist damit die Datei, die der Smoke-Test importiert. */
function repoLocalFile(mod: string): string | null {
  const rel = mod.replace(/\./g, "/");
  for (const cand of [
    `ai_trading_bot/${rel}.oss.py`,
    `ai_trading_bot/${rel}.py`,
    `ai_trading_bot/${rel}/__init__.py`,
    `${rel}.py`,
  ]) {
    if (has(cand)) return cand;
  }
  return null;
}

/** Importe auf Modulebene (Spalte 0). Faule Importe in Funktionen — `keyring`,
 *  `google.genai` — laufen beim blossen Import nicht und zählen deshalb nicht. */
const MODULE_LEVEL = /^(?:import|from)\s+([A-Za-z_][\w.]*)/gm;
/** Im Shell-Skript steht das Python eingerückt im Here-Doc. */
const ANY_LEVEL = /^\s*(?:import|from)\s+([A-Za-z_][\w.]*)/gm;

/** Sammelt jedes Fremdpaket, dem der Import begegnet — quer durch repo-eigene Module. */
function collectThirdParty(file: string, seen = new Set<string>(), out = new Set<string>(), depth = 0): Set<string> {
  if (depth > 4 || seen.has(file) || !has(file)) return out;
  seen.add(file);
  const pattern = file.endsWith(".py") && depth > 0 ? MODULE_LEVEL : ANY_LEVEL;
  for (const m of read(file).matchAll(pattern)) {
    const mod = m[1];
    const top = mod.split(".")[0];
    if (STDLIB.has(top)) continue;
    const local = repoLocalFile(mod);
    if (local) {
      collectThirdParty(local, seen, out, depth + 1);
      continue;
    }
    out.add(top);
  }
  return out;
}

/** Jede Workflow-Datei, die einen Einstiegspunkt tatsächlich AUFRUFT (Erwähnungen in
 *  Kommentaren zählen nicht) — samt der Einstiegspunkte, die sie aufruft. */
const invocations = readdirSync(resolve(process.cwd(), WORKFLOW_DIR))
  .filter((f) => f.endsWith(".yml") || f.endsWith(".yaml"))
  .flatMap((wf) => {
    const content = read(`${WORKFLOW_DIR}/${wf}`);
    return Object.keys(ENTRYPOINTS)
      .filter((ep) => invokes(content, ep))
      .map((ep) => ({ wf, ep }));
  });

describe("OSS-Ausschluss-Tor (#3601/#3616/#3641) — der Workflow installiert, was die Skripte brauchen", () => {
  it("mindestens ein Workflow ruft die Tor-Skripte auf", () => {
    // Findet der Filter nichts, prüft die Matrix unten nichts — das soll auffallen.
    expect(invocations.length).toBeGreaterThan(0);
  });

  it("der Smoke-Test zieht Abhängigkeiten über repo-eigene Module nach (#3641)", () => {
    // Genau hier lag die Lücke: `dotenv` steht in keinem Skript, sondern in der
    // config.py des Snapshots. Fällt diese Verfolgung weg, ist der Test blind.
    const viaVerify = collectThirdParty("scripts/verify_oss_snapshot.sh");
    expect([...viaVerify]).toContain("dotenv");
  });

  const matrix = invocations.flatMap(({ wf, ep }) =>
    ENTRYPOINTS[ep].flatMap((src) => [...collectThirdParty(src)].map((pkg) => ({ wf, ep, pkg }))),
  );

  it("die Matrix ist nicht leer", () => {
    expect(matrix.length).toBeGreaterThan(0);
  });

  it.each(matrix)("$wf installiert $pkg (via $ep)", ({ wf, ep, pkg }) => {
    const content = read(`${WORKFLOW_DIR}/${wf}`);
    const want = PACKAGE_OF[pkg] ?? pkg;
    expect(
      installsPackage(content, want),
      `${ep} braucht beim Import '${want}', aber ${WORKFLOW_DIR}/${wf} führt es aus, ohne das ` +
        `Paket zu installieren — der Lauf bricht fail-closed ab`,
    ).toBe(true);
  });
});
