// #5155 (GTM-1): no place in the renderer takes Alpaca credentials without Alpaca's authorization
// prompt. A file that validates Alpaca keys, connects a paper slot or stores an ALPACA_* secret must
// import `useBrokerAuthorization` — the hook that writes the answer FIRST and stops on DENY or on a
// write error (src/console/desktop/useBrokerAuthorization.tsx). A new call site without it is red.
//
// Static check in the pattern of designGuard.test.ts: read the source, apply the rule. Excluded are
// the tests and `src/lib/desktopBridge.ts` (it DEFINES the bridge functions).
import { describe, it, expect } from "vitest";
import * as fs from "node:fs";
import * as path from "node:path";

const SRC = path.resolve(__dirname, "..");
const EXCLUDED = [path.join(SRC, "test"), path.join(SRC, "lib", "desktopBridge.ts")];

/** Does this source take Alpaca credentials? */
export function takesAlpacaCredentials(source: string): boolean {
  if (/\bvalidateAlpaca\s*\(/.test(source)) return true;
  if (/\bconnectPaperSlot\s*\(/.test(source)) return true;
  return /\bsaveSecret\s*\(/.test(source) && /\bALPACA_/.test(source);
}

/** Does this source import the hook? */
export function importsAuthorizationHook(source: string): boolean {
  return /import\s*\{[^}]*\buseBrokerAuthorization\b[^}]*\}\s*from\s*["'][^"']*\/useBrokerAuthorization["']/.test(source);
}

export const violates = (source: string) => takesAlpacaCredentials(source) && !importsAuthorizationHook(source);

function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name);
    if (EXCLUDED.includes(p)) continue;
    if (entry.isDirectory()) out.push(...sourceFiles(p));
    else if (/\.(ts|tsx)$/.test(entry.name) && !/\.test\.(ts|tsx)$/.test(entry.name)) out.push(p);
  }
  return out;
}

describe("Alpaca authorization prompt at every call site (#5155)", () => {
  it("the rule can be red: saveSecret(ALPACA_…) without the hook import is a violation", () => {
    const bad = `import { saveSecret } from "@/lib/desktopBridge";\nawait saveSecret("ALPACA_API_KEY", k);\n`;
    expect(violates(bad)).toBe(true);
    expect(violates(`import { validateAlpaca } from "@/lib/desktopBridge";\nvalidateAlpaca(a, b);`)).toBe(true);
    expect(violates(`connectPaperSlot("paper2", a, b);`)).toBe(true);
  });

  it("the rule is not over-eager", () => {
    // Another secret (LLM, Telegram) is no broker account.
    expect(violates(`await saveSecret("TELEGRAM_BOT_TOKEN", t);`)).toBe(false);
    // With the hook imported, the call site is covered.
    const ok =
      `import { useBrokerAuthorization } from "@/console/desktop/useBrokerAuthorization";\n` +
      `await saveSecret("ALPACA_API_KEY", k);`;
    expect(violates(ok)).toBe(false);
  });

  it("no renderer file takes Alpaca credentials without importing useBrokerAuthorization", () => {
    const offenders = sourceFiles(SRC)
      .filter((f) => violates(fs.readFileSync(f, "utf8")))
      .map((f) => path.relative(SRC, f).replace(/\\/g, "/"));
    expect(offenders).toEqual([]);
  });

  it("the guard actually sees the four known call sites", () => {
    const sites = sourceFiles(SRC)
      .filter((f) => takesAlpacaCredentials(fs.readFileSync(f, "utf8")))
      .map((f) => path.relative(SRC, f).replace(/\\/g, "/"))
      .sort();
    expect(sites).toEqual([
      "console/desktop/BrokerKeysCard.tsx",
      "console/desktop/LiveConsentModal.tsx",
      "console/desktop/strategyWorkshop/PaperAccountSlots.tsx",
      "console/setup/SetupWizard.tsx",
    ]);
  });
});
