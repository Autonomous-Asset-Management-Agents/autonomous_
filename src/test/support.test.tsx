// GTM-1 T1 (#1464): the /support page renders the canonical FAQ (docs/oss/FAQ.md) plus the
// Get-Help channels and the "technical support, not investment advice" boundary.
import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { MemoryRouter } from "react-router-dom";
import Support from "../pages/Support";

const renderPage = () =>
  render(
    <MemoryRouter>
      <Support />
    </MemoryRouter>,
  );

describe("Support page (/support) — GTM-1 T1", () => {
  it("renders the canonical FAQ content (Alpaca + app setup, from docs/oss/FAQ.md)", () => {
    renderPage();
    expect(screen.getAllByText(/What is Alpaca/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/paper trading by default/i).length).toBeGreaterThan(0);
  });

  it("offers the Get-Help channels (GitHub Discussions / Issues / email)", () => {
    renderPage();
    expect(screen.getAllByText(/Discussions/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Issues/i).length).toBeGreaterThan(0);
    // an actual email contact is present (mailto link or text)
    expect(screen.getAllByText(/aaagents\.de/i).length).toBeGreaterThan(0);
  });

  it("states the technical-support-not-investment-advice boundary", () => {
    renderPage();
    expect(screen.getAllByText(/not.*investment advice/i).length).toBeGreaterThan(0);
  });

  it("links back home (mirrors the Legal page chrome)", () => {
    renderPage();
    expect(screen.getByText(/back to home/i)).toBeTruthy();
  });
});
