// #2869 review (POLICY-01/02): guard the i18n setup — key parity across locales
// and region-locale resolution to a base language the LanguageSwitcher can match.
//
// #4604: the language gate must own the LANGUAGE, not just the toggle. The gate-closed
// block below is the regression guard — before it, `GERMAN_ENABLED = false` hid the
// LanguageSwitcher while the browser detector still resolved navigator 'de-DE' -> 'de'
// and served the un-reviewed German bundle to every German visitor.
import { describe, expect, it } from "vitest";

import i18n from "../i18n";
import { GERMAN_ENABLED } from "../languageGate";
import de from "../../locales/de/translation.json";
import en from "../../locales/en/translation.json";

function keyPaths(obj: Record<string, unknown>, prefix = ""): string[] {
  return Object.entries(obj).flatMap(([k, v]) =>
    v && typeof v === "object"
      ? keyPaths(v as Record<string, unknown>, `${prefix}${k}.`)
      : [`${prefix}${k}`],
  );
}

describe("i18n translation resources", () => {
  it("en and de expose an identical key set (no missing translations)", () => {
    expect(keyPaths(de).sort()).toEqual(keyPaths(en).sort());
  });

  // #4604: the gate closes the DOOR, it does not throw away the room. The German strings
  // stay on disk so the DE launch is a gate flip plus a copy review, not a re-translation.
  it("keeps the German strings on disk so the DE launch stays a gate flip", () => {
    expect((de as { header: { download: string } }).header.download).toBe(
      "Herunterladen",
    );
  });
});

// #4604: while the gate is closed there is exactly ONE reachable language. These run only
// in that state; the DE-launch PR flips GERMAN_ENABLED and they skip in favour of the
// gate-open block below.
describe.runIf(!GERMAN_ENABLED)("i18n language gate — closed", () => {
  it("serves English for an explicit German request", async () => {
    await i18n.changeLanguage("de");
    expect(i18n.resolvedLanguage).toBe("en");
    expect(i18n.t("header.download")).toBe("Download");
  });

  it("serves English for a German browser locale (navigator 'de-DE')", async () => {
    await i18n.changeLanguage("de-DE");
    expect(i18n.resolvedLanguage).toBe("en");
    expect(i18n.languages).not.toContain("de-DE");
  });

  it("exposes no German in the supported language space", () => {
    expect(i18n.options.supportedLngs).not.toContain("de");
  });

  it("does not consult the browser detector, so no locale can reopen the gate", () => {
    expect(i18n.options.detection?.order ?? []).toEqual([]);
  });

  it("renders no German string even though the resource is bundled", async () => {
    await i18n.changeLanguage("de");
    expect(i18n.t("header.download")).not.toBe("Herunterladen");
  });
});

// The pre-#4604 contract, kept for the day the gate opens: region locales collapse to a
// base language the LanguageSwitcher's 'en'/'de' values can match, and German resolves.
describe.runIf(GERMAN_ENABLED)("i18n language gate — open", () => {
  it("collapses a region locale so no region-namespace is fetched (load: languageOnly)", async () => {
    await i18n.changeLanguage("de-DE");
    expect(i18n.languages).not.toContain("de-DE");
    expect(i18n.resolvedLanguage).toBe("de");
  });

  it("returns the localized string for the active language", async () => {
    await i18n.changeLanguage("de");
    expect(i18n.t("header.download")).toBe("Herunterladen");
    await i18n.changeLanguage("en");
    expect(i18n.t("header.download")).toBe("Download");
  });
});
