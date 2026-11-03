// #5136 (Epic #4450): The broker's "OAuth Due Diligence Questionnaire v.3 03.26", page 1, forbids
// its name on the website and in marketing material. It is allowed only "when showing brokerage
// integrations partners"; otherwise neutral language like "link your brokerage account" applies.
//
// This is a broker rule, deliberately separate from the #4457 wording guard (wordingGuard.ts):
// it has its own exceptions and can be withdrawn on its own (e.g. after a written release).
//
// Checked surfaces = what src/App.tsx routes publicly (/ and /preview → LandingViewE, /appliance,
// /performance), the static files shipped unchanged into dist/ (index.html, public/llms.txt), the
// landing.* texts, plus unshipped landing variants and the broker widget so a reactivation cannot
// bring the name back unnoticed. A new public page must be added to SOURCES here.
// Not checked on purpose: src/pages/Legal.tsx and /support (docs/oss/FAQ.md) — owner decision,
// see docs/5136-*/implementation_plan.md §6 Offen 1 and 8.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect } from "vitest";

const NAME = /alpaca/i;

// vitest runs from the repo root; import.meta.url is not file-schemed under the transform.
const read = (p: string) => readFileSync(resolve(process.cwd(), p), "utf8");

// Comments are not rendered. Block comments ({/* … */} and /* … */) are blanked while keeping
// their line breaks so reported line numbers stay true; a // comment is cut unless it is part of
// a URL (preceded by ":").
const withoutComments = (src: string): string =>
  src
    .replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, " "))
    .split("\n")
    .map((line) => line.replace(/(^|[^:])\/\/.*$/, "$1"))
    .join("\n");

const SOURCES = [
  "src/components/views/LandingViewE.tsx",
  "src/components/views/LandingViewB.tsx",
  "src/components/views/LandingViewD.tsx",
  "src/components/BrokerConnectionWidget.tsx",
  "src/pages/Appliance.tsx",
  "src/pages/Performance.tsx",
];

interface Ausnahme {
  datei: string;
  enthaelt: string;
  grund: string;
}

const PARTNER = "DDQ v.3 S.1: brokerage integrations partners";
const PRODUKT_UI = "Produkt-UI hinter PrivateRoute, technischer Bezeichner; #5136 Offen 2";

const AUSNAHMEN: Ausnahme[] = [
  { datei: "src/components/views/LandingViewD.tsx", enthaelt: "Execution adapter — Alpaca in-box", grund: PARTNER },
  { datei: "src/components/views/LandingViewD.tsx", enthaelt: "execution · Alpaca · IBKR planned", grund: PARTNER },
  { datei: "src/components/views/LandingViewD.tsx", enthaelt: "interface. Alpaca and IBKR ship in-box", grund: PARTNER },
  { datei: "src/components/BrokerConnectionWidget.tsx", enthaelt: "ALPACA_API_KEY=", grund: PRODUKT_UI },
  { datei: "src/components/BrokerConnectionWidget.tsx", enthaelt: "ALPACA_SECRET_KEY=", grund: PRODUKT_UI },
];

const landing = (lang: "en" | "de") => JSON.parse(read(`src/locales/${lang}/translation.json`)).landing;

describe("Alpaca-Name nur dort, wo die DDQ-Auflage ihn erlaubt (#5136)", () => {
  it("index.html nennt den Namen nicht (keywords, featureList, noscript)", () => {
    const html = read("index.html").replace(/<!--[\s\S]*?-->/g, "");
    const ld = JSON.parse(html.match(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/i)![1]);
    const nodes: Array<Record<string, unknown>> = ld["@graph"] ?? [ld];
    const app = nodes.find((n) => n["@type"] === "SoftwareApplication")!;
    expect(String(app.keywords)).not.toMatch(NAME);
    expect(JSON.stringify(app.featureList)).not.toMatch(NAME);
    const noscript = html.match(/<noscript>([\s\S]*?)<\/noscript>/i)?.[1] ?? "";
    expect(noscript).not.toMatch(NAME);
    expect(noscript).toMatch(/paper mode/i);
    expect(noscript).toMatch(/brokerage account/i);
    expect(html).not.toMatch(NAME);
  });

  it("public/llms.txt nennt den Namen nicht", () => {
    const t = read("public/llms.txt");
    expect(t).not.toMatch(NAME);
    const broker = t.split(/\r?\n/).find((l) => l.startsWith("- Broker:"));
    expect(broker, "Zeile '- Broker:' fehlt").toBeTruthy();
    expect(broker).toMatch(/your own brokerage account/i);
    expect(broker).toMatch(/paper mode is the default/i);
  });

  it("landing.* in en und de nennt den Namen nicht", () => {
    expect(JSON.stringify(landing("en"))).not.toMatch(NAME);
    expect(JSON.stringify(landing("de"))).not.toMatch(NAME);
  });

  it("Landing-Quelltext nennt den Namen nur in AUSNAHMEN", () => {
    const funde: string[] = [];
    for (const datei of SOURCES) {
      const ausnahmen = AUSNAHMEN.filter((a) => a.datei === datei);
      withoutComments(read(datei))
        .split("\n")
        .forEach((zeile, i) => {
          if (!NAME.test(zeile)) return;
          if (ausnahmen.some((a) => zeile.includes(a.enthaelt))) return;
          funde.push(`${datei}:${i + 1}: ${zeile.trim()}`);
        });
    }
    expect(funde).toEqual([]);
  });

  it("jede Ausnahme hat einen Grund und passt auf genau eine Zeile", () => {
    for (const a of AUSNAHMEN) {
      expect(a.grund.trim(), `${a.datei} · ${a.enthaelt}: Grund fehlt`).not.toBe("");
      expect(SOURCES, `${a.datei} steht nicht in SOURCES`).toContain(a.datei);
      const treffer = withoutComments(read(a.datei))
        .split("\n")
        .filter((z) => z.includes(a.enthaelt) && NAME.test(z));
      expect(treffer.length, `${a.datei} · ${a.enthaelt}: passt auf ${treffer.length} Zeilen`).toBe(1);
    }
  });

  it("Aufforderungen zum Verbinden lauten neutral", () => {
    expect(landing("en").pricing.private.senior.f1.desc).toMatch(/^Link your brokerage account\b/);
    expect(landing("de").pricing.private.senior.f1.desc).toMatch(/^Verknüpfen Sie Ihr Brokerkonto\b/);
    expect(withoutComments(read("src/components/BrokerConnectionWidget.tsx"))).toMatch(
      /Link your brokerage account to enable the AI Trading Engine/,
    );
  });
});
