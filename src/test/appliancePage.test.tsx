// Appliance product page (plan Task 3) — the approved structure & copy spine (spec §7a),
// the §9 form state machine, and the legal lines. Copy is byte-pinned deliberately: the
// Structure & Copy artifact was operator-approved on 14.08.
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

vi.mock("@/lib/applianceApi", () => ({
  joinWaitlist: vi.fn().mockResolvedValue({ status: "pending" }),
  fetchPosition: vi.fn().mockResolvedValue({ position: 42, total: 187, referrals: 3, editionSize: 100 }),
}));
import Appliance from "@/pages/Appliance";

describe("Appliance page (spec §7a)", () => {
  it("renders the approved copy spine: H1, price, edition, CTA, section heads", () => {
    render(<Appliance />);
    // \n in the catalog renders as the landing-style two-line headline (pre-line)
    expect(screen.getByRole("heading", { level: 1 }).textContent?.replace(/\s+/g, " ")).toBe("Autonomous tower");
    // net price (operator 17.08.): 799.00 € + VAT, unified format across pages
    expect(screen.getAllByText(/799\.00 €/).length).toBeGreaterThanOrEqual(2); // hero + spec sheet
    expect(screen.getAllByText(/100 units/i).length).toBeGreaterThan(0);
    expect(screen.getByText("Always on. Every day, every hour, every moment.")).toBeTruthy();
    expect(screen.getByText("Running in two minutes.")).toBeTruthy();
    expect(screen.getByText("The sheet.")).toBeTruthy();
    expect(screen.getByText("Reserve your unit.")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: /join the waitlist/i }).length).toBeGreaterThan(0);
  });

  it("no-purchase + risk lines are present (legal spine)", () => {
    render(<Appliance />);
    // micro line renders twice: desktop text column + mobile block below the image
    expect(screen.getAllByText(/no payment now/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/free and not a purchase/i)).toBeTruthy();
    expect(screen.getByText(/execution-only tool/i)).toBeTruthy();
  });

  it("join flow: submit -> inbox hint (double opt-in), never a fake position before confirm", async () => {
    render(<Appliance />);
    fireEvent.change(screen.getByLabelText("waitlist-email"), { target: { value: "a@b.de" } });
    // the submit button is the LAST join CTA in the DOM (hero now renders the
    // CTA twice: desktop text-column row + mobile pair below the image)
    fireEvent.click(screen.getAllByRole("button", { name: /join the waitlist/i }).at(-1)!);
    await waitFor(() => expect(screen.getByText(/check your inbox/i)).toBeTruthy());
    expect(screen.queryByText(/you are #/i)).toBeNull();
  });

  it("confirmed state (initialCode): renders the terminal counter from fetchPosition", async () => {
    render(<Appliance initialCode="DEMO42" />);
    await waitFor(() => expect(screen.getByText(/you are #042 of 100/i)).toBeTruthy());
    expect(screen.getByText(/ref=DEMO42/)).toBeTruthy();
  });
});
