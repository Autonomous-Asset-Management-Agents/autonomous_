// C-B — the first-run setup wizard must give honest feedback + a recovery path:
// the error banner is announced, the Alpaca + Ollama steps link to where to get things, and the
// user can step Back instead of being locked into a forward-only flow.
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const dir = path.dirname(fileURLToPath(import.meta.url));
const src = readFileSync(path.join(dir, "..", "console", "setup", "SetupWizard.tsx"), "utf8");

describe("C-B: setup wizard feedback + recovery", () => {
  it("the error banner is announced to assistive tech (role=alert)", () => {
    expect(src).toMatch(/role="alert"/);
  });

  it("the Alpaca step links to where to get the keys", () => {
    expect(src).toMatch(/alpaca\.markets/);
  });

  it("a failed local-AI install surfaces a clickable Ollama download link", () => {
    expect(src).toMatch(/needsOllamaInstall/);
    expect(src).toMatch(/ollama\.com\/download/);
  });

  it("steps after welcome have a Back control (not a forward-only trap)", () => {
    const backs = (src.match(/>\s*Back\s*</g) || []).length;
    expect(backs).toBeGreaterThanOrEqual(2);
  });
});
