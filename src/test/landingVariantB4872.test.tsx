// #4872 (Epic #4450): variant B "transparency" of the hero_positioning test. The hero and the two
// new sections render only when the server assigns the variation; control stays unchanged.
import { describe, it, expect, vi, beforeAll, afterEach } from "vitest";
import { render, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import i18n from "@/lib/i18n";
import en from "@/locales/en/translation.json";
import de from "@/locales/de/translation.json";
import { FORBIDDEN } from "./wordingGuard";

vi.unmock("react-i18next");
vi.mock("firebase/auth", () => ({ signInWithPopup: vi.fn() }));
vi.mock("@/lib/firebase", () => ({ auth: {}, googleProvider: {} }));

const variant = vi.hoisted(() => ({ value: "control" }));
vi.mock("@/hooks/useExperiment", () => ({ useExperiment: () => variant.value }));

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

afterEach(() => {
  cleanup();
  variant.value = "control";
});

async function renderLanding(v: string) {
  variant.value = v;
  await i18n.changeLanguage("en");
  return render(
    <MemoryRouter>
      <LandingViewE />
    </MemoryRouter>,
  );
}

const strings = (o: unknown): string[] =>
  typeof o === "string" ? [o] : o && typeof o === "object" ? Object.values(o).flatMap(strings) : [];

describe("#4872 landing variant B (transparency)", () => {
  it("shows the transparency hero and both sections before 'Your rules.'", async () => {
    const { container } = await renderLanding("transparency");
    const h1 = container.querySelector("h1")?.textContent ?? "";
    expect(h1).toContain("AI suggests.");
    expect(h1).not.toContain("Your AI suggests.");
    expect(h1).toContain("Your rules decide.");
    expect(container.textContent).toContain("Quant agents check. Every pick. 24/7.");

    const quant = container.querySelector("#block-quant");
    const trail = container.querySelector("#block-trail");
    expect(quant?.textContent).toContain("14 agents.");
    expect(trail?.textContent).toContain("Full transparency.");

    const html = container.innerHTML;
    const rules = html.indexOf('id="block-profit"');
    expect(rules).toBeGreaterThan(-1);
    expect(html.indexOf('id="block-quant"')).toBeLessThan(rules);
    expect(html.indexOf('id="block-trail"')).toBeLessThan(rules);
  });

  it("lists the 14 agents by their role titles", async () => {
    const { container } = await renderLanding("transparency");
    const text = container.querySelector("#block-quant")?.textContent ?? "";
    for (const title of en.landing.quant.graphic.experts) expect(text).toContain(title);
    expect(en.landing.quant.graphic.experts).toHaveLength(14);
  });

  it.each(["control", "positioning"])("variation %s: no transparency sections, hero unchanged", async (v) => {
    const { container } = await renderLanding(v);
    expect(container.querySelector("#block-quant")).toBeNull();
    expect(container.querySelector("#block-trail")).toBeNull();
    expect(container.querySelector("h1")?.textContent ?? "").not.toContain("Your rules decide.");
  });

  it("variant copy passes the wording guard in en and de", () => {
    const pick = (c: typeof en | typeof de) => [
      c.landing.hero.exp.transparency,
      c.landing.quant,
      c.landing.trail,
    ];
    for (const s of [...strings(pick(en)), ...strings(pick(de))]) {
      expect(s.match(FORBIDDEN) ?? [], s).toEqual([]);
    }
  });
});
