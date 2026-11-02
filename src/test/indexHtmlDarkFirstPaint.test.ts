// #2804: the desktop window flashed WHITE on start before turning black. Electron shows the
// window on `ready-to-show`, which fires on the renderer's FIRST paint — and that paint happens
// before the bundled stylesheet (where `--background: 0 0% 0%` lives) is applied, so the
// document's browser-default white shows through. The BrowserWindow's `backgroundColor` only
// covers the native layer, not the renderer's document background. The fix is an INLINE dark
// background in index.html's <head>, which is guaranteed to be in effect for the very first
// paint. This test is the tripwire so an index.html rework can't silently drop it.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect } from "vitest";

describe("index.html first-paint background (#2804)", () => {
  // vitest runs from the repo root; import.meta.url is not file-schemed under the transform.
  const html = readFileSync(resolve(process.cwd(), "index.html"), "utf8");

  it("head carries an inline style painting html/body black before any external CSS/JS", () => {
    const headEnd = html.indexOf("</head>");
    expect(headEnd, "index.html must have a <head>").toBeGreaterThan(-1);
    const head = html.slice(0, headEnd);
    const style = head.match(/<style[^>]*>([\s\S]*?)<\/style>/i);
    expect(style, "inline <style> missing from <head> — first paint flashes white (#2804)").toBeTruthy();
    expect(style![1]).toMatch(/html\s*,\s*body\s*\{[^}]*background(?:-color)?\s*:\s*#000\b/);
  });
});
