import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("@/lib/firebase", () => ({ auth: {}, googleProvider: {} }));
vi.mock("sonner", () => ({ toast: { error: vi.fn() } }));

import { SiteHeader } from "@/components/SiteHeader";

describe("SiteHeader (shared 1:1 nav)", () => {
  const renderHeader = () =>
    render(
      <MemoryRouter>
        <SiteHeader />
      </MemoryRouter>,
    );

  it("renders the logo and the LinkedIn / GitHub / Demo menu", () => {
    renderHeader();
    expect(screen.getByText("LIVE DEMO")).toBeTruthy();
    expect(screen.getByRole("link", { name: /LinkedIn/i })).toBeTruthy();
    expect(screen.getByRole("link", { name: /GitHub/i })).toBeTruthy();
    
    // Logo check
    const logoBtn = screen.getByRole("button", { name: /To homepage/i });
    expect(logoBtn).toBeTruthy();
  });

  it("applies the dark variant class only when `dark` is set", () => {
    const { container: light } = render(
      <MemoryRouter>
        <SiteHeader />
      </MemoryRouter>,
    );
    expect(light.querySelector(".site-header--dark")).toBeNull();

    const { container: dark } = render(
      <MemoryRouter>
        <SiteHeader dark />
      </MemoryRouter>,
    );
    expect(dark.querySelector(".site-header--dark")).not.toBeNull();
  });

  it("keeps the LIVE DEMO button and shows no Download pill by default (cta=demo)", () => {
    renderHeader();
    expect(screen.getByText("LIVE DEMO")).toBeTruthy();
    expect(
      screen.queryByRole("link", { name: /choose your edition/i }),
    ).toBeNull();
  });

  it("on the demo (cta=download) swaps LIVE DEMO for a white Download pill → the Editions section", () => {
    render(
      <MemoryRouter>
        <SiteHeader cta="download" />
      </MemoryRouter>,
    );
    // The demo page must NOT show a "Live Demo" self-link
    expect(screen.queryByText("LIVE DEMO")).toBeNull();
    const dl = screen.getByRole("link", { name: /choose your edition/i });
    expect(dl.textContent).toBe("Download");
    // Same link target as the Editions chooser on the landing (not the direct installer).
    expect(dl.getAttribute("href")).toBe("/preview#block-editions");
    // White pill / black text, mirroring the "Download for Windows" CTA.
    const style = dl.getAttribute("style") ?? "";
    expect(style).toMatch(/background:\s*(#fff|rgb\(255,\s*255,\s*255\)|white)/i);
    expect(style).toMatch(/color:\s*(#000|rgb\(0,\s*0,\s*0\)|black)/i);
  });
});
