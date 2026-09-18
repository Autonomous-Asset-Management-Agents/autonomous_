import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

// #1983 → mobile breakage (#1987) → this fix: the shared `.aaa-console` class styles BOTH the
// desktop app console AND the public web/mobile LiveDemo. #1983 put a CSS `zoom: 1.18` on it;
// even scoped to >=1024px (#1987) that CSS zoom scaled the 100vw/100vh `overflow:hidden` shell
// 18% larger than the viewport and CLIPPED the right/bottom ~18% on smaller / high-DPI desktop
// screens (and blocked fit-to-window). The 18% desktop enlargement is now applied NATIVELY via
// Electron `webContents.setZoomFactor` (desktop/electron/main.cjs), which reflows to the real
// window size. So the console CSS must carry NO `zoom` declaration at all — web/mobile render at
// 100%, the desktop app scales in the Electron shell.

const rawCss = readFileSync(
  path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "console", "console.css"),
  "utf8",
);
// Strip /* … */ comments so the guard checks real declarations, not the prose that
// documents WHY the CSS zoom was removed (that comment legitimately mentions `zoom`).
const css = rawCss.replace(/\/\*[\s\S]*?\*\//g, "");

describe("console CSS carries no `zoom` (desktop enlargement is native, not CSS)", () => {
  it("has no `zoom:` declaration anywhere in console.css", () => {
    expect(css).not.toMatch(/\bzoom\s*:/);
  });
});
