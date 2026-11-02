// Hero tower flag (operator 16.08.): the landing hero carries one green flag linking
// to /appliance. The operator's hard constraint was "nothing on the existing design
// changes", so these assertions pin the flag as ADDITIVE — it derives its width from
// the CTA row (align-self: stretch + a desktop-only wrapper rule) instead of
// restyling LIVE DEMO / DOWNLOAD.
//
// Source-level assertions, matching applianceRoute.test.tsx: LandingViewE does not
// mount in jsdom (Lenis/observer-driven hero).
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const landing = readFileSync(
  resolve(process.cwd(), "src/components/views/LandingViewE.tsx"),
  "utf8",
);
const css = readFileSync(resolve(process.cwd(), "src/styles/landing-d.css"), "utf8");
// The flag copy moved into the catalogue (i18n step 1); assert it there.
const en = JSON.parse(
  readFileSync(resolve(process.cwd(), "src/locales/en/translation.json"), "utf8"),
);

// the flag element, from its aria-label to the closing tag
// (#4514: the aria-label moved into the catalogue as landing.hero.flagAria)
const flag = landing.slice(landing.indexOf('aria-label={t("landing.hero.flagAria")}'));

describe("hero tower flag", () => {
  it("links to the tower page and names hardware, product and price", () => {
    expect(landing).toMatch(/aria-label=\{t\("landing\.hero\.flagAria"\)\}/);
    expect(en.landing.hero.flagAria).toBe("autonomous_ tower — new hardware, First Edition");
    expect(flag).toMatch(/t\("landing\.hero\.flagKicker"\)/);
    expect(flag).toMatch(/t\("landing\.hero\.flagName"\)/);
    expect(en.landing.hero.flagKicker).toBe("New hardware · price on request");
    expect(en.landing.hero.flagName).toBe("autonomous_ tower");
    // href + SPA navigate both point at the tower page
    expect(landing).toMatch(/href="\/appliance"[\s\S]{0,400}landing\.hero\.flagAria/);
    expect(flag).toMatch(/appliance-tower\.webp/);
  });

  it("stays additive: stretches to the CTA row, no fixed width", () => {
    expect(flag.slice(0, 900)).toMatch(/alignSelf:\s*"stretch"/);
    expect(flag.slice(0, 900)).not.toMatch(/width:\s*\d+/);
  });

  it("the CTA pills keep their own styling (no inline overrides)", () => {
    // LIVE DEMO / DOWNLOAD / VIEW THE CODE (#4514) must remain plain .lb-cta-social anchors:
    // the opening tag of each hero pill carries no inline style.
    for (const label of [
      'aria-label={t("landing.hero.ctaDemoAria")}',
      'aria-label={t("landing.hero.ctaDownloadAria")}',
      'data-cta="github_code"',
    ]) {
      const end = landing.indexOf(label);
      expect(end, label).toBeGreaterThan(0);
      const tagStart = landing.lastIndexOf("<a", end);
      const openingTag = landing.slice(tagStart, end);
      expect(openingTag, `${label} must stay unstyled`).not.toMatch(/style=/);
    }
  });

  it("the width coupling is desktop-only so mobile keeps full-width buttons", () => {
    // below 901px the existing mobile rule (.lb-hero-ctas { width: 100% }) must win
    expect(css).toMatch(/@media \(min-width: 901px\)[\s\S]{0,200}lb-hero-ctas[\s\S]{0,80}max-content/);
  });
});
