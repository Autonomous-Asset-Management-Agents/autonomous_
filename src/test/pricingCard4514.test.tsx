// #4514 (Epic #4450): the edition cards take their fixed texts from the catalogue and
// use the one contact address of the landing. The global react-i18next mock from setup.ts
// is lifted so "cimode" reaches the component.
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import i18n from "@/lib/i18n";
import translationDE from "@/locales/de/translation.json";
import { PricingCard } from "@/pages/PricingMaster";
import { getPricingData } from "@/lib/pricing-config";

vi.unmock("react-i18next");

afterEach(async () => {
  cleanup();
  await i18n.changeLanguage("en");
});

const pricing = () => getPricingData((k) => i18n.t(k));
const deLookup = (key: string): string => {
  const val = key
    .split(".")
    .reduce<unknown>((o, p) => (o && typeof o === "object" ? (o as Record<string, unknown>)[p] : undefined), translationDE);
  return typeof val === "string" ? val : key;
};

describe("#4514 PricingCard", () => {
  it("cimode: fixed texts appear as keys", async () => {
    await i18n.changeLanguage("cimode");
    const { container } = render(
      <>
        <PricingCard tier={pricing().private} mode="Junior" setMode={() => {}} />
        <PricingCard tier={pricing().professional} isProfessional />
        <PricingCard tier={pricing().institutional} isInstitutional />
      </>,
    );
    const text = container.textContent ?? "";
    for (const literal of ["BETA", "Junior", "Senior", "Contact Us", "Download on GitHub", "Windows", "macOS"]) {
      expect(text, literal).not.toContain(literal);
    }
    const labels = [...container.querySelectorAll("[aria-label]")].map((el) => el.getAttribute("aria-label"));
    expect(labels).not.toContain("Download for Windows");
    expect(labels).not.toContain("Download for macOS");
    expect(text).toContain("landing.pricingCard.beta");
    expect(text).toContain("landing.pricingCard.contact");
  });

  it("contact falls back to info@aaagents.de", () => {
    const original = window.location;
    const loc = { href: "" } as Location;
    Object.defineProperty(window, "location", { value: loc, configurable: true });
    try {
      render(<PricingCard tier={pricing().institutional} isInstitutional />);
      fireEvent.click(screen.getByRole("button", { name: "Contact Us" }));
      expect(loc.href).toBe("mailto:info@aaagents.de");
    } finally {
      Object.defineProperty(window, "location", { value: original, configurable: true });
    }
  });

  it("Senior pricing reflects KR2/KR5 benchmark (4.99 €/mo or 39.00 €/yr, 14-day free trial) without free-until-2026", async () => {
    await i18n.changeLanguage("en");
    const enSenior = pricing().private.Senior;
    expect(enSenior.price).toBe("4.99 €");
    expect(enSenior.priceSub).toBe("/ month incl. VAT");
    expect(enSenior.priceMeta).toBe("or 39.00 € / year · 14-day free trial");
    const enFoot = i18n.t("landing.editions.foot");
    expect(enFoot).toContain("4.99 €");
    expect(enFoot).toContain("39.00 €");
    expect(enFoot).toContain("14-day free trial");
    expect(enFoot).not.toMatch(/31 Dec|31\.12\.2026/i);

    const deSenior = getPricingData(deLookup).private.Senior;
    expect(deSenior.price).toBe("4,99 €");
    expect(deSenior.priceSub).toBe("/ Monat inkl. MwSt.");
    expect(deSenior.priceMeta).toBe("oder 39,00 € / Jahr · 14 Tage kostenlos testen");
    const deFoot = deLookup("landing.editions.foot");
    expect(deFoot).toContain("4,99 €");
    expect(deFoot).toContain("39,00 €");
    expect(deFoot).toContain("14 Tage kostenlos testen");
    expect(deFoot).not.toMatch(/31\. Dezember 2026|31\.12\.2026/i);
  });
});
