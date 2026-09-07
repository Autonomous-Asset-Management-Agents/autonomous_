// macOS release (desktop-mac-v*): the private edition card offers BOTH installers as
// two half-width pills side by side (operator pick 14.08., proposal 1) — no more
// single OS-detected button. Links come from the appVersion SSOT.
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { PricingCard } from "@/pages/PricingMaster";
import { getPricingData } from "@/lib/pricing-config";
import i18n from "@/lib/i18n";
import { WINDOWS_DOWNLOAD_URL, MAC_DOWNLOAD_URL } from "@/lib/appVersion";

describe("private edition download buttons (Windows + macOS)", () => {
  it("renders each platform pill exactly ONCE (top row only, operator 15.08.)", () => {
    const { container } = render(
      <PricingCard tier={getPricingData((k) => i18n.t(k)).private} mode="Junior" setMode={() => {}} />,
    );
    const win = screen.getAllByRole("link", { name: /windows/i });
    const mac = screen.getAllByRole("link", { name: /macos/i });
    expect(win.length).toBe(1);
    expect(mac.length).toBe(1);
    expect(win[0].getAttribute("href")).toBe(WINDOWS_DOWNLOAD_URL);
    expect(mac[0].getAttribute("href")).toBe(MAC_DOWNLOAD_URL);
    // the remaining row is visible on ALL breakpoints (no hidden sm:block) and
    // there is no tall mt-12 bottom row anymore
    expect(win[0].closest("div.hidden")).toBeNull();
    expect(container.querySelector("div.mt-12")).toBeNull();
    // slim design: py-6, not the tall py-8 variant
    expect(win[0].querySelector("button")?.className).toContain("py-6");
  });

  it("BETA sits in the private eyebrow, not inside the pills (operator 15.08.)", () => {
    const { container } = render(
      <PricingCard tier={getPricingData((k) => i18n.t(k)).private} mode="Junior" setMode={() => {}} />,
    );
    // no BETA inside any download link — it made the pills overflow on mobile
    for (const link of container.querySelectorAll("a")) {
      expect(link.textContent).not.toContain("BETA");
    }
    // exactly one green BETA, appended to the eyebrow line above the card name
    const eyebrow = container.querySelector(".ed-tier-eyebrow");
    expect(eyebrow?.textContent).toContain("BETA");
    const marker = screen.getByText("BETA");
    expect(marker.getAttribute("style") ?? "").toContain("rgb(0, 194, 122)");
  });
});
