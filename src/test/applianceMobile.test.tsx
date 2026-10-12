// Mobile cleanup (operator 15.08.): below md the tower page must follow the
// main-page hero order (header, sub, text, image, CTAs side by side), the
// waitlist CTA uses the editions Contact-Us format, and the private edition
// download pills stack instead of overflowing the viewport.
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("@/lib/applianceApi", () => ({
  joinWaitlist: vi.fn().mockResolvedValue({ status: "pending" }),
  fetchPosition: vi.fn().mockResolvedValue({ position: 1, total: 1, referrals: 0, editionSize: 100 }),
}));
import Appliance from "@/pages/Appliance";
import { PricingCard } from "@/pages/PricingMaster";
import { getPricingData } from "@/lib/pricing-config";
import i18n from "@/lib/i18n";

describe("appliance page mobile layout", () => {
  it("hero CTAs exist twice: desktop row (hidden on mobile) and mobile pair below the image", () => {
    const { container } = render(<Appliance />);
    const desktopRow = container.querySelector("div.hidden.md\\:flex");
    expect(desktopRow?.textContent).toMatch(/join the waitlist/i);
    // mobile block: two equal pills side by side (main-page hero pattern)
    const mobilePair = container.querySelector("div.md\\:hidden div.grid.grid-cols-2");
    expect(mobilePair).toBeTruthy();
    expect(mobilePair!.querySelectorAll("button").length).toBe(2);
  });

  it("mobile CTA pair sits AFTER the hero image in DOM order", () => {
    const { container } = render(<Appliance />);
    const img = container.querySelector("img[src='/appliance-tower.webp']")!;
    const mobilePair = container.querySelector("div.md\\:hidden div.grid.grid-cols-2")!;
    expect(img.compareDocumentPosition(mobilePair) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("waitlist submit uses the slim editions CTA format (full width, 51px, green)", () => {
    render(<Appliance />);
    const submit = screen.getAllByRole("button", { name: /join the waitlist/i }).at(-1)!;
    expect(submit.className).toContain("w-full");
    expect(submit.className).toContain("h-[51px]"); // exact editions pill height (operator 15.08.)
    expect(submit.className).toContain("bg-[#00c27a]");
  });

  it("nav LIVE DEMO pill never wraps", () => {
    render(<Appliance />);
    const demo = screen.getByRole("link", { name: /view live demo/i });
    expect(demo.className).toContain("whitespace-nowrap");
  });
});

describe("private edition download pills mobile layout", () => {
  it("pill row stacks below sm instead of overflowing", () => {
    const { container } = render(
      <PricingCard tier={getPricingData((k) => i18n.t(k)).private} mode="Junior" setMode={() => {}} />,
    );
    const row = container.querySelector("a[aria-label='Download for Windows']")?.parentElement;
    expect(row?.className).toContain("flex-col");
    expect(row?.className).toContain("sm:flex-row");
  });
});
