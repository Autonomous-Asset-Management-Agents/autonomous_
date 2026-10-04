import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";

/**
 * #3321 / C1 — the public live-demo must lead with the PORTFOLIO (shared Balances/KPI bands +
 * positions), not the Round Table, and render the same bands as the console dashboard. This pins
 * the positions-first order and the compact Round Table teaser.
 */
const snapshot = {
  generated_at: "2026-09-13T12:00:00Z",
  status: "live",
  disclaimer: "Paper-trading demo — fictitious.",
  equity: 144361,
  cash: 43245,
  day_pl_pct: 1.1,
  positions: [
    { symbol: "AMT", qty: 57, market_value: 10162, unrealized_pl_pct: 1.65 },
    { symbol: "FDX", qty: 31, market_value: 9819, unrealized_pl_pct: -1.7 },
  ],
  decisions: Array.from({ length: 7 }, (_, i) => ({
    symbol: `SYM${i}`,
    action: i % 2 ? "hold" : "buy",
    consensus: 0.5 + i * 0.03,
    conviction: 0.5,
    summary: "reasoning",
    execution_outcome: null,
  })),
  report: {
    symbol: "AAPL",
    summary: "steady cashflow",
    sentiment: "positiv",
    as_of: "2026-09-13",
  },
  equity_curve: [
    { date: "2026-02-20", equity: 100000, benchmark: 100000 },
    { date: "2026-06-01", equity: 120000, benchmark: 110000 },
    { date: "2026-09-13", equity: 144361, benchmark: 112000 },
  ],
};

vi.mock("@/console/live/useSnapshotPolling", () => ({
  useSnapshotPolling: () => ({
    snapshot,
    paused: false,
    error: null,
    stale: false,
  }),
}));
vi.mock("@/components/SiteHeader", () => ({ SiteHeader: () => null }));
vi.mock("@/components/SiteMarquee", () => ({ SiteMarquee: () => null }));

import { LiveDemo } from "../pages/LiveDemo";

describe("LiveDemo — positions-first parity (#3321)", () => {
  it("leads with the portfolio: shared Balances + KPI bands and the positions table above the Round Table", () => {
    const { container } = render(<LiveDemo />);
    const text = container.textContent ?? "";

    // Shared bands render (same surface as the console Overview).
    expect(text).toContain("Cash / buying power"); // BalancesBand
    expect(text).toContain("autonomous_"); // KpiBand brand card
    expect(text).toContain("S&P 500");
    expect(text).toContain("Max drawdown");

    // Positions lead; the Round Table is a compact teaser BELOW them.
    const posIdx = text.indexOf("Top positions");
    const rtIdx = text.indexOf("Round Table · latest calls");
    expect(posIdx).toBeGreaterThanOrEqual(0);
    expect(rtIdx).toBeGreaterThanOrEqual(0);
    expect(posIdx).toBeLessThan(rtIdx);

    // A held name renders in the positions table.
    expect(text).toContain("AMT");
  });

  it("shows a compact teaser (top 5 of N), not the full decisions list", () => {
    const { container } = render(<LiveDemo />);
    const text = container.textContent ?? "";
    expect(text).toContain("Showing the top 5 of 7 calls");
    // The hero no longer leads with the decisions count.
    expect(text).not.toContain("The Round Table made");
    expect(text).toContain("Autonomous paper portfolio");
  });
});
