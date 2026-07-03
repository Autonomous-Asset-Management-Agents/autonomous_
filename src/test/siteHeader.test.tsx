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

  it("renders the logo and the LinkedIn / GitHub / Login / Demo menu", () => {
    renderHeader();
    expect(screen.getByText("LOGIN")).toBeTruthy();
    expect(screen.getByText("DEMO")).toBeTruthy();
    expect(screen.getByRole("link", { name: /LinkedIn/i })).toBeTruthy();
    expect(screen.getByRole("link", { name: /GitHub/i })).toBeTruthy();
    // logo doubles as a home button linking back to the main page
    expect(screen.getByRole("button", { name: /startseite/i })).toBeTruthy();
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
});
