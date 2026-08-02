import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { SiteMarquee } from "@/components/SiteMarquee";
import { WINDOWS_DOWNLOAD_URL } from "@/lib/appVersion";

describe("SiteMarquee (OSS promo ticker)", () => {
  it("renders the desktop-trader message linking to the Windows installer via the version SSOT", () => {
    render(<SiteMarquee />);
    expect(screen.getAllByText(/download for windows/i).length).toBeGreaterThan(0);
    const link = screen.getByRole("link");
    // Assert against the single source of truth (@/lib/appVersion), not a hardcoded release tag.
    expect(link.getAttribute("href")).toBe(WINDOWS_DOWNLOAD_URL);
  });

  it("applies the dark variant class only when `dark` is set", () => {
    const { container: light } = render(<SiteMarquee />);
    expect(light.querySelector(".site-marquee--dark")).toBeNull();
    const { container: dark } = render(<SiteMarquee dark />);
    expect(dark.querySelector(".site-marquee--dark")).not.toBeNull();
  });
});
