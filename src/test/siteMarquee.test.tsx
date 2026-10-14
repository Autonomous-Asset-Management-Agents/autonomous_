import { describe, it, expect, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";

import { SiteMarquee } from "@/components/SiteMarquee";
import { WINDOWS_DOWNLOAD_URL, MAC_DOWNLOAD_URL } from "@/lib/appVersion";

function setUA(value: string) {
  Object.defineProperty(navigator, "userAgent", { value, configurable: true });
}
const ORIGINAL_UA = navigator.userAgent;
afterEach(() => setUA(ORIGINAL_UA));

describe("SiteMarquee (OSS promo ticker)", () => {
  it("Windows visitor → links to the Windows installer via the version SSOT", () => {
    setUA("Mozilla/5.0 (Windows NT 10.0; Win64; x64)");
    render(<SiteMarquee />);
    expect(screen.getAllByText(/download for windows/i).length).toBeGreaterThan(0);
    expect(screen.getByRole("link").getAttribute("href")).toBe(WINDOWS_DOWNLOAD_URL);
  });

  it("#2739/S10: macOS visitor → links to the Apple-Silicon .dmg", () => {
    setUA("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15");
    render(<SiteMarquee />);
    expect(screen.getAllByText(/download for mac/i).length).toBeGreaterThan(0);
    expect(screen.getByRole("link").getAttribute("href")).toBe(MAC_DOWNLOAD_URL);
  });

  it("applies the dark variant class only when `dark` is set", () => {
    const { container: light } = render(<SiteMarquee />);
    expect(light.querySelector(".site-marquee--dark")).toBeNull();
    const { container: dark } = render(<SiteMarquee dark />);
    expect(dark.querySelector(".site-marquee--dark")).not.toBeNull();
  });
});
