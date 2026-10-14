// #4514 (Epic #4450): the landing renders without return promises, without the hidden
// subpage overlays, and with its fixed texts taken from the catalogue.
//
// Unlike the source-level landing tests (heroTowerFlag, applianceRoute) this one mounts
// LandingViewE: the claims are about what a visitor, a crawler or an LLM actually reads in
// the DOM. Browser APIs jsdom lacks (IntersectionObserver, ResizeObserver) are stubbed;
// firebase is mocked so the landing mounts without a project config. The global
// react-i18next mock from setup.ts is lifted: interpolation and the "cimode" check need the
// real i18n runtime.
import { describe, it, expect, vi, beforeAll, afterEach } from "vitest";
import { render, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import i18n from "@/lib/i18n";
import { FORBIDDEN } from "./wordingGuard";

vi.unmock("react-i18next");
vi.mock("firebase/auth", () => ({ signInWithPopup: vi.fn() }));
vi.mock("@/lib/firebase", () => ({ auth: {}, googleProvider: {} }));

import LandingViewE from "@/components/views/LandingViewE";

class NoopObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return [];
  }
}

beforeAll(() => {
  vi.stubGlobal("IntersectionObserver", NoopObserver);
  vi.stubGlobal("ResizeObserver", NoopObserver);
});

afterEach(async () => {
  cleanup();
  await i18n.changeLanguage("en");
});

async function renderLanding(lng = "en") {
  await i18n.changeLanguage(lng);
  return render(
    <MemoryRouter>
      <LandingViewE />
    </MemoryRouter>,
  );
}

const attrTexts = (root: HTMLElement) =>
  [...root.querySelectorAll("[title], [aria-label]")].flatMap((el) =>
    ["title", "aria-label"].map((a) => el.getAttribute(a) ?? "").filter(Boolean),
  );

describe("#4514 landing: rendered DOM", () => {
  it("keine_renditezahlen", async () => {
    const { container } = await renderLanding();
    // The example panel's "Daily vol target 1.5 %" is a risk setting, not a return; it is
    // checked on its own in beispiel_panel.
    const clone = container.cloneNode(true) as HTMLElement;
    clone.querySelectorAll(".pex").forEach((el) => el.remove());
    const text = clone.textContent ?? "";
    expect(text.match(/[+−-]?\d+[.,]\d+\s?%/g) ?? []).toEqual([]);
    for (const s of ["P / L", "Sharpe", "alpha vs", "Since inception"]) {
      expect(text, s).not.toContain(s);
    }
  });

  it("nur_14_und_8_agenten", async () => {
    const { container } = await renderLanding();
    const text = [container.textContent ?? "", ...attrTexts(container)].join(" ");
    const counts = [...text.matchAll(/(\d+)\s+(agents|voting|classifiers|Senators)/gi)].map((m) => m[1]);
    expect(counts.length).toBeGreaterThan(0);
    expect(counts.filter((n) => n !== "14" && n !== "8")).toEqual([]);
  });

  it("wording_guard_dom", async () => {
    const { container } = await renderLanding();
    expect((container.textContent ?? "").match(FORBIDDEN) ?? []).toEqual([]);
    expect(attrTexts(container).join(" | ").match(FORBIDDEN) ?? []).toEqual([]);
  });

  it("keine_overlays", async () => {
    const { container } = await renderLanding();
    for (const id of ["overlayProfit", "overlayAuto", "overlaySafe"]) {
      expect(container.querySelector(`#${id}`), id).toBeNull();
    }
    expect(container.querySelector("[data-overlay]")).toBeNull();
    expect(container.querySelector("[data-open], [data-close]")).toBeNull();
  });

  it("risikokarte_ohne_adr_kuerzel", async () => {
    // Plan #4514: the risk pipeline card carries no ADR codes, and no limit figure the
    // landing cannot back up (review on PR #4577).
    const { container } = await renderLanding();
    const card = container.querySelector(".rpipe") as HTMLElement;
    expect(card).not.toBeNull();
    const text = [card.textContent ?? "", ...attrTexts(card)].join(" | ");
    expect(text).not.toMatch(/ADR-|R0\d/);
    expect(text).not.toMatch(/\d+[.,]?\d*\s?%/);
  });

  it("kein_gcp", async () => {
    const { container } = await renderLanding();
    // The approved Developer-edition copy ("AWS, GCP or bare metal", #4490) names a place
    // the open-source stack can run, not a sink the landing claims; the pricing cards are
    // therefore outside this check. Everything else must not mention GCP.
    const clone = container.cloneNode(true) as HTMLElement;
    clone.querySelectorAll(".ed-tiers").forEach((el) => el.remove());
    expect(clone.textContent).not.toMatch(/GCP/);
    expect(attrTexts(clone).join(" ")).not.toMatch(/GCP/);
  });

  it("beispiel_panel", async () => {
    const { container } = await renderLanding();
    const block = container.querySelector("#block-profit");
    const text = block?.textContent ?? "";
    expect(text).toContain("Your rules · Example");
    expect(text).toContain("Agent weights · Example");
    for (const [label, value] of [
      ["Maximum positions", "10"],
      ["Volatility sizing", "on"],
      ["Daily vol target", "1.5 %"],
      ["Minimum holding period", "20 days"],
      ["DrawdownGuard", "0.60"],
      ["Regime", "0.50"],
      ["Momentum", "0.45"],
      ["VIX", "0.45"],
      ["Fundamentals", "0.25"],
      ["News", "0.20"],
      ["Specialist", "0.15"],
      ["LSTM", "0.15"],
    ]) {
      const row = [...(block?.querySelectorAll(".pex-row") ?? [])].find(
        (r) => r.querySelector(".pex-k")?.textContent === label,
      );
      expect(row?.querySelector(".pex-v")?.textContent, label).toBe(value);
    }
  });

  it("code_link_gleiche_url", async () => {
    const { container } = await renderLanding();
    const code = [...container.querySelectorAll<HTMLAnchorElement>(".lb-hero a")].find(
      (a) => a.textContent === "View the code",
    );
    const header = container.querySelector<HTMLAnchorElement>('nav.lb-nav a[aria-label="View on GitHub"]');
    expect(code, "hero code link").toBeTruthy();
    expect(header, "header GitHub icon").toBeTruthy();
    expect(code!.getAttribute("href")).toBe(header!.getAttribute("href"));
    expect(code!.getAttribute("data-cta")).toBe("github_code");
    expect(code!.getAttribute("target")).toBe("_blank");
    expect(code!.getAttribute("rel")).toContain("noopener");
  });

  it("cimode_feste_texte", async () => {
    const { container } = await renderLanding("cimode");
    const text = container.textContent ?? "";
    const attrs = attrTexts(container);
    for (const literal of [
      "autonomous_ tower — limited first edition",
      "LIVE DEMO",
      "View the code",
      "Voting ensemble",
      "Price on request",
      "Your rules · Example",
    ]) {
      expect(text, literal).not.toContain(literal);
    }
    for (const literal of [
      "Follow us on LinkedIn",
      "View on GitHub",
      "Live-Demo ansehen",
      "View live demo",
      "Download — choose your edition",
      "autonomous_ tower — new hardware, First Edition",
      "autonomous_ tower — limited first edition",
    ]) {
      expect(attrs, literal).not.toContain(literal);
    }
    expect(text).toContain("landing.ticker");
    expect(text).toContain("landing.nav.demo");
    expect(text).toContain("landing.hero.ctaCode");
    expect(text).toContain("landing.safe.pipeline.votingTag");
    expect(text).toContain("landing.profit.example.rulesTitle");
  });
});
