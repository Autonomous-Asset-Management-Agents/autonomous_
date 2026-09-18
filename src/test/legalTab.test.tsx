import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { LegalTab } from "../console/desktop/LegalTab";

/**
 * Settings → Legal tab: a bundled, offline index of every legal document, each linking to its
 * in-app `/legal/*` route (never an external host), plus the registered-entity footer.
 */
describe("Settings → Legal tab", () => {
  it("links every legal document to its in-app /legal/* route", () => {
    render(<LegalTab />);
    const hrefs = screen.getAllByRole("link").map((l) => l.getAttribute("href"));
    for (const kind of ["risk-disclosure", "terms", "inducements", "imprint", "notice", "privacy"]) {
      expect(hrefs).toContain(`/legal/${kind}`);
    }
    // in-app only — no external aaagents.de legal link
    expect(hrefs.every((h) => h?.startsWith("/legal/"))).toBe(true);
  });

  it("shows the ToS + inducements docs and the registered UG entity footer", () => {
    render(<LegalTab />);
    expect(screen.getByText("Terms of Service")).toBeTruthy();
    expect(screen.getByText(/Conflicts of Interest & Inducements/)).toBeTruthy();
    expect(screen.getByText(/Autonomous Asset Management Agents UG \(haftungsbeschränkt\)/)).toBeTruthy();
  });
});
