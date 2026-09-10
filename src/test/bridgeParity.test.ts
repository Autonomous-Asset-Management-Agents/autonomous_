import { describe, it, expect } from "vitest";
import { readFileSync } from "fs";
import { resolve } from "path";
import { BRIDGE_KEYS } from "@/lib/desktopBridge";

// #2744/S5: guard against the preload ⇄ renderer contract drifting. Every member the renderer relies
// on (BRIDGE_KEYS, kept in sync with the AAAgentsBridge interface) MUST be exposed by
// desktop/electron/preload.cjs — otherwise a renderer call silently hits an undefined bridge method
// (the class of bug that made the mac demo-shell mis-handle the keychain status). Static source check:
// no Electron runtime needed, so it runs in the normal vitest/CI environment.

const PRELOAD = readFileSync(
  resolve(__dirname, "../../desktop/electron/preload.cjs"),
  "utf-8",
);

describe("desktop bridge parity (preload ⊇ BRIDGE_KEYS)", () => {
  it("preload.cjs exposes every key the renderer contract declares", () => {
    const missing = BRIDGE_KEYS.filter((key) => {
      // exposeInMainWorld object members are written as `key:` (functions/values) — match at a
      // property position (start of a trimmed line) to avoid matching the name inside a comment.
      const re = new RegExp(`(^|[,{]\\s*|\\n\\s*)${key}\\s*:`, "m");
      return !re.test(PRELOAD);
    });
    expect(missing, `preload.cjs is missing bridge keys: ${missing.join(", ")}`).toEqual([]);
  });

  it("exposes it via contextBridge.exposeInMainWorld('aaagents', …)", () => {
    expect(PRELOAD).toContain('exposeInMainWorld("aaagents"');
  });
});
