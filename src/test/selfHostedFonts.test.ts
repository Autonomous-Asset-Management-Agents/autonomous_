// Self-hosted fonts (legal review 22.10.2026): loading Google Fonts from fonts.googleapis.com sends
// every visitor's IP address to Google without consent (LG München I, 20.01.2022, 3 O 17493/20).
// The website and the console must not reference Google's font hosts; the fonts ship with the build.
//
// Source-level assertions: the request happens in the browser before any component mounts.
import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

const root = process.cwd();
const GOOGLE_FONT_HOSTS = /fonts\.googleapis\.com|fonts\.gstatic\.com/;

const filesUnder = (dir: string): string[] =>
    readdirSync(dir).flatMap((name) => {
        const path = join(dir, name);
        if (statSync(path).isDirectory()) return filesUnder(path);
        return /\.(css|tsx?|html)$/.test(name) ? [path] : [];
    });

// Comments may name the old host to explain the change; only real references count.
const withoutComments = (src: string): string => src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/<!--[\s\S]*?-->/g, "");

describe("self-hosted fonts", () => {
    it("index.html loads no font from Google", () => {
        const html = readFileSync(resolve(root, "index.html"), "utf8");
        expect(withoutComments(html)).not.toMatch(GOOGLE_FONT_HOSTS);
    });

    it("no stylesheet or component under src/ references Google's font hosts", () => {
        const offenders = filesUnder(resolve(root, "src"))
            .filter((f) => !f.endsWith("selfHostedFonts.test.ts"))
            .filter((f) => GOOGLE_FONT_HOSTS.test(withoutComments(readFileSync(f, "utf8"))));
        expect(offenders).toEqual([]);
    });

    it("the global stylesheet imports the self-hosted Inter and JetBrains Mono", () => {
        const indexCss = readFileSync(resolve(root, "src/index.css"), "utf8");
        expect(indexCss).toMatch(/@import ['"]\.\/styles\/fonts\.css['"]/);
        const fonts = readFileSync(resolve(root, "src/styles/fonts.css"), "utf8");
        // Same family name the CSS and components use, so the design does not change.
        expect(fonts).toMatch(/font-family: 'Inter';/);
        expect(fonts).toContain("@fontsource/jetbrains-mono/500.css");
        // CSS drops an @import that follows any other rule; JetBrains Mono would silently fall back.
        const lastImport = fonts.lastIndexOf("@import");
        expect(lastImport).toBeGreaterThan(-1);
        expect(lastImport).toBeLessThan(fonts.indexOf("@font-face"));
    });

    it("the hosting CSP no longer allows Google's font hosts", () => {
        // Blocks a reintroduced Google Fonts link in production instead of silently loading it.
        const firebase = readFileSync(resolve(root, "firebase.json"), "utf8");
        expect(firebase).toContain("font-src 'self' data:;");
        expect(firebase).not.toMatch(GOOGLE_FONT_HOSTS);
    });

    it("the Stitch variant brings its own Manrope and Material Symbols", () => {
        const stitch = readFileSync(resolve(root, "src/components/variants/stitch-v1/pages/IndexStitch.tsx"), "utf8");
        expect(stitch).toContain('import "@fontsource/manrope/800.css";');
        expect(stitch).toContain('import "material-symbols/outlined.css";');
    });
});
