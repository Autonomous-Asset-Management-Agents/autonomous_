// i18n coverage guard for the main page (operator 16.08.).
//
// German was switched off because the landing was only partially translated —
// half German, half English. The extraction removes the hard-coded copy; this
// guard keeps it removed, so nobody can flip GERMAN_ENABLED back into the mixed
// page by accident.
//
// SCOPE TODAY: the pricing catalogue. It is fully extracted and can be asserted
// exactly. The landing component still carries hard-coded text in two flavours:
//   1. mock-up labels (hero terminal, performance chart, audit chain, risk
//      pipeline, agent diagram) — these STAY English by operator decision, they
//      depict the app UI;
//   2. a remainder of real copy (agent-section headline and captions, the
//      rotating hero words, the tower flag from #2893).
// Telling those two apart by proximity heuristics proved unreliable — a marker
// search over the surrounding source flags mock-ups and prose alike. The next
// increment marks the mock-up regions explicitly in the source
// (`/* i18n-ignore-start */ … /* i18n-ignore-end */`), which is deterministic;
// only then can this guard cover the component without false positives.
//
// #4514 adds the fixed texts of the landing component and the edition card. There is no
// released German copy for them yet, so the de catalogue carries the English wording (what
// the 'en' fallback would show anyway) and the key parity guards stay intact. They are
// listed in EN_WORDING_UNTIL_DE_RELEASE; the list must be empty before GERMAN_ENABLED can
// be switched on.
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { GERMAN_ENABLED } from "@/lib/languageGate";

const pricing = readFileSync(resolve(process.cwd(), "src/lib/pricing-config.ts"), "utf8");
const landing = readFileSync(resolve(process.cwd(), "src/components/views/LandingViewE.tsx"), "utf8");
const pricingCard = readFileSync(resolve(process.cwd(), "src/pages/PricingMaster.tsx"), "utf8");

// Key prefixes (#4514) whose de value is the English wording until German copy is released.
const EN_WORDING_UNTIL_DE_RELEASE = [
  "landing.ticker",
  "landing.nav.",
  "landing.hero.ctaCode",
  "landing.hero.ctaDemoAria",
  "landing.hero.ctaDownloadAria",
  "landing.hero.flagAria",
  "landing.hero.console.",
  "landing.profit.example.",
  "landing.safe.pipeline.",
  "landing.audit.graphic.",
  "landing.editions.towerAria",
  "landing.pricingCard.",
];
const enWording = (k: string) => EN_WORDING_UNTIL_DE_RELEASE.some((p) => k === p || k.startsWith(p));
const en = JSON.parse(
  readFileSync(resolve(process.cwd(), "src/locales/en/translation.json"), "utf8"),
);
const de = JSON.parse(
  readFileSync(resolve(process.cwd(), "src/locales/de/translation.json"), "utf8"),
);

function flatten(obj: Record<string, unknown>, prefix = ""): string[] {
  return Object.entries(obj).flatMap(([k, v]) =>
    typeof v === "object" && v !== null
      ? flatten(v as Record<string, unknown>, `${prefix}${k}.`)
      : [`${prefix}${k}`],
  );
}

describe("main page i18n coverage", () => {
  it("pricing data is built from the catalogue, not from literals", () => {
    const prose = [...pricing.matchAll(/"([A-Za-z][^"]{15,})"/g)]
      .map((m) => m[1])
      // catalogue keys are fine — we are hunting prose (a sentence has spaces)
      .filter((s) => /\s/.test(s) && !s.startsWith("landing."));
    expect(prose, "pricing-config must only reference keys").toEqual([]);
    expect(pricing).toMatch(/export const getPricingData = \(t: Translate\)/);
    expect(pricing).not.toMatch(/export const pricingData/);
  });

  it("every pricing key the config asks for exists in both catalogues", () => {
    const asked = [...pricing.matchAll(/t\("(landing\.pricing\.[^"]+)"\)/g)].map((m) => m[1]);
    expect(asked.length, "config should reference the whole pricing block").toBeGreaterThan(40);
    const enKeys = new Set(flatten(en));
    const deKeys = new Set(flatten(de));
    expect(asked.filter((k) => !enKeys.has(k)), "missing in en").toEqual([]);
    expect(asked.filter((k) => !deKeys.has(k)), "missing in de").toEqual([]);
  });

  it("en and de carry the same key set (no half-translated catalogue)", () => {
    const a = flatten(en).sort();
    const b = flatten(de).sort();
    expect(a.filter((k) => !b.includes(k)), "in en but not de").toEqual([]);
    expect(b.filter((k) => !a.includes(k)), "in de but not en").toEqual([]);
  });

  it("#4514: no unreleased German copy, and the German switch waits for it", () => {
    const get = (o: Record<string, unknown>, k: string) =>
      k.split(".").reduce<unknown>((x, p) => (x as Record<string, unknown>)?.[p], o);
    const keys = flatten(en).filter(enWording);
    expect(keys.length, "the #4514 keys exist").toBeGreaterThan(30);
    expect(keys.filter((k) => get(de, k) !== get(en, k)), "de differs from en").toEqual([]);
    if (GERMAN_ENABLED) {
      expect(EN_WORDING_UNTIL_DE_RELEASE, "German needs every landing key translated").toEqual([]);
    }
  });

  it("#4514: every landing key the landing and the edition card ask for exists in en", () => {
    const enKeys = new Set(flatten(en));
    const asked = [landing, pricingCard].flatMap((src) =>
      [...src.matchAll(/\bt\("((?:landing|appliance)\.[^"]+)"/g)].map((m) => m[1]),
    );
    expect(asked.filter((k) => !enKeys.has(k)), "missing in en").toEqual([]);
    // the edition card's fixed texts (BETA, modes, CTAs) come from the catalogue
    expect(pricingCard).toMatch(/t\("landing\.pricingCard\./);
  });
});
