// #2869 review (POLICY-01/02): guard the i18n setup — key parity across locales
// and region-locale resolution to a base language the LanguageSwitcher can match.
import { describe, expect, it } from "vitest";

import i18n from "../i18n";
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
});

describe("i18n configuration", () => {
  it("collapses a region locale so no region-namespace is fetched (load: languageOnly)", async () => {
    await i18n.changeLanguage("de-DE");
    // load:'languageOnly' + supportedLngs drop the de-DE variant from the resolution
    // chain, so i18next never tries to fetch a missing de-DE namespace.
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
