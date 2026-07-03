import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { SiteMarquee } from "@/components/SiteMarquee";

describe("SiteMarquee (OSS promo ticker)", () => {
  it("renders the OSS launch message linking to the GitHub releases", () => {
    render(<SiteMarquee />);
    expect(screen.getAllByText(/download from github/i).length).toBeGreaterThan(0);
    const link = screen.getByRole("link");
    expect(link.getAttribute("href")).toContain("/releases");
  });

  it("applies the dark variant class only when `dark` is set", () => {
    const { container: light } = render(<SiteMarquee />);
    expect(light.querySelector(".site-marquee--dark")).toBeNull();
    const { container: dark } = render(<SiteMarquee dark />);
    expect(dark.querySelector(".site-marquee--dark")).not.toBeNull();
  });
});
