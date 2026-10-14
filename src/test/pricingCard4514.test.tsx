// #4514 (Epic #4450): the edition cards take their fixed texts from the catalogue and
// use the one contact address of the landing. The global react-i18next mock from setup.ts
// is lifted so "cimode" reaches the component.
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import i18n from "@/lib/i18n";
import { PricingCard } from "@/pages/PricingMaster";
import { getPricingData } from "@/lib/pricing-config";

vi.unmock("react-i18next");

afterEach(async () => {
  cleanup();
  await i18n.changeLanguage("en");
});

const pricing = () => getPricingData((k) => i18n.t(k));

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
});
